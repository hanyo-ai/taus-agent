import asyncio

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

from src.agent.core import Agent
from src.agent.persistence import AgentPersistence
from src.utils.completer import SlashCompleter


REPL_STYLE = Style.from_dict({
    "completion-menu.completion": "bg:#1e1e1e fg:#888888",
    "completion-menu.completion.current": "bg:#005f87 fg:#ffffff bold",
    "completion-menu.meta.completion": "bg:#1e1e1e fg:#5f8787",
    "completion-menu.meta.completion.current": "bg:#005f87 fg:#dddddd",
    "scrollbar.background": "bg:#1e1e1e",
    "scrollbar.button": "bg:#444444",
})


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
        print("  /mode chat|agent           切换模式")
        print("  /skills                    列出技能")
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
        skills = agent.skill_loader.list_skills() if hasattr(agent.skill_loader, "list_skills") else []
        if skills:
            print("\n已加载技能：")
            for s in skills:
                print(f"  - {s}")
        else:
            print("\n(无已加载技能)\n")
        return True, None

    if cmd == "/save":
        # Optional: /save [name]
        if arg:
            save_message = f"请使用 create_agent 工具保存当前agent,名称为 '{arg}'。"
        else:
            save_message = "请使用 create_agent 工具保存当前agent,根据对话内容自行决定一个合适的 kebab-case 名称。注： 建议名称应与当前任务或上下文强相关,创建的agent的目的是完成特定的任务或角色。"

        print(f"\n正在保存 agent...\n")

        # Run the agent with the save command
        await agent.run(save_message)
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
    session = PromptSession(
        completer=SlashCompleter(),
        message=HTML("<b><ansigreen>&gt;</ansigreen></b> "),
        style=REPL_STYLE,
    )

    print("输入消息 (Ctrl+D to exit, /help 查看命令)\n")

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
                await current_agent.run(stripped)
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
                reply = await current_agent.run(prompt)

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
