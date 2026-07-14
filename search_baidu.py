"""使用 Browser 模块打开百度搜索 ai news（修复版）"""
import asyncio
from src.browser import BrowserConfig, BrowserSession

async def main():
    config = BrowserConfig(headless=False)
    session = BrowserSession(config=config)

    await session.start()
    print("浏览器已启动")

    # 打开百度
    await session.navigate("https://www.baidu.com")
    print("已打开百度首页")

    page = await session.get_current_page()

    # 方案1：用 JS 直接设置值并提交（最可靠）
    try:
        # 先找到搜索框，尝试点击聚焦
        search_input = await page.query_selector("#kw")
        if search_input:
            await search_input.scroll_into_view()
            await search_input.click()  # 点击来聚焦
            await asyncio.sleep(0.3)
            print("已点击搜索框")
    except Exception as e:
        print(f"点击搜索框失败: {e}")

    # 用 evaluate 直接设置输入框的值
    await page.evaluate("""
        (function() {
            var input = document.querySelector('#kw');
            if (input) {
                input.value = 'ai news';
                input.dispatchEvent(new Event('input', { bubbles: true }));
                return 'ok';
            }
            return 'not found';
        })()
    """)
    print("已设置搜索词: ai news")

    # 点击搜索按钮
    try:
        search_btn = await page.query_selector("#su")
        if search_btn:
            await search_btn.click()
            print("已点击搜索按钮")
    except Exception as e:
        print(f"点击按钮失败: {e}，尝试用 JS 提交")
        await page.evaluate("document.querySelector('#su').click()")

    # 等待搜索结果加载
    await asyncio.sleep(3)

    # 截图保存结果
    await session.screenshot("baidu_search_result.png")
    print("截图已保存: baidu_search_result.png")

    # 打印页面标题
    title = await session.get_current_title()
    print(f"页面标题: {title}")

    await session.stop()
    print("浏览器已关闭")


if __name__ == "__main__":
    asyncio.run(main())
