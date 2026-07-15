import json
import os

from anthropic import AsyncAnthropic
from anthropic.types import ThinkingDelta, TextDelta
from openai import AsyncOpenAI
from dataclasses import dataclass, field
from typing import Optional, Callable

"""
Provider support: "anthropic" (default) or "openai".

Anthropic: uses AsyncAnthropic client, messages endpoint.
OpenAI: uses AsyncOpenAI client, chat/completions endpoint (compatible with
  any OpenAI-format API, including DeepSeek, local proxies, etc.).

Tool schemas are stored internally in Anthropic format and converted
on-the-fly when using the OpenAI provider.

claude-opus  → deepseek-v4-pro
claude-haiku / claude-sonnet → deepseek-v4-flash
"""


@dataclass
class ClientConfig:
    model: str = field(default_factory=lambda: os.environ.get("MODEL", "claude-opus-4-8"))
    api_key: str = field(default_factory=lambda: os.environ.get("API_KEY", None))
    base_url: str = field(default_factory=lambda: os.environ.get("BASE_URL", None))
    system_prompt: str = "You are a helpful assistant"
    max_token: int = 16384
    max_retrise: int = 3
    timeout: float = 120.00
    stream: bool = True
    """Provider: "anthropic" (default) or "openai" """
    provider: str = "anthropic"
    """anthropic: thinking={"type": "enabled", "budget_tokens": 10000}
       openai: extra_body={"thinking": {"type": "enabled"}}"""
    thinking: dict = field(default_factory=lambda: {"type": "adaptive"})
    tool_choice: dict = field(default_factory=lambda: {"type": "auto"})
    default_headers: dict = field(default_factory=dict)

# ---------------------------------------------------------------------------
# Normalized response wrapper — so core.py sees the same shape regardless of
# whether we used Anthropic or OpenAI under the hood.
# ---------------------------------------------------------------------------

class ContentBlock(dict):
    """Mimics an Anthropic content block so core.py can iterate .content.

    Inherits from dict so it's JSON-serializable (required by Anthropic SDK
    for conversation history), while supporting attribute-style access
    (block.type, block.text, block.id, block.name, block.input).
    """
    def __init__(self, type: str, text: str = "", id: str = "", name: str = "", input: dict | None = None):
        super().__init__(type=type, text=text, id=id, name=name, input=input if input is not None else {})

    def __getattr__(self, key: str):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(f"'ContentBlock' has no attribute '{key}'") from None


@dataclass
class NormalizedResponse:
    """Thin wrapper that exposes .content (list of ContentBlock) and .usage."""
    content: list  # list[ContentBlock]
    usage: Optional[object] = None
    stop_reason: str = ""


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

@dataclass
class Usage:
    """Tracks cumulative token usage across calls.

    deepseek 缓存命中规则:
    1. A+B → A+B+C hits A+B cache prefix.
    2. A+B → A+C misses,但系统记录公共前缀 A 落盘;
       下一轮 A+D → hits A.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def update(self, usage_obj) -> None:
        if usage_obj is None:
            return
        self.input_tokens += getattr(usage_obj, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage_obj, "output_tokens", 0) or 0
        # OpenAI-style usage may have prompt_tokens_details.cached_tokens
        details = getattr(usage_obj, "prompt_tokens_details", None)
        if details:
            self.prompt_cache_hit_tokens += getattr(details, "cached_tokens", 0) or 0
        # Anthropic-style
        self.cache_creation_input_tokens += getattr(usage_obj, "cache_creation_input_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(usage_obj, "cache_read_input_tokens", 0) or 0

    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# ---------------------------------------------------------------------------
# LLM client — dual-provider (Anthropic + OpenAI)
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(self, config: ClientConfig):
        self.cfg = config
        self.usage = Usage()
        self.provider = config.provider.lower()

        # Normalize base_url per provider:
        # - OpenAI SDK doesn't prefix with /v1, so we need it in the base URL.
        # - Anthropic SDK auto-adds /v1/messages, so strip /v1 if present.
        base_url = config.base_url or ""
        if self.provider == "openai":
            if not base_url.rstrip("/").endswith("/v1"):
                base_url = base_url.rstrip("/") + "/v1"
        else:
            base_url = base_url.rstrip("/").removesuffix("/v1")

        kwargs: dict = {
            "api_key": config.api_key,
            "base_url": base_url,
            "max_retries": config.max_retrise,
            "timeout": config.timeout,
            "default_headers": config.default_headers,
        }

        if self.provider == "openai":
            self._client = AsyncOpenAI(**kwargs)
        else:
            self._client = AsyncAnthropic(**kwargs)

    # ------------------------------------------------------------------
    # Extra params (thinking / reasoning)
    # ------------------------------------------------------------------

    def _extra_params(self) -> dict:
        if self.provider == "openai":
            params: dict = {}
            if self.cfg.thinking:
                params["extra_body"] = {"thinking": {"type": "enabled"}}
            return params
        else:
            params: dict = {}
            if self.cfg.thinking:
                params["thinking"] = {"type": "adaptive"}
            else:
                params["thinking"] = {"type": "disabled"}
            return params

    # ------------------------------------------------------------------
    # Tool schema conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_tools_for_openai(tools: list[dict]) -> list[dict]:
        """Convert Anthropic-style tool schemas → OpenAI function-calling format."""
        result = []
        for tool in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        return result

    # ------------------------------------------------------------------
    # Message conversion (Anthropic internal format → OpenAI format)
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_messages_for_openai(messages: list[dict]) -> list[dict]:
        """Convert from Anthropic-style message list to OpenAI-style.

        Anthropic differences handled:
        - Assistant content is a list of blocks (text + tool_use).
        - Tool results are user-role with list of {tool_use_id, type, content}.
        """
        converted: list[dict] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "assistant" and isinstance(content, list):
                # Build text + tool_calls from content blocks
                text_parts: list[str] = []
                tool_calls: list[dict] = []
                for block in content:
                    btype = getattr(block, "type", None) or block.get("type", "")
                    if btype == "text":
                        txt = getattr(block, "text", "") or block.get("text", "")
                        if txt:
                            text_parts.append(txt)
                    elif btype == "tool_use":
                        tid = getattr(block, "id", "") or block.get("id", "")
                        tname = getattr(block, "name", "") or block.get("name", "")
                        tinp = getattr(block, "input", {}) or block.get("input", {})
                        tool_calls.append({
                            "id": tid,
                            "type": "function",
                            "function": {
                                "name": tname,
                                "arguments": json.dumps(tinp, ensure_ascii=False),
                            },
                        })
                converted_msg: dict = {"role": "assistant"}
                if text_parts:
                    converted_msg["content"] = "\n".join(text_parts)
                else:
                    converted_msg["content"] = None
                if tool_calls:
                    converted_msg["tool_calls"] = tool_calls
                converted.append(converted_msg)

            elif role == "user" and isinstance(content, list):
                # Could be tool results
                tool_msgs: list[dict] = []
                text_parts: list[str] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        tool_msgs.append({
                            "role": "tool",
                            "tool_call_id": item.get("tool_use_id", ""),
                            "content": item.get("content", ""),
                        })
                    elif isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        text_parts.append(item)
                if text_parts:
                    converted.append({"role": "user", "content": "\n".join(text_parts)})
                converted.extend(tool_msgs)

            else:
                converted.append({"role": role, "content": str(content) if content else ""})

        return converted

    # ------------------------------------------------------------------
    # Streaming — Anthropic
    # ------------------------------------------------------------------

    async def _stream_anthropic(
        self,
        kwargs: dict,
        on_text: Callable[[str], None],
        on_thinking: Callable[[str], None],
    ) -> NormalizedResponse:
        current_block_type: str | None = None
        blocks: list[ContentBlock] = []
        current_text: list[str] = []

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                etype = getattr(event, "type", None)
                if etype == "content_block_start":
                    # Flush previous text block if any
                    if current_text and current_block_type == "text":
                        blocks.append(ContentBlock(type="text", text="".join(current_text)))
                        current_text.clear()
                    current_block_type = event.content_block.type
                    if current_block_type == "thinking" and on_thinking:
                        on_thinking("<think>\n")
                elif etype == "content_block_delta":
                    delta = event.delta
                    if isinstance(delta, TextDelta):
                        on_text(delta.text)
                        current_text.append(delta.text)
                    elif isinstance(delta, ThinkingDelta):
                        on_thinking(delta.thinking)
                elif etype == "content_block_stop":
                    if current_block_type == "thinking" and on_thinking:
                        on_thinking("</think>\n")
                    current_block_type = None

            msg = await stream.get_final_message()

        # Build blocks from final message content
        final_blocks: list[ContentBlock] = []
        for block in msg.content:
            btype = getattr(block, "type", "text")
            if btype == "tool_use":
                final_blocks.append(ContentBlock(
                    type="tool_use",
                    id=getattr(block, "id", ""),
                    name=getattr(block, "name", ""),
                    input=getattr(block, "input", {}),
                ))
            elif btype == "text":
                final_blocks.append(ContentBlock(type="text", text=getattr(block, "text", "")))
            elif btype == "thinking":
                pass  # skip thinking blocks in final content

        self.usage.update(msg.usage)
        return NormalizedResponse(
            content=final_blocks,
            usage=msg.usage,
            stop_reason=getattr(msg, "stop_reason", ""),
        )

    # ------------------------------------------------------------------
    # Streaming — OpenAI
    # ------------------------------------------------------------------

    async def _stream_openai(
        self,
        kwargs: dict,
        on_text: Callable[[str], None],
        on_thinking: Callable[[str], None],
    ) -> NormalizedResponse:
        kwargs["stream"] = True
        # Accumulators
        text_buf: list[str] = []
        tool_calls_acc: dict[int, dict] = {}  # index → {id, name, args_str}
        thinking_active = False

        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # Reasoning / thinking (DeepSeek-style)
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                if not thinking_active:
                    thinking_active = True
                    if on_thinking:
                        on_thinking("<think>\n")
                if on_thinking:
                    on_thinking(reasoning)
                continue

            if thinking_active:
                thinking_active = False
                if on_thinking:
                    on_thinking("</think>\n")

            # Text content
            if delta.content:
                on_text(delta.content)
                text_buf.append(delta.content)

            # Tool calls (accumulated across chunks)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": "", "name": "", "args_str": ""}
                    entry = tool_calls_acc[idx]
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["name"] = tc.function.name
                        if tc.function.arguments:
                            entry["args_str"] += tc.function.arguments

        # Build normalized response blocks
        final_blocks: list[ContentBlock] = []

        if text_buf:
            final_blocks.append(ContentBlock(type="text", text="".join(text_buf)))

        for entry in tool_calls_acc.values():
            try:
                parsed_input = json.loads(entry["args_str"]) if entry["args_str"] else {}
            except json.JSONDecodeError:
                parsed_input = {}
            final_blocks.append(ContentBlock(
                type="tool_use",
                id=entry["id"],
                name=entry["name"],
                input=parsed_input,
            ))

        # Usage from the last chunk (OpenAI sends usage in final chunk)
        last_usage = getattr(chunk, "usage", None) if 'chunk' in dir() else None
        self.usage.update(last_usage)

        return NormalizedResponse(
            content=final_blocks,
            usage=last_usage,
            stop_reason="",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_text: Callable[[str], None] | None = None,
        on_thinking: Callable[[str], None] | None = None,
    ) -> NormalizedResponse:
        on_text = on_text or (lambda t: print(t, end="", flush=True))
        on_thinking = on_thinking or (lambda t: print(t, end="", flush=True))

        if self.provider == "openai":
            # Convert messages to OpenAI format
            openai_messages: list[dict] = []
            if self.cfg.system_prompt:
                openai_messages.append({"role": "system", "content": self.cfg.system_prompt})
            openai_messages.extend(self._convert_messages_for_openai(messages))

            kwargs = {
                "model": self.cfg.model,
                "messages": openai_messages,
                "max_tokens": self.cfg.max_token,
                **self._extra_params(),
            }

            if tools:
                kwargs["tools"] = self._convert_tools_for_openai(tools)

            if self.cfg.stream:
                return await self._stream_openai(kwargs, on_text, on_thinking)
            else:
                # Non-streaming OpenAI
                response = await self._client.chat.completions.create(**kwargs)
                choice = response.choices[0]
                final_blocks: list[ContentBlock] = []
                if choice.message.content:
                    final_blocks.append(ContentBlock(type="text", text=choice.message.content))
                if choice.message.tool_calls:
                    for tc in choice.message.tool_calls:
                        try:
                            parsed = json.loads(tc.function.arguments)
                        except (json.JSONDecodeError, TypeError):
                            parsed = {}
                        final_blocks.append(ContentBlock(
                            type="tool_use",
                            id=tc.id,
                            name=tc.function.name,
                            input=parsed,
                        ))
                self.usage.update(response.usage)
                return NormalizedResponse(content=final_blocks, usage=response.usage)
        else:
            # --- Anthropic path ---
            tools_param = tools if tools else None

            kwargs = {
                "model": self.cfg.model,
                "messages": messages,
                "max_tokens": self.cfg.max_token,
                "system": self.cfg.system_prompt,
                **self._extra_params(),
            }
            if tools_param:
                kwargs["tools"] = tools_param

            if self.cfg.stream:
                return await self._stream_anthropic(kwargs, on_text, on_thinking)
            else:
                async with self._client.messages.stream(**kwargs) as stream:
                    msg = await stream.get_final_message()
                self.usage.update(msg.usage)
                final_blocks: list[ContentBlock] = []
                for block in msg.content:
                    btype = getattr(block, "type", "text")
                    if btype == "tool_use":
                        final_blocks.append(ContentBlock(
                            type="tool_use",
                            id=getattr(block, "id", ""),
                            name=getattr(block, "name", ""),
                            input=getattr(block, "input", {}),
                        ))
                    elif btype == "text":
                        final_blocks.append(ContentBlock(type="text", text=getattr(block, "text", "")))
                return NormalizedResponse(content=final_blocks, usage=msg.usage)


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    async def _test():
        # Test OpenAI provider
        config = ClientConfig(
            model="deepseek-v4-flash",
            api_key="sgw_zey3d2zcvek2wa4menvlkmlc",
            base_url="http://127.0.0.1:3000/anthropic",
            provider="anthropic",
            thinking=True,
        )
        client = LLMClient(config)
        resp = await client.create(
            messages=[{"role": "user", "content": "hello"}],
        )
        print("\n--- blocks ---")
        for b in resp.content:
            print(f"  [{b.type}] text={b.text!r} name={b.name!r} input={b.input!r}")

    asyncio.run(_test())
    