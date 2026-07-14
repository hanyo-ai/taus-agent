from prompt_toolkit.completion import Completer, Completion

"""
命令注册表
"""

COMMAND_REGISTRY: dict[str, dict] = {
    "/help": {
        "aliases": ["/h", "/?"],
        "desc": "显示帮助信息",
    },
    "/exit": {
        "aliases": ["/q", "/quit"],
        "desc": "退出程序",
    },
    "/reset": {
        "aliases": ["/clear"],
        "desc": "重置对话上下文",
    },
    "/mode": {
        "aliases": [],
        "desc": "切换模式",
        "subcommands": {
            "chat": "纯对话模式",
            "agent": "Agent 模式（可调用工具）",
        },
    },
    "/skills": {
        "aliases": [],
        "desc": "列出已加载的技能",
    },
    "/save": {
        "aliases": [],
        "desc": "保存当前agent",
    },
    "/load": {
        "aliases": [],
        "desc": "加载已保存的agent",
    },
    "/agents": {
        "aliases": [],
        "desc": "列出所有已保存的agents",
    },
    "/delete": {
        "aliases": [],
        "desc": "删除已保存的agent",
    },
}


def _all_commands() -> list[tuple[str, str]]:
    """返回所有命令及其描述，包括别名。"""
    result: list[tuple[str, str]] = []
    for cmd, info in COMMAND_REGISTRY.items():
        result.append((cmd, info["desc"]))
        for alias in info.get("aliases", []):
            result.append((alias, info["desc"]))
    return result   


def _resolve_command(cmd: str) -> str | None:
    """将别名解析为主命令名。"""
    if cmd in COMMAND_REGISTRY:
        return cmd
    for name, info in COMMAND_REGISTRY.items():
        if cmd in info.get("aliases", []):
            return name
    return None


class SlashCompleter(Completer):
    """支持嵌套子命令的 `/` 补全器。"""

    def get_completions(self, document, complete_event):
        text = document.text
        if not text.startswith("/"):
            return

        tokens = text.split(maxsplit=1)
        cmd = tokens[0]

        # 情况 1: 正在补全命令名（还没有空格）
        if len(tokens) == 1:
            for name, desc in _all_commands():
                if name.startswith(cmd):
                    yield Completion(
                        name,
                        start_position=-len(text),
                        display_meta=desc,
                    )
            return

        # 情况 2: 命令名 + 空格 + 正在补全子命令
        arg = tokens[1] if len(tokens) > 1 else ""
        main_cmd = _resolve_command(cmd)
        if main_cmd and "subcommands" in COMMAND_REGISTRY.get(main_cmd, {}):
            subcmds = COMMAND_REGISTRY[main_cmd]["subcommands"]
            for sub, sub_desc in subcmds.items():
                if sub.startswith(arg):
                    yield Completion(
                        sub,
                        start_position=-len(arg),
                        display_meta=sub_desc,
                    )
