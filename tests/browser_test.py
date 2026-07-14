import os
os.environ["ANONYMIZED_TELEMETRY"] = "false"
import asyncio

from browser_use import Agent, ChatBrowserUse

# class LLMBrowserUse(ChatBrowserUse):
#     def s

# async def main():
#     agent = Agent(
#         task="Find the number of stars of the browser-use repo",
#         llm=ChatBrowserUse(model='deepseek-v4-pro', api_key="sk-60536fb22eb94bf0bb1c382567c5a9cc", base_url="https://api.deepseek.com"),
#         # llm=ChatBrowserUse(model='bu-2-0'),  # Browser Use's optimized model
#         # llm=ChatOpenAI(model='gpt-5.5'),
#         # llm=ChatAnthropic(model='claude-opus-4-8'),  # Sonnet also works well
#     )
#     history = await agent.run()

# if __name__ == "__main__":
#     asyncio.run(main())


from browser_use import Agent
from browser_use.llm.deepseek.chat import ChatDeepSeek
from dotenv import load_dotenv
import asyncio

load_dotenv()

async def main():
    llm = ChatDeepSeek(model='deepseek-chat', api_key="sk-60536fb22eb94bf0bb1c382567c5a9cc")
    task = "打开百度 www.baidu.com, 搜索ai news"
    agent = Agent(task=task, llm=llm)
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())