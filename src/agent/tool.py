import asyncio

from dataclasses import dataclass
from typing import Callable, Any
from src.agent.tools import (
    BASH_SCHEMA, _bash_handler,
    READ_SCHEMA, _read_handler,
    EDIT_SCHEMA, _edit_handler,
    WRITE_SCHEMA, _write_handler, 
    )

@dataclass
class ToolDef:
    schema: dict
    handler: Callable[..., Any]
    

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
    
    def register(self, tool:ToolDef) -> None:
        self._tools[tool.schema["name"]] = tool
    
    def schemas(self):
        return [i.schema for i in self._tools.values()]

    async def dispatch(self, name:str, _inp: dict) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return "ERROR: tool is None"
        
        try:
            return await tool.handler(_inp)
        except Exception as e:
            return f"Error: {e}"
        
    def names(self) -> list[str]:
        return self._tools.keys()


def build_core_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolDef(schema=READ_SCHEMA, handler=_read_handler))
    registry.register(ToolDef(schema=BASH_SCHEMA, handler=_bash_handler))
    registry.register(ToolDef(schema=EDIT_SCHEMA, handler=_edit_handler))
    registry.register(ToolDef(schema=WRITE_SCHEMA, handler=_write_handler))
    return registry




if __name__ == "__main__":
    import asyncio
    from datetime import datetime
    
    NOW_SCHEMA = {
    "name": "now",
    "description": "get current date and time",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
        }   
    
    }
    
    async def _now_handler(inp:dict):
        await asyncio.sleep(0)
        print(inp.get("description"))
        return datetime.now().isoformat()
    
    registry = ToolRegistry()
    
    registry.register(
        ToolDef(
            schema=NOW_SCHEMA,
            handler=_now_handler
        )
    )
    
    result = asyncio.run(registry.dispatch(name="now",_inp=NOW_SCHEMA))
    print(result)