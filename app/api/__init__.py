"""
TAUS Agent — FastAPI Application
=================================
REST + SSE streaming API for the TAUS agent framework.

Start with:
    cd /Users/tsing/data/hanyo/taus-agent
    uv run uvicorn app.api:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from src.agent.core import Agent
from src.agent.llm import ClientConfig
from src.agent.prompts import SYSTEM_PROMPT
from src.agent.mbus import MessageBus, HttpGateway, AgentRunner

load_dotenv()

# ── Module-level MessageBus (shared across all sessions) ─────────────

_bus: MessageBus | None = None
_gateway: HttpGateway | None = None
_bus_agent_ref: "Agent | None" = None
_runner: "AgentRunner | None" = None

# ── App ───────────────────────────────────────────────────────────────

app = FastAPI(title="TAUS Agent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Upload dir ───────────────────────────────────────────────────────

UPLOAD_DIR = Path("temp/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── In-memory session store ──────────────────────────────────────────

# session_id → { "agent": Agent, "agent_ref": str, "created_at": str, "last_active": float }
_sessions: dict[str, dict] = {}
_sessions_lock = asyncio.Lock()


def _make_config() -> ClientConfig:
    return ClientConfig(
        model=os.environ.get("MODEL", "claude-sonnet-4-6"),
        api_key=os.environ.get("API_KEY", ""),
        base_url=os.environ.get("BASE_URL", ""),
        thinking=os.environ.get("THINKING", "0") != "0",
        system_prompt=SYSTEM_PROMPT,
        stream=True,
        provider=os.environ.get("PROVIDER", "anthropic"),
    )


async def _create_session(agent_ref: str = "main") -> tuple[Agent, str]:
    """Create a new session. All agents (including main) use isolated skills.

    Session agents get a unique endpoint on the shared bus so they can use
    send_message / spawn_agent / list_agents tools.
    """
    from src.agent.persistence import AgentPersistence

    async with _sessions_lock:
        new_id = uuid.uuid4().hex[:12]
        endpoint_name = f"session_{agent_ref}_{new_id[:6]}"

        if agent_ref == "main":
            # Main uses isolated skills dir, starting empty (zero skills).
            # Use /inject to add skills from the global pool.
            # Once .agents/main/skills/ exists, core.py auto-detects it
            # and ignores the skills_dir fallback.
            main_skills = AgentPersistence.get_skills_dir("main")
            main_skills.mkdir(parents=True, exist_ok=True)

            agent = Agent(
                config=_make_config(),
                agent_name="main",
                skills_dir="skills",  # fallback, unused once .agents/main/skills/ exists
                bus=_bus,
                endpoint_name=endpoint_name,
            )
        else:
            # Load saved agent (already isolated skills/memory)
            agent = Agent.load(name=agent_ref, session_id=None)
            # Inject bus so loaded agents can also use messaging tools
            agent.bus = _bus
            agent.endpoint_name = endpoint_name
            # Re-register context-aware tools to pick up messaging tools
            agent._register_context_aware_tools()

        # Register endpoint on the bus (no AgentRunner — agent.run()
        # drains the inbox at the end of each turn for sub-agent results).
        if _bus is not None:
            _bus.register(endpoint_name)
            agent._endpoint_registered = endpoint_name

        _sessions[new_id] = {
            "agent": agent,
            "agent_ref": agent_ref,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "last_active": time.time(),
        }
        return agent, new_id


async def _get_session(session_id: str) -> Agent | None:
    async with _sessions_lock:
        sess = _sessions.get(session_id)
        if sess:
            sess["last_active"] = time.time()
            return sess["agent"]
    return None


async def _cleanup_session_endpoint(session_id: str) -> None:
    """Unregister a session agent's endpoint from the bus."""
    async with _sessions_lock:
        sess = _sessions.get(session_id)
        if sess:
            agent = sess["agent"]
            ep = getattr(agent, "_endpoint_registered", None)
            if ep and _bus is not None:
                _bus.unregister(ep)


# ═══════════════════════════════════════════════════════════════════════
#  Health
# ═══════════════════════════════════════════════════════════════════════

@app.get("/api/agent/health")
async def health():
    return {
        "status": "ok",
        "model": os.environ.get("MODEL", "claude-sonnet-4-6"),
        "provider": os.environ.get("PROVIDER", "anthropic"),
        "sessions": len(_sessions),
    }


# ═══════════════════════════════════════════════════════════════════════
#  Skills & Tools
# ═══════════════════════════════════════════════════════════════════════

@app.get("/api/agent/skills")
async def list_skills(session_id: Optional[str] = None):
    """List skills. If session_id given, shows agent's own skills; otherwise global."""
    if session_id:
        agent = await _get_session(session_id)
        if agent:
            available = agent.skill_loader.available()
            return {"skills": [{"name": s, "description": ""} for s in available], "source": "agent"}

    skills_dir = Path("skills")
    skills = []
    if skills_dir.exists():
        for d in skills_dir.iterdir():
            skill_md = d / "SKILL.md"
            if d.is_dir() and skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")
                desc = ""
                for line in content.split("\n"):
                    if line.startswith("# "):
                        desc = line[2:].strip()
                        break
                skills.append({"name": d.name, "description": desc})
    return {"skills": skills, "source": "global"}


