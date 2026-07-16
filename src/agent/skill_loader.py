import re
from pathlib import Path
from dataclasses import dataclass

@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path

class SkillLoader:
    def __init__(self, skill_dir: str | Path = "skills"):
        self._dir = Path(skill_dir)
        # self._dir.absolute()
        self._skills: dict[str, Skill] = {}
        self._load_all()
        
    def _load_all(self) -> None:
        if not self._dir.exists():
            return
        for skill_file in self._dir.rglob("SKILL.md"):
            skill = self._parse(skill_file)
            if skill:
                self._skills[skill.name] = skill
    
    def _parse(self, path: Path) -> Skill | None:
        text = path.read_text(encoding="utf-8")
        """re.DOTALL: make . match newlines"""
        match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
        meta: dict[str, str] = {}
        body = text
        
        if match:
            for line in match.group(1).strip().splitlines():
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
            body = match.group(2).strip()

        name = meta.get("name", path.stem) # path.parent.name
        description = meta.get("description", "")
        
        if not name or len(name) > 64:
            return None
        if not description:
            return None
        
        return Skill(name=name, description=description, body=body, path=path)
        
    def available(self) -> list[str]:
        return list(self._skills.keys())
    
    def system_prompt_section(self) -> str:
        """Level-1 content injected into every system prompt."""
        if not self._skills:
            return ""
        lines = ["## Available Skills", ""]
        for s in self._skills.values():
            lines.append(f"- **{s.name}**: {s.description}")
        lines.append(
            "\nTo use a skill, call the `load_skill` tool with the skill name. "
            "This loads the full instructions for that skill into your context."
        )
        return "\n".join(lines)
    
    def load(self, name: str) -> str:
        """Level-2 load: return the full SKILL.md body wrapped in a skill tag."""
        skill = self._skills.get(name)
        if skill is None:
            available = ", ".join(self._skills.keys()) or "(none)"
            return f"Error: unknown skill '{name}'. Available: {available}"
        return f'<skill name="{name}">\n{skill.body}\n</skill>'

    def skill_tool_schema(self) -> dict:
        """Return the Anthropic tool schema for load_skill."""
        names = self.available()
        schema: dict = {
            "name": "load_skill",
            "description": (
                "Load the full instructions for a named skill into the conversation context. "
                "Call this when the user's request matches a skill's description. "
                "The skill body contains step-by-step guidance and examples. "
                f"Available skills: {', '.join(names) if names else '(none)'}."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The skill name to load.",
                        "enum": names if names else ["(none)"],
                    }
                },
                "required": ["name"],
            },
        }
        return schema

    def inject(self, name: str, skill: Skill) -> bool:
        """Inject a skill from an external source.  Won't overwrite an
        existing skill (agent-local skills take priority)."""
        if name in self._skills:
            return False
        self._skills[name] = skill
        return True

    def reload(self) -> None:
        """Re-scan the skills directory (useful for hot-reloading during development)."""
        self._skills.clear()
        self._load_all()