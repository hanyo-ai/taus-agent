import asyncio
import select
import sys
import termios
import threading
import tty

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style

from src.agent.core import Agent
from src.agent.persistence import AgentPersistence
from src.agent.signals import AgentBrake, AgentAborted, AgentPaused
from src.utils.completer import SlashCompleter


REPL_STYLE = Style.from_dict({
    "completion-menu.completion": "bg:#1e1e1e fg:#888888",
    "completion-menu.completion.current": "bg:#005f87 fg:#ffffff bold",
    "completion-menu.meta.completion": "bg:#1e1e1e fg:#5f8787",
    "completion-menu.meta.completion.current": "bg:#005f87 fg:#dddddd",
    "scrollbar.background": "bg:#1e1e1e",
    "scrollbar.button": "bg:#444444",
})

repl_brake = AgentBrake("repl")


# ── Background stdin watcher ─────────────────────────────────────────────────
# When agent.run() is executing, prompt_toolkit is no longer listening for
# keystrokes.  A background thread monitors stdin in raw mode so ESC / Ctrl+C
# still work mid-flight.

class StdinEscWatcher:
    """Background thread that watches stdin for ESC / Ctrl+C while the agent
    is running and prompt_toolkit is not actively waiting for input."""

    def __init__(self, brake: AgentBrake):
        self.brake = brake
        self._active = False
        self._thread: threading.Thread | None = None
        self._old_settings: list | None = None

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._active = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._restore_terminal()

    # ------------------------------------------------------------------
    def _watch(self) -> None:
        fd = sys.stdin.fileno()
        try:
            self._old_settings = termios.tcgetattr(fd)
        except (termios.error, IOError):
            return  # non-tty stdin, bail out

        try:
            tty.setcbreak(fd)
            while self._active:
                ready, _, _ = select.select([sys.stdin], [], [], 0.3)
                if not ready:
                    continue
                ch = sys.stdin.read(1)
                if ch == '\x1b':          # ESC
                    self.brake.pause()
                elif ch == '\x03':        # Ctrl+C
                    if self.brake.is_paused():
                        self.brake.abort()
                    else:
                        self.brake.pause()
        except (termios.error, IOError, ValueError):
            pass
        finally:
            self._restore_terminal()

    def _restore_terminal(self) -> None:
        if self._old_settings is None:
            return
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN,
                              self._old_settings)
        except (termios.error, IOError):
            pass
        self._old_settings = None


_stdin_watcher = StdinEscWatcher(repl_brake)


async def _run_with_brake(agent: Agent, message: str) -> str:
    """Run agent with stdin ESC watcher active, so the user can pause/abort
    mid-flight via keystrokes."""
    _stdin_watcher.start()
    try:
        return await agent.run(message)
    except KeyboardInterrupt:
        # Ctrl+C SIGINT while agent is running — the watcher thread already
        # wrote the signal file.  Treat as paused.
        return "[PAUSED]"
    finally:
        _stdin_watcher.stop()


# ── Key bindings (active while prompt_toolkit is listening) ──

kb = KeyBindings()


@kb.add(Keys.Escape)
def on_escape(event):
    """ESC = emergency brake: pause the agent."""
    repl_brake.pause()
    print("\n⏸️  已暂停。输入纠偏指令后 Enter，或 /go 继续，/abort 中止。\n")
    # Flush the prompt_toolkit buffer so the user can type correction
    event.app.renderer.write(event.app.renderer.erase_down())


@kb.add(Keys.ControlC)
def on_ctrl_c(event):
    """Ctrl+C = force abort the agent."""
    if repl_brake.is_paused():
        # Already paused — second Ctrl+C means abort
        repl_brake.abort()
        print("\n🛑 已发送中止信号。\n")
    else:
        # First Ctrl+C = pause
        repl_brake.pause()
        print("\n⏸️  已暂停（Ctrl+C）。/go 继续，/abort 中止。\n")