@app.get("/api/agent/tools")
async def list_tools():
    agent = Agent(config=_make_config(), agent_name="inspector")
    schemas = agent.registry.schemas()
    return {"tools": schemas}


# ═══════════════════════════════════════════════════════════════════════
#  Sessions
# ═══════════════════════════════════════════════════════════════════════

@app.get("/api/agent/sessions")
async def list_sessions(agent_ref: str = "main"):
    """List saved sessions for the given agent_ref from disk."""
    from src.agent.persistence import AgentPersistence
    sessions = []
    # Read saved sessions from agent's own directory
    sessions_dir = AgentPersistence.get_sessions_dir(agent_ref)
    if sessions_dir.exists():
        for sf in sorted(sessions_dir.glob("*.json"), reverse=True):
            try:
                sdata = json.loads(sf.read_text(encoding="utf-8"))
                sessions.append({
                    "session_id": sf.stem,
                    "agent_ref": agent_ref,
                    "created_at": sdata.get("created_at", ""),
                    "message_count": sdata.get("message_count", 0),
                })
            except Exception:
                sessions.append({"session_id": sf.stem, "agent_ref": agent_ref, "created_at": "", "message_count": 0})

    # Also include active in-memory sessions for this agent_ref
    async with _sessions_lock:
        active = [
            {"session_id": sid, "agent_ref": s["agent_ref"], "created_at": s["created_at"],
             "message_count": len(s["agent"].context.messages)}
            for sid, s in _sessions.items()
            if s["agent_ref"] == agent_ref and len(s["agent"].context.messages) > 0
        ]

    return {"sessions": sessions, "active": active}


@app.post("/api/agent/sessions")
async def create_session(request: Request = None):
    """Create a new session: persist current, start fresh."""
    body = {}
    if request:
        try:
            body = await request.json()
        except Exception:
            pass
    agent_ref = body.get("agent_ref", "main")

    # If there's an active session_id provided, save it first
    old_sid = body.get("session_id")
    if old_sid:
        old_agent = await _get_session(old_sid)
        if old_agent and old_agent.agent_name != "temp_agent" and old_agent.context.messages:
            old_agent.save_session()

    agent, sid = await _create_session(agent_ref)
    return {"session_id": sid, "agent_ref": agent_ref}


@app.get("/api/agent/sessions/{session_id}")
async def get_session(session_id: str):
    agent = await _get_session(session_id)
    if not agent:
        return JSONResponse({"error": "session not found"}, status_code=404)
    async with _sessions_lock:
        s = _sessions[session_id]
    return {
        "session_id": session_id,
        "agent_ref": s["agent_ref"],
        "created_at": s["created_at"],
        "turn_count": len(agent.context.messages),
    }


@app.delete("/api/agent/sessions/{session_id}")
async def delete_session(session_id: str, agent_ref: str = "main"):
    """Delete a session from memory AND disk."""
    # 1) Clean up bus endpoint
    await _cleanup_session_endpoint(session_id)

    # 2) Delete from memory
    async with _sessions_lock:
        if session_id in _sessions:
            del _sessions[session_id]

    # 3) Delete from disk (the session file)
    from src.agent.persistence import AgentPersistence
    session_file = AgentPersistence.get_sessions_dir(agent_ref) / f"{session_id}.json"
    if session_file.exists():
        session_file.unlink()

    return {"status": "deleted"}


@app.get("/api/agent/sessions/{session_id}/history")
async def get_session_history(session_id: str):
    """Get messages for a session. Tries in-memory first, then disk."""
    from src.agent.persistence import AgentPersistence

    agent = await _get_session(session_id)
    messages: list = []

    if agent and agent.context.messages:
        # In-memory agent with messages
        messages = agent.context.messages
    else:
        # Fallback: load from disk — try all known agent dirs
        for agent_name in ["main"]:
            try:
                messages = AgentPersistence.load_session(agent_name, session_id)
                break
            except FileNotFoundError:
                continue

    # Serialize
    serialized = []
    for m in messages:
        msg = m.copy() if isinstance(m, dict) else {"role": "user", "content": str(m)}
        if "content" in msg and isinstance(msg["content"], list):
            msg["content"] = [
                {k: v for k, v in c.items() if k != "_type"} if isinstance(c, dict) else str(c)
                for c in msg["content"]
            ]
        serialized.append(msg)
    return {"messages": serialized, "turn_count": len(serialized)}


