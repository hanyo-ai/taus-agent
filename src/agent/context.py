

class Context:
    def __init__(self):
        self.messages: list[dict] = []
        self._turn: int = 0
        
    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})
    
    def add_assistant(self, content: list) -> None:
        # breakpoint()
        stripped = [i for i in content if getattr(i, "type", None) != "thinking"]
        self.messages.append({"role": "assistant", "content": stripped})
    
    def add_tool_results(self, results: list[dict]):
        self.messages.append({"role": "user", "content": list(results)})
        
    def to_list(self) -> list[dict]:
        return self.messages
    
    