async def _handle_command(agent: Agent, raw: str) -> tuple[bool, Agent | None]:
    """处理斜杠命令，返回 (已处理, 新agent实例或None)。"""
    parts = raw.split(maxsplit=1)
    cmd = parts[0]
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/help", "/h", "/?"):
        print("\n可用命令：")
        print("  /help, /h, /?              显示帮助")
        print("  /exit, /q, /quit           退出")
        print("  /reset, /clear             重置对话上下文")
        print("  /pause, /brake             暂停当前agent（ESC 也可）")
        print("  /go                        继续执行")
        print("  /abort                     中止当前任务")
        print("  /mode chat|agent           切换模式")
        print("  /skills                    列出技能")
        print("  /inject [name]             注入全局技能（无参数则列出可注入项）")
        print("  /save [name]               保存当前agent（可选指定名称）")
        print("  /load <name> [session_id]  加载已保存的agent")
        print("  /agents                    列出所有已保存的agents")
        print("  /agents-online             列出总线上在线的agents")
        print("  /msg <name> <text>         发送消息到指定agent")
        print("  /sessions                  列出当前agent的所有会话")
        print("  /delete <name>             删除已保存的agent")
        print()
        return True, None

    if cmd in ("/exit", "/q", "/quit"):
        print("\nBye!")
        raise EOFError  # 退出 REPL 循环

    if cmd in ("/pause", "/brake"):
        repl_brake.pause()
        print("\n⏸️  已暂停。输入 /go 继续，/abort 中止，或输入纠偏指令。\n")
        return True, None

    if cmd == "/go":
        repl_brake.resume()
        print("\n▶️  继续执行。\n")
        return True, None

    if cmd == "/abort":
        repl_brake.abort()
        print("\n🛑 已发送中止信号。\n")
        return True, None

    if cmd in ("/reset", "/clear"):
        agent.context = type(agent.context)()  # 重建上下文
        print("\n✓ 上下文已重置\n")
        return True, None

    if cmd == "/mode":
        if arg == "chat":
            print(f"\n✓ 已切换到 chat 模式\n")
        elif arg == "agent":
            print(f"\n✓ 已切换到 agent 模式\n")
        else:
            print(f"\n用法: /mode chat|agent\n")
        return True, None

    if cmd == "/skills":
        skills = agent.skill_loader.available() if hasattr(agent.skill_loader, "available") else []
        if skills:
            print("\n已加载技能：")
            for s in skills:
                print(f"  - {s}")
        else:
            print("\n(无已加载技能)\n")
        return True, None

    if cmd == "/inject":
        if not arg:
            # List available global skills
            from pathlib import Path
            global_skills = [d.name for d in Path("skills").iterdir()
                             if (d / "SKILL.md").exists()]
            current = set(agent.skill_loader.available())
            available = [s for s in global_skills if s not in current]
            if available:
                print(f"\n可注入的全局技能 ({len(available)}):")
                for s in available:
                    print(f"  - {s}")
                print("\n用法: /inject <name>\n")
            else:
                print("\n(无新技能可注入)\n")
        else:
            result = agent.inject_skill(arg)
            print(f"\n{result}\n")
        return True, None

    if cmd == "/save":
        # Optional: /save [name]
        if arg:
            save_message = f"请使用 create_agent 工具保存当前agent,名称为 '{arg}'。"
        else:
            save_message = "请使用 create_agent 工具保存当前agent,根据对话内容自行决定一个合适的 kebab-case 名称。注： 建议名称应与当前任务或上下文强相关,创建的agent的目的是完成特定的任务或角色。"

        print(f"\n正在保存 agent...\n")

        # Run the agent with the save command
        await _run_with_brake(agent, save_message)
        print()

        return True, None

    if cmd == "/load":
        if not arg:
            print("\n用法: /load <name> [session_id]\n")
            return True, None

        parts = arg.split(maxsplit=1)
        name = parts[0]
        session_id = parts[1] if len(parts) > 1 else None

        try:
            new_agent = Agent.load(
                name=name,
                session_id=session_id,
                on_text=agent.on_text,
                on_thinking=agent.on_thinking,
                on_tool_call=agent.on_tool_call,
            )
            print(f"\n✓ Agent 已加载")
            print(f"  名称: {new_agent.agent_name}")
            print(f"  消息数: {len(new_agent.context.messages)}")
            if session_id:
                print(f"  会话: {session_id}")
            else:
                print(f"  会话: 新会话")
            print()
            return True, new_agent
        except FileNotFoundError as e:
            print(f"\n✗ {e}\n")
        except Exception as e:
            print(f"\n✗ 加载失败: {e}\n")

        return True, None

    if cmd == "/agents":
        agents = Agent.list_saved()
        if not agents:
            print("\n(无已保存的agents)\n")
        else:
            print(f"\n已保存的agents ({len(agents)}):")
            print("-" * 80)
            for a in agents:
                print(f"  名称: {a['name']}")
                if a.get('description'):
                    print(f"  描述: {a['description']}")
                print(f"  目录: {a['agent_dir']}")
                print(f"  会话数: {a['session_count']}")

                # Show independent resources
                resources = []
                if a.get('has_memory'):
                    resources.append("MEMORY.md")
                if a.get('has_skills'):
                    resources.append("skills/")
                if resources:
                    print(f"  独立资源: {', '.join(resources)}")

                print(f"  更新时间: {a['updated_at']}")
                print()

        return True, None

    if cmd == "/delete":
        if not arg:
            print("\n用法: /delete <agent_name>\n")
            return True, None

        if Agent.delete_saved(arg):
            print(f"\n✓ Agent '{arg}' 已删除\n")
        else:
            print(f"\n✗ Agent '{arg}' 不存在\n")

        return True, None

    if cmd == "/agents-online":
        if not hasattr(agent, 'bus') or agent.bus is None:
            print("\n✗ Message bus not available\n")
            return True, None

        endpoints = agent.bus.endpoints()
        if not endpoints:
            print("\n(无在线agents)\n")
        else:
            print(f"\n在线agents ({len(endpoints)}):")
            for ep in endpoints:
                marker = " (me)" if ep == agent.endpoint_name else ""
                print(f"  - {ep}{marker}")
            print()
        return True, None

    if cmd == "/msg":
        if not hasattr(agent, 'bus') or agent.bus is None:
            print("\n✗ Message bus not available\n")
            return True, None

        if not arg:
            print("\n用法: /msg <recipient> <message>\n")
            return True, None

        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            print("\n用法: /msg <recipient> <message>\n")
            return True, None

        recipient = parts[0]
        content = parts[1]

        from src.agent.mbus import Message
        msg = Message(
            sender=agent.endpoint_name or "main",
            recipient=recipient,
            content=content,
            kind="text"
        )

        success = await agent.bus.send(msg)
        if success:
            print(f"\n✓ Message sent to '{recipient}'\n")
        else:
            print(f"\n✗ Recipient '{recipient}' not found\n")

        return True, None

    print(f"\n未知命令: {cmd}（输入 /help 查看帮助）\n")
    return False, None