@app.post("/api/agent/sessions/{session_id}/activate")
async def activate_session(session_id: str, agent_ref: str = "main"):
    """Load a saved session from disk into memory and return its session_id for chat."""
    from src.agent.persistence import AgentPersistence

    # Load the saved session's messages from the right agent directory
    messages: list = []
    try:
        messages = AgentPersistence.load_session(agent_ref, session_id)
    except FileNotFoundError:
        return JSONResponse({"error": f"Saved session '{session_id}' not found for agent '{agent_ref}'"}, status_code=404)

    if not messages:
        return JSONResponse({"error": f"Session '{session_id}' has no messages"}, status_code=404)

    # Create a new in-memory agent with restored context
    agent, new_id = await _create_session(agent_ref)
    agent.context.messages = messages
    agent.session_id = session_id

    # Serialize messages for the frontend (same logic as get_session_history)
    serialized_messages = []
    for m in messages:
        msg = m.copy() if isinstance(m, dict) else {"role": "user", "content": str(m)}
        if "content" in msg and isinstance(msg["content"], list):
            msg["content"] = [
                {k: v for k, v in c.items() if k != "_type"} if isinstance(c, dict) else str(c)
                for c in msg["content"]
            ]
        serialized_messages.append(msg)

    return {
        "session_id": new_id, "agent_ref": agent_ref,
        "message_count": len(messages), "messages": serialized_messages,
    }


# ═══════════════════════════════════════════════════════════════════════
#  File Upload
# ═══════════════════════════════════════════════════════════════════════

@app.post("/api/agent/upload")
async def upload_file(file: UploadFile = File(...)):
    safe_name = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    file_path = UPLOAD_DIR / safe_name
    content = await file.read()
    file_path.write_bytes(content)
    return {"path": str(file_path.absolute()), "filename": file.filename, "size": len(content)}


# ═══════════════════════════════════════════════════════════════════════
#  Commands: /save, /load, /inject
# ═══════════════════════════════════════════════════════════════════════

@app.post("/api/agent/inject")
async def inject_skill(request: Request):
    """Inject a global skill into the current agent, or list available skills."""
    body = await request.json()
    session_id = body.get("session_id")
    skill_name = body.get("skill", "").strip()

    agent = await _get_session(session_id) if session_id else None
    if not agent:
        return JSONResponse({"error": "session not found"}, status_code=404)

    if not skill_name:
        # List injectable global skills (those NOT already loaded)
        global_skills = [d.name for d in Path("skills").iterdir() if (d / "SKILL.md").exists()]
        current = set(agent.skill_loader.available())
        available = [s for s in global_skills if s not in current]
        return {"available": available, "current": list(current)}

    result = agent.inject_skill(skill_name)
    return {"result": result, "skills": list(agent.skill_loader.available())}


@app.post("/api/agent/load")
async def load_agent(request: Request):
    """Load a saved agent into a new session."""
    body = await request.json()
    agent_name = body.get("name", "").strip()
    if not agent_name:
        return JSONResponse({"error": "agent name required"}, status_code=400)

    # First persist current session if provided
    old_sid = body.get("session_id")
    if old_sid:
        old_agent = await _get_session(old_sid)
        if old_agent and old_agent.agent_name != "temp_agent":
            old_agent.save_session()

    # Check agent exists
    from src.agent.persistence import AgentPersistence
    try:
        AgentPersistence.load_agent(agent_name)
    except FileNotFoundError:
        return JSONResponse({"error": f"Agent '{agent_name}' not found"}, status_code=404)

    agent, sid = await _create_session(agent_name)
    return {"session_id": sid, "agent_ref": agent_name}


# ═══════════════════════════════════════════════════════════════════════
#  Chat (SSE streaming)
# ═══════════════════════════════════════════════════════════════════════

@app.post("/api/agent/chat")
async def chat(request: Request):
    """
    SSE streaming chat endpoint.

    Accepts JSON body:
        { "message": "...", "session_id": "...", "file_path": "..." }
    """
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id")
    file_path = body.get("file_path")

    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    # Inject file context if provided
    if file_path:
        fp = Path(file_path)
        if fp.exists():
            try:
                file_content = fp.read_text(encoding="utf-8")
                ext = fp.suffix.lstrip(".")
                message = (
                    f"[用户上传了文件: {fp.name}]\n\n"
                    f"```{ext}\n{file_content[:8000]}\n```\n\n"
                    f"用户指令: {message}"
                )
            except Exception:
                message = f"[文件路径: {file_path}]\n\n用户指令: {message}"

    # Get or create agent
    if session_id:
        agent = await _get_session(session_id)
        if not agent:
            agent, sid = await _create_session("main")
        else:
            sid = session_id
    else:
        agent, sid = await _create_session("main")

    # Handle /save - instruct the agent to save itself (like REPL does)
    # The agent will call create_agent tool which persists to .agents/
    if message.strip().startswith("/save"):
        parts = message.strip().split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            message = f"请使用 create_agent 工具保存当前 agent，名称为 '{parts[1].strip()}'。根据对话内容描述这个 agent 的用途。"
        else:
            message = "请使用 create_agent 工具保存当前 agent，根据对话内容自行决定一个合适的 kebab-case 名称。建议名称应与当前任务或上下文强相关，创建的 agent 的目的是完成特定的任务或角色。"

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()

        def send_event(event_type: str, data):
            try:
                queue.put_nowait((event_type, data))
            except asyncio.QueueFull:
                pass

        def on_text(t: str):
            send_event("text", {"text": t})

        def on_thinking(t: str):
            send_event("thinking", {"text": t})

        def on_tool_call(name: str, inp: dict):
            send_event("tool_call", {"name": name, "input": inp})

        orig_text = agent.on_text
        orig_thinking = agent.on_thinking
        orig_tool_call = agent.on_tool_call

        agent.on_text = on_text
        agent.on_thinking = on_thinking
        agent.on_tool_call = on_tool_call

        yield f"event: init\ndata: {json.dumps({'session_id': sid})}\n\n"

        run_task = asyncio.ensure_future(agent.run(message))

        try:
            # ── Phase 1: Main agent run ──
            while True:
                try:
                    evt_type, evt_data = await asyncio.wait_for(queue.get(), timeout=0.05)
                    yield f"event: {evt_type}\ndata: {json.dumps(evt_data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if run_task.done():
                        while not queue.empty():
                            evt_type, evt_data = queue.get_nowait()
                            yield f"event: {evt_type}\ndata: {json.dumps(evt_data, ensure_ascii=False)}\n\n"
                        try:
                            result = run_task.result()
                            yield f"event: done\ndata: {json.dumps({'session_id': sid, 'text': result}, ensure_ascii=False)}\n\n"
                        except Exception as e:
                            yield f"event: error\ndata: {json.dumps({'text': str(e)})}\n\n"
                        break

                if run_task.done() and queue.empty():
                    try:
                        result = run_task.result()
                        yield f"event: done\ndata: {json.dumps({'session_id': sid, 'text': result}, ensure_ascii=False)}\n\n"
                    except Exception as e:
                        yield f"event: error\ndata: {json.dumps({'text': str(e)})}\n\n"
                    break

            # ── Phase 2: Drain inbox for sub-agent results ──
            # After the main agent finishes its turn, it may have spawned
            # sub-agents.  Wait for their results on the bus and forward
            # them to the frontend so the user sees the full workflow.
            ep_name = getattr(agent, '_endpoint_registered', None)
            if ep_name and _bus is not None:
                ep = _bus.get_endpoint(ep_name)
                if ep is not None:
                    # Collect results for up to 120s total idle time
                    idle_deadline = time.time() + 120
                    while time.time() < idle_deadline:
                        try:
                            msg = await asyncio.wait_for(
                                ep.inbox.get(), timeout=2.0
                            )
                        except asyncio.TimeoutError:
                            # No message arrived — idling, check deadline
                            continue

                        # Got a message from a sub-agent
                        from src.agent.mbus import format_bus_prompt

                        # Add to agent context (saved on next run or save)
                        prompt_text = format_bus_prompt(msg)
                        agent.context.add_user(prompt_text)

                        # Yield the message to the frontend
                        yield (
                            f"event: agent_message\n"
                            f"data: {json.dumps({
                                'sender': msg.sender,
                                'content': msg.content,
                                'kind': msg.kind,
                                'id': msg.id,
                            }, ensure_ascii=False)}\n\n"
                        )

                        # Auto-save session
                        if agent.agent_name != "temp_agent":
                            agent.save_session()

                        # Reset idle deadline on each message
                        idle_deadline = time.time() + 120

        finally:
            agent.on_text = orig_text
            agent.on_thinking = orig_thinking
            agent.on_tool_call = orig_tool_call
            if not run_task.done():
                run_task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════════════════════════
#  Background Tasks (Agent Space)
# ═══════════════════════════════════════════════════════════════════════

# task_id → { task_id, agent_ref, message, status, log[], result, session_id, ... }
_tasks: dict[str, dict] = {}
_tasks_lock = asyncio.Lock()