async def run_repl(agent: Agent) -> None:
    # Inject brake into agent so it shares the same signal files
    agent.brake = repl_brake

    # Clean up any leftover signals from a previously crashed session
    repl_brake.reset()

    session = PromptSession(
        completer=SlashCompleter(),
        message=HTML("<b><ansigreen>&gt;</ansigreen></b> "),
        style=REPL_STYLE,
        key_bindings=kb,
    )

    print("输入消息 (Ctrl+D to exit, ESC 暂停, /help 查看命令)\n")

    current_agent = agent

    while True:
        # Race between user input and incoming bus messages
        tasks = [asyncio.create_task(session.prompt_async())]

        # If agent has a bus, also wait for incoming messages
        if hasattr(current_agent, 'bus') and current_agent.bus is not None:
            endpoint = current_agent.bus.get_endpoint(current_agent.endpoint_name or "main")
            if endpoint:
                tasks.append(asyncio.create_task(endpoint.inbox.get()))

        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            # Cancel pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Get the result from the completed task
            completed_task = list(done)[0]
            result = completed_task.result()

            # Check if it's user input (str) or bus message (Message)
            if isinstance(result, str):
                # User input
                user_input = result
                stripped = user_input.strip()
                if not stripped:
                    continue

                # ── 处理暂停状态下的命令 ──
                if repl_brake.is_paused():
                    if stripped == "/go":
                        repl_brake.resume()
                        print("▶️  继续执行。\n")
                        try:
                            await _run_with_brake(current_agent, "")
                        except AgentAborted:
                            print("任务已中止。")
                        print()
                        continue
                    elif stripped == "/abort":
                        repl_brake.abort()
                        print("🛑 已发送中止信号。\n")
                        try:
                            await _run_with_brake(current_agent, "")
                        except AgentAborted:
                            print("任务已中止。")
                        print()
                        continue
                    else:
                        # Treat as correction — inject via signal file, then resume
                        repl_brake.pause(correction=stripped)
                        repl_brake.resume()
                        print(f"💬 纠偏已注入，继续执行。\n")
                        try:
                            await _run_with_brake(current_agent, "")
                        except AgentAborted:
                            print("任务已中止。")
                        print()
                        continue

                # ── 处理 `/` 命令 ──
                if stripped.startswith("/"):
                    try:
                        _, new_agent = await _handle_command(current_agent, stripped)
                        if new_agent:
                            current_agent = new_agent
                    except EOFError:
                        break
                    continue

                print()
                try:
                    result = await _run_with_brake(current_agent, stripped)
                    if result == "[PAUSED]":
                        print("\n⏸️  已暂停。输入纠偏指令后 Enter，或 /go 继续，/abort 中止。\n")
                    elif result == "[ABORTED]":
                        print("任务已中止。")
                except AgentAborted:
                    print("任务已中止。")
                print()

            else:
                # Bus message
                from src.agent.mbus import Message, format_bus_prompt
                msg: Message = result

                # Display incoming message
                print(f"\n\033[93m[← from {msg.sender}]\033[0m")
                print(msg.content)
                print()

                # Process message through agent
                prompt = format_bus_prompt(msg)
                reply = await _run_with_brake(current_agent, prompt)

                if reply == "[PAUSED]" or reply == "[ABORTED]":
                    # Don't send replies for paused/aborted runs
                    print()
                    continue

                # Send reply back only if the incoming message expects one.
                # "result"/"error" are terminal notifications — replying to
                # those would bounce back and forth forever with the sender.
                if (
                    msg.kind in ("task", "text")
                    and msg.correlation_id
                    and hasattr(current_agent, 'bus')
                    and current_agent.bus
                ):
                    reply_msg = Message(
                        sender=current_agent.endpoint_name or "main",
                        recipient=msg.sender,
                        content=reply or "",
                        kind="result",
                        correlation_id=msg.correlation_id
                    )
                    await current_agent.bus.send(reply_msg)

                print()

        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