async def _run_task(task: dict):
    """Run a background task: create agent session, execute, collect logs/results."""
    try:
        agent, sid = await _create_session(task["agent_ref"])
        task["session_id"] = sid
        task["status"] = "running"
        task["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        # Override callbacks to capture logs
        def on_tool(name: str, inp: dict):
            task["log"].append({"time": time.strftime("%H:%M:%S"), "tool": name, "msg": str(inp.get("name", ""))[:60], "ok": None})

        def on_text(t: str):
            task["result"] = (task.get("result") or "") + t

        agent.on_tool_call = on_tool
        agent.on_text = on_text

        # Run the agent
        result = await agent.run(task["message"])
        task["result"] = result or ""
        task["status"] = "done"
        task["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        task["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    finally:
        # Clean up session runner to avoid orphaned runners
        sid = task.get("session_id")
        if sid:
            await _cleanup_session_endpoint(sid)


@app.post("/api/agent/tasks")
async def create_task(request: Request):
    """Start a background task. Returns task_id immediately, agent runs async."""
    body = await request.json()
    message = body.get("message", "").strip()
    agent_ref = body.get("agent_ref", "main")
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    task_id = uuid.uuid4().hex[:12]
    task = {
        "task_id": task_id,
        "agent_ref": agent_ref,
        "message": message,
        "status": "pending",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "started_at": None,
        "finished_at": None,
        "result": "",
        "error": None,
        "log": [],
        "session_id": None,
    }

    async with _tasks_lock:
        _tasks[task_id] = task

    # Launch background
    asyncio.ensure_future(_run_task(task))

    return {"task_id": task_id, "agent_ref": agent_ref, "status": "pending"}


@app.get("/api/agent/tasks")
async def list_tasks():
    """List all tasks (active + recent)."""
    async with _tasks_lock:
        tasks = [
            {
                "task_id": t["task_id"],
                "agent_ref": t["agent_ref"],
                "message": t["message"][:100],
                "status": t["status"],
                "created_at": t["created_at"],
                "finished_at": t.get("finished_at"),
                "result": (t.get("result") or "")[:200],
                "error": t.get("error"),
                "log": t.get("log", []),
                "session_id": t.get("session_id"),
            }
            for t in _tasks.values()
        ]
    # Sort by created_at descending, limit to 50
    tasks.sort(key=lambda t: t["created_at"], reverse=True)
    return {"tasks": tasks[:50]}


@app.get("/api/agent/tasks/{task_id}")
async def get_task(task_id: str):
    """Get a single task's details + full messages."""
    async with _tasks_lock:
        if task_id not in _tasks:
            return JSONResponse({"error": "task not found"}, status_code=404)
        t = _tasks[task_id]
        info = {
            "task_id": t["task_id"],
            "agent_ref": t["agent_ref"],
            "message": t["message"],
            "status": t["status"],
            "created_at": t["created_at"],
            "started_at": t.get("started_at"),
            "finished_at": t.get("finished_at"),
            "result": t.get("result", ""),
            "error": t.get("error"),
            "log": t.get("log", []),
            "session_id": t.get("session_id"),
        }
    return info


@app.get("/api/agent/tasks/{task_id}/stream")
async def task_stream(task_id: str):
    """SSE stream for a running task — polls status until done."""
    async def generate():
        last_log_len = 0
        while True:
            async with _tasks_lock:
                t = _tasks.get(task_id)
            if not t:
                yield f"event: error\ndata: {json.dumps({'text': 'task not found'})}\n\n"
                return

            # Send new log entries
            logs = t.get("log", [])
            for log_entry in logs[last_log_len:]:
                yield f"event: tool_call\ndata: {json.dumps({'name': log_entry['tool'], 'input': {}})}\n\n"
            last_log_len = len(logs)

            if t["status"] == "done":
                yield f"event: text\ndata: {json.dumps({'text': t.get('result', '')})}\n\n"
                yield f"event: done\ndata: {json.dumps({'task_id': task_id, 'text': t.get('result', '')})}\n\n"
                return
            if t["status"] == "failed":
                yield f"event: error\ndata: {json.dumps({'text': t.get('error', 'Unknown error')})}\n\n"
                return

            await asyncio.sleep(0.3)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ═══════════════════════════════════════════════════════════════════════
#  Scheduled Jobs (Cron-like agent tasks)
# ═══════════════════════════════════════════════════════════════════════

# job_id → { id, agent_ref, message, schedule_type, schedule_value,
#            enabled, next_run, last_run, last_status, last_result, log[] }
_jobs: dict[str, dict] = {}
_jobs_lock = asyncio.Lock()

# Managed jobs are loaded from JOBS_CONFIG env var or jobs.json at project root
_JOBS_CONFIG_PATH = Path(os.environ.get("JOBS_CONFIG", "jobs.json"))


async def _import_managed_jobs():
    """Import managed jobs from JOBS_CONFIG json file on startup.
    
    Each job entry needs: id, log_file, state_file (optional: agent_ref, message, schedule_*)
    The external script is responsible for writing a standardized state file:
        { "status": "running|done|failed|idle", "last_run": "...", "last_result": "..." }
    """
    if not _JOBS_CONFIG_PATH.exists():
        return

    try:
        config = json.loads(_JOBS_CONFIG_PATH.read_text(encoding="utf-8"))
        managed_jobs = config.get("managed_jobs", [])
    except Exception:
        return

    async with _jobs_lock:
        for mj in managed_jobs:
            jid = mj.get("id", "")
            if not jid or jid in _jobs:
                continue

            job = {
                "id": jid,
                "agent_ref": mj.get("agent_ref", "main"),
                "message": mj.get("message", ""),
                "schedule_type": mj.get("schedule_type", "interval_hours"),
                "schedule_value": mj.get("schedule_value", "1"),
                "enabled": mj.get("enabled", True),
                "managed": True,
                "next_run": _next_run_time(mj.get("schedule_type", "interval_hours"), mj.get("schedule_value", "1")),
                "last_run": None,
                "last_status": None,
                "last_result": "",
                "log": [],
                "log_file": mj.get("log_file", ""),
                "state_file": mj.get("state_file", ""),
                "pid_file": mj.get("pid_file", ""),
            }
            _jobs[jid] = job

            # Read initial status from standardized state file
            _read_job_state_file(job)


def _read_job_state_file(job: dict):
    """Read standardized state file into job dict. No-op if file missing or malformed.
    
    Expected state file format:
        { "status": "running|done|failed|idle", "last_run": "...", "last_result": "..." }
    Any extra keys (e.g. 'total', 'error') are used as fallback for last_result.
    """
    state_path = Path(job.get("state_file", ""))
    if not state_path.exists():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if "status" in state:
            job["last_status"] = state["status"]
        if "last_run" in state:
            job["last_run"] = state["last_run"]
        if "last_result" in state:
            job["last_result"] = str(state["last_result"])[:500]
        elif "total" in state:
            # Fallback: use total counter as result summary
            job["last_result"] = f"累计 {state['total']} 条"
        if "error" in state and state["error"]:
            job["last_result"] = str(state["error"])[:500]
    except Exception:
        pass


async def _watch_managed_jobs():
    """Periodically refresh managed jobs: read state files + tail log files.
    
    - State file: primary source for status/last_run/last_result
    - Log file: appended as raw log entries for UI visibility (no parsing)
    """
    import re
    last_positions: dict[str, int] = {}

    while True:
        await asyncio.sleep(15)
        async with _jobs_lock:
            for jid, job in list(_jobs.items()):
                if not job.get("managed"):
                    continue

                # 1) Refresh status from standardized state file
                _read_job_state_file(job)

                # 2) Tail log file — generic [timestamp] message extraction
                log_path = Path(job.get("log_file", ""))
                if not log_path.exists():
                    continue

                offset = last_positions.get(jid, 0)
                try:
                    content = log_path.read_text(encoding="utf-8")
                    if len(content) <= offset:
                        continue
                    new_content = content[offset:]
                    last_positions[jid] = len(content)

                    for line in new_content.split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        m = re.match(r"^\[(.+?)\]\s+(.+)$", line)
                        if m:
                            ts, msg = m.group(1), m.group(2)
                            job["log"].append({"time": ts, "tool": "📄", "msg": msg[:120], "ok": None})
                        else:
                            # Non-timestamped line — still capture
                            job["log"].append({"time": "", "tool": "📄", "msg": line[:120], "ok": None})

                    # Cap log size
                    if len(job["log"]) > 200:
                        job["log"] = job["log"][-100:]

                except Exception:
                    pass
_scheduler_running = False
_scheduler_task: "asyncio.Task | None" = None
_watcher_task: "asyncio.Task | None" = None


def _next_run_time(schedule_type: str, schedule_value: str, from_time: float | None = None) -> float:
    """Compute the next run timestamp. Returns Unix timestamp."""
    now = from_time or time.time()
    if schedule_type == "interval_minutes":
        mins = int(schedule_value) if schedule_value else 60
        return now + mins * 60
    elif schedule_type == "interval_hours":
        hrs = int(schedule_value) if schedule_value else 1
        return now + hrs * 3600
    elif schedule_type == "daily_at":
        # schedule_value = "HH:MM"
        try:
            h, m = map(int, schedule_value.split(":"))
            from datetime import datetime
            target = datetime.fromtimestamp(now).replace(hour=h, minute=m, second=0, microsecond=0)
            if target.timestamp() <= now:
                target = target.replace(day=target.day + 1)
            return target.timestamp()
        except Exception:
            return now + 86400
    else:
        return now + 3600  # default: 1 hour


async def _scheduler_loop():
    """Background loop that checks due jobs every 30 seconds."""
    global _scheduler_running
    _scheduler_running = True
    while _scheduler_running:
        await asyncio.sleep(30)
        now = time.time()
        due_jobs: list[dict] = []
        async with _jobs_lock:
            for job in _jobs.values():
                if job.get("enabled", True) and job.get("next_run", 0) <= now:
                    due_jobs.append(dict(job))
                    job["next_run"] = _next_run_time(job.get("schedule_type", "interval_hours"), job.get("schedule_value", "1"), now)

        for job in due_jobs:
            await _execute_scheduled_job(job)


async def _execute_scheduled_job(job: dict):
    """Execute a single scheduled job."""
    job_id = job["id"]
    log_entries: list[dict] = job.setdefault("log", [])
    log_entries.append({"time": time.strftime("%H:%M:%S"), "tool": "⏱", "msg": "定时触发", "ok": None})

    # Also create a task for visibility in Agent Space
    task_id = uuid.uuid4().hex[:12]
    task = {
        "task_id": task_id, "agent_ref": job["agent_ref"], "message": job["message"],
        "status": "pending", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "started_at": None, "finished_at": None, "result": "", "error": None,
        "log": [], "session_id": None, "is_scheduled": True, "job_id": job_id,
    }
    async with _tasks_lock:
        _tasks[task_id] = task

    try:
        agent, sid = await _create_session(job["agent_ref"])
        task["session_id"] = sid
        task["status"] = "running"
        task["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        def on_tool(name: str, inp: dict):
            entry = {"time": time.strftime("%H:%M:%S"), "tool": name, "msg": str(inp.get("name", ""))[:60], "ok": None}
            task["log"].append(entry)
            log_entries.append(entry)

        def on_text(t: str):
            task["result"] = (task.get("result") or "") + t

        agent.on_tool_call = on_tool
        agent.on_text = on_text

        result = await agent.run(job["message"])
        task["result"] = result or ""
        task["status"] = "done"
        task["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        async with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                _jobs[job_id]["last_status"] = "done"
                _jobs[job_id]["last_result"] = (result or "")[:500]

    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        task["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        async with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                _jobs[job_id]["last_status"] = "failed"
                _jobs[job_id]["last_result"] = str(e)[:500]
    finally:
        # Clean up session runner
        sid = task.get("session_id")
        if sid:
            await _cleanup_session_endpoint(sid)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start MessageBus + HttpGateway + scheduler on startup, clean up on shutdown."""
    global _scheduler_running, _scheduler_task, _watcher_task
    global _bus, _gateway, _bus_agent_ref, _runner

    # ── 1. Create shared MessageBus ──────────────────────────────────
    _bus = MessageBus()
    print("[lifespan] MessageBus created")

    # ── 2. Create & mount HttpGateway (external scripts / WebSocket) ─
    _gateway = HttpGateway(_bus, endpoint_name="http_gateway")
    _gateway.start()
    app.include_router(_gateway.router, prefix="/api/mbus")
    print("[lifespan] HttpGateway mounted at /api/mbus")

    # ── 3. Create persistent "main" agent on the bus ─────────────────
    # This agent listens for messages sent to "main" and processes them.
    # External scripts can POST /api/mbus/message {"recipient":"main",...}
    _bus_agent_ref = Agent(
        config=_make_config(),
        agent_name="main",
        skills_dir="skills",
        bus=_bus,
        endpoint_name="main",
        on_text=lambda t: None,
        on_thinking=lambda t: None,
        on_tool_call=lambda name, inp: None,
        auto_save_session=False,
    )
    # Ensure main skills dir exists
    from src.agent.persistence import AgentPersistence
    AgentPersistence.get_skills_dir("main").mkdir(parents=True, exist_ok=True)

    _runner = AgentRunner(agent=_bus_agent_ref, bus=_bus, endpoint_name="main")
    _runner.start()
    print("[lifespan] AgentRunner 'main' started — listening on bus")

    # ── 4. Import managed jobs & start scheduler ────────────────────
    await _import_managed_jobs()
    _scheduler_running = True
    _scheduler_task = asyncio.ensure_future(_scheduler_loop())
    _watcher_task = asyncio.ensure_future(_watch_managed_jobs())

    yield

    # ── Shutdown ─────────────────────────────────────────────────────
    _scheduler_running = False
    for t in [_scheduler_task, _watcher_task]:
        if t and not t.done():
            t.cancel()

    # Stop bus runner
    if _runner:
        await _runner.stop()
        print("[lifespan] AgentRunner stopped")

    # Close WebSocket connections
    if _gateway:
        await _gateway.close_websockets()
        _gateway.stop()
        print("[lifespan] HttpGateway stopped")


# Lifespan replaces deprecated @app.on_event
app.router.lifespan_context = lifespan


@app.get("/api/agent/jobs")
async def list_jobs():
    """List all scheduled jobs."""
    async with _jobs_lock:
        jobs = [
            {
                "id": j["id"],
                "agent_ref": j["agent_ref"],
                "message": j["message"][:200],
                "schedule_type": j.get("schedule_type", "interval_hours"),
                "schedule_value": j.get("schedule_value", "1"),
                "enabled": j.get("enabled", True),
                "next_run": j.get("next_run", 0),
                "last_run": j.get("last_run"),
                "last_status": j.get("last_status"),
                "last_result": (j.get("last_result") or "")[:200],
                "log": j.get("log", [])[-20:],
                "managed": j.get("managed", False),
            }
            for j in _jobs.values()
        ]
    jobs.sort(key=lambda j: j["next_run"])
    return {"jobs": jobs}


@app.post("/api/agent/jobs")
async def create_job(request: Request):
    """Create a scheduled job."""
    body = await request.json()
    agent_ref = body.get("agent_ref", "main")
    message = body.get("message", "").strip()
    schedule_type = body.get("schedule_type", "interval_hours")
    schedule_value = body.get("schedule_value", "1")
    enabled = body.get("enabled", True)

    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    job_id = uuid.uuid4().hex[:8]
    next_run = _next_run_time(schedule_type, schedule_value)

    job = {
        "id": job_id, "agent_ref": agent_ref, "message": message,
        "schedule_type": schedule_type, "schedule_value": schedule_value,
        "enabled": enabled, "next_run": next_run,
        "last_run": None, "last_status": None, "last_result": "",
        "log": [],
    }

    async with _jobs_lock:
        _jobs[job_id] = job

    return {"job_id": job_id, "next_run": next_run}


@app.post("/api/agent/jobs/{job_id}/run")
async def run_job_now(job_id: str):
    """Execute a scheduled job immediately (one-shot)."""
    async with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return JSONResponse({"error": "job not found"}, status_code=404)
        job_copy = dict(job)

    # Run in background
    asyncio.ensure_future(_execute_scheduled_job(job_copy))
    return {"status": "triggered", "job_id": job_id}


@app.post("/api/agent/jobs/{job_id}/stop")
async def stop_job(job_id: str):
    """Stop a managed job: disable it, write 'stopped' to its state file,
    and optionally kill the external process if pid_file is configured."""
    import signal as sig

    async with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return JSONResponse({"error": "job not found"}, status_code=404)
        job["enabled"] = False
        job["last_status"] = "stopped"
        state_file = job.get("state_file", "")
        pid_file = job.get("pid_file", "")

    # Write stopped status to state file so external script can check
    if state_file:
        try:
            sp = Path(state_file)
            existing = {}
            if sp.exists():
                existing = json.loads(sp.read_text(encoding="utf-8"))
            existing["status"] = "stopped"
            existing["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
            sp.write_text(json.dumps(existing, indent=2))
        except Exception:
            pass

    # Try to kill the external process if pid_file configured
    killed = False
    if pid_file:
        try:
            pp = Path(pid_file)
            if pp.exists():
                pid = int(pp.read_text().strip())
                os.kill(pid, sig.SIGTERM)
                killed = True
        except Exception:
            pass

    return {"status": "stopped", "killed": killed}


@app.post("/api/agent/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    """Re-enable a stopped managed job: set enabled=True, clear stopped status in state file."""
    async with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return JSONResponse({"error": "job not found"}, status_code=404)
        job["enabled"] = True
        job["last_status"] = None
        state_file = job.get("state_file", "")
        # Recalc next_run so it doesn't fire immediately
        job["next_run"] = _next_run_time(job.get("schedule_type", "interval_hours"), job.get("schedule_value", "1"))

    # Clear stopped status from state file so external script can resume
    if state_file:
        try:
            sp = Path(state_file)
            if sp.exists():
                existing = json.loads(sp.read_text(encoding="utf-8"))
                existing.pop("status", None)
                existing["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
                sp.write_text(json.dumps(existing, indent=2))
        except Exception:
            pass

    return {"status": "resumed"}


@app.patch("/api/agent/jobs/{job_id}")
async def update_job(job_id: str, request: Request):
    """Update job: toggle enabled, change schedule, etc."""
    body = await request.json()
    async with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return JSONResponse({"error": "job not found"}, status_code=404)
        if "enabled" in body:
            job["enabled"] = bool(body["enabled"])
        if "schedule_type" in body and "schedule_value" in body:
            job["schedule_type"] = body["schedule_type"]
            job["schedule_value"] = body["schedule_value"]
            job["next_run"] = _next_run_time(body["schedule_type"], body["schedule_value"])
        if "message" in body:
            job["message"] = body["message"]
    return {"status": "updated"}


@app.delete("/api/agent/jobs/{job_id}")
async def delete_job(job_id: str):
    """Remove a job from the dashboard. Managed jobs are also removable — this only
    removes the in-memory tracking, not the external cron/process."""
    async with _jobs_lock:
        if job_id in _jobs:
            del _jobs[job_id]
    return {"status": "deleted"}


# ═══════════════════════════════════════════════════════════════════════
#  Agents Dashboard (lists all saved agents from .agents/)
# ═══════════════════════════════════════════════════════════════════════

@app.get("/api/agents")
async def list_saved_agents():
    """List all saved agents from .agents directory, excluding temp API sessions."""
    from src.agent.persistence import AgentPersistence
    all_agents = AgentPersistence.list_agents()
    saved = [a for a in all_agents if not a["name"].startswith("api_")]
    return {
        "agents": saved,
        "active_refs": [{"session_id": sid, "agent_ref": s["agent_ref"]} for sid, s in _sessions.items()],
    }


@app.post("/api/agents/{name}/use")
async def use_agent(name: str):
    """Load a saved agent into a new session. Returns session_id for /chat."""
    from src.agent.persistence import AgentPersistence
    try:
        AgentPersistence.load_agent(name)
    except FileNotFoundError:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)

    agent, sid = await _create_session(name)
    return {"session_id": sid, "agent_ref": name}


@app.get("/api/agents/{name}/info")
async def get_agent_info(name: str):
    """Get detailed info about a saved agent (no API keys)."""
    from src.agent.persistence import AgentPersistence
    try:
        data = AgentPersistence.load_agent(name)
        config = data.get("config", {})
        return {
            "name": data.get("name"),
            "description": data.get("description", ""),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "sessions": data.get("sessions", []),
            "session_count": data.get("session_count", 0),
            "has_memory": data.get("has_memory", False),
            "has_skills": data.get("has_skills", False),
            "model": config.get("model", ""),
            "provider": config.get("provider", ""),
        }
    except FileNotFoundError:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)


@app.get("/api/agents/{name}/sessions")
async def list_agent_sessions(name: str):
    """List saved sessions for an agent."""
    from src.agent.persistence import AgentPersistence
    try:
        AgentPersistence.load_agent(name)
        sessions = []
        sessions_dir = AgentPersistence.get_sessions_dir(name)
        if sessions_dir.exists():
            for sf in sorted(sessions_dir.glob("*.json"), reverse=True):
                try:
                    sdata = json.loads(sf.read_text(encoding="utf-8"))
                    sessions.append({
                        "session_id": sf.stem,
                        "created_at": sdata.get("created_at", ""),
                        "message_count": sdata.get("message_count", 0),
                    })
                except Exception:
                    sessions.append({"session_id": sf.stem, "created_at": "", "message_count": 0})
        return {"agent_name": name, "sessions": sessions}
    except FileNotFoundError:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
