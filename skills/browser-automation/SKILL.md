---
name: browser-automation
description: CDP 浏览器自动化 — 启动/连接 Chromium 浏览器，导航页面，操作 DOM 元素，截图，执行 JavaScript，鼠标键盘控制
---

# Browser Automation

纯 CDP（Chrome DevTools Protocol）浏览器自动化，不依赖事件总线、LLM 或 agent 框架。支持本地启动 Chrome/Chromium 或连接现有浏览器实例。

## 依赖

```bash
source .venv/bin/activate
uv add cdp-use pydantic httpx psutil
```

## 快速开始

```python
import asyncio
from src.browser import BrowserConfig, BrowserSession

async def main():
    config = BrowserConfig(headless=True)
    session = BrowserSession(config=config)

    await session.start()
    await session.navigate("https://example.com")

    page = await session.get_current_page()
    title = await page.evaluate("document.title")
    print(f"页面标题: {title}")

    await session.stop()

asyncio.run(main())
```

## 核心 API

### BrowserSession — 浏览器生命周期

```python
config = BrowserConfig(
    headless=True,                    # 无头模式
    window_size={"width": 1280, "height": 720},
    disable_security=False,
)

session = BrowserSession(config=config)
await session.start()                 # 启动或连接浏览器
await session.navigate("https://xxx.com", wait_until="load")
await session.stop()                  # 停止并清理
```

| 方法 | 说明 |
|------|------|
| `navigate(url, new_tab=False, wait_until='load')` | 导航到 URL，wait_until: `load` / `domcontentloaded` / `networkidle` / `commit` |
| `get_current_page()` | 返回当前页面的 Page 对象 |
| `get_pages()` | 返回所有页面的 Page 列表 |
| `get_tabs()` | 返回 `[{target_id, url, title}]` |
| `switch_to_tab(target_id)` | 切换标签页 |
| `close_tab(target_id)` | 关闭标签页 |
| `screenshot(path, full_page=False, format='png')` | 截图，返回 bytes |
| `evaluate(expression)` | 在当前页面执行 JS |
| `get_cookies()` / `clear_cookies()` | Cookie 管理 |
| `set_viewport(width, height)` | 设置视口 |

```python
# 连接已有浏览器（先在终端启动：chrome --remote-debugging-port=9222）
config = BrowserConfig(cdp_url="http://localhost:9222")
session = BrowserSession(config=config)
await session.start()
```

### Page — 页面操作

```python
page = await session.get_current_page()

# 导航与历史
await page.navigate("https://example.com")
await page.reload()
await page.go_back()
await page.go_forward()

# 元素查询
el = await page.query_selector("h1")          # 单个元素 或 None
els = await page.query_selector_all("a")      # 元素列表
el = await page.wait_for_selector(".modal", timeout=10.0)

# JS 执行
result = await page.evaluate("document.title")
result = await page.evaluate("(el) => el.textContent", arg)

# 页面内容
html = await page.get_html()
text = await page.get_text()

# 截图
data = await page.screenshot("page.png", full_page=True, format="jpeg", quality=80)

# 滚动
await page.scroll_to_bottom()
await page.scroll_to_top()
pos = await page.get_scroll_position()    # {"x": 0, "y": 1200}

# 键盘
await page.type_text("hello")
await page.press_key("Enter")

# 尺寸
dims = await page.get_page_dimensions()       # {"width": 1920, "height": 8000}
view = await page.get_viewport_dimensions()   # {"width": 1280, "height": 720}
```

### Element — DOM 元素交互

```python
el = await page.query_selector("#search-input")

# 点击
await el.click()                    # 左键单击（自动计算可点击中心）
await el.double_click()
await el.right_click()
await el.hover()

# 输入
await el.type("hello world", clear=True)
await el.press("Enter")

# 信息获取
text = await el.get_text()
html = await el.get_inner_html()
tag = await el.get_tag_name()
attrs = await el.get_attributes()           # {"id": "foo", "class": "bar"}
val = await el.get_attribute("href")

# 状态检查
visible = await el.is_visible()
enabled = await el.is_enabled()
box = await el.get_bounding_box()           # {"x": 100, "y": 200, "width": 300, "height": 40}

# 元素截图
data = await el.screenshot_element("element.png")

# 表单操作
await el.select_option(value="beijing")     # <select>
await el.upload_file("/path/to/file.pdf")   # <input type="file">
```

### Mouse — 坐标级鼠标控制

```python
mouse = await page.mouse

await mouse.move(500, 300)
await mouse.click(500, 300)                 # 左键单击
await mouse.double_click(500, 300)
await mouse.right_click(500, 300)

# 拖拽（10 步平滑拖动）
await mouse.drag(from_x=100, from_y=200, to_x=400, to_y=200, steps=10)

# 滚动（优先 wheel 事件，回退 JS scrollBy）
await mouse.scroll(delta_y=300)             # 向下滚动 300px
await mouse.scroll(delta_x=-100, delta_y=0) # 向左滚动 100px
```

## 实用场景

### 1. 截图并提取页面标题

```python
async def capture_page(url: str, output: str = "screenshot.png"):
    session = BrowserSession(BrowserConfig(headless=True))
    try:
        await session.start()
        await session.navigate(url)
        title = await session.evaluate("document.title")
        await session.screenshot(output, full_page=True)
        print(f"[{title}] 截图已保存至 {output}")
    finally:
        await session.stop()
```

### 2. 表单填写与提交

**输入框交互策略（重要）**

`Element.type()` 内部调用 `DOM.focus` 聚焦元素。部分网站（百度等）的输入框会返回 `RuntimeError: Element is not focusable`。因此输入框填值分成两步：

| 步骤 | 优先方式 | 说明 |
|------|----------|------|
| **聚焦** | `el.scroll_into_view()` + `el.click()` | click 比 DOM.focus 更通用，大多数网站都能响应 |
| **设值** | **`page.evaluate()` JS 设值**（优先推荐） | 绕过 CDP focus 限制，同时触发 `input` 事件让框架感知变化 |
| 设值（备选） | `el.type(text)` | 仅在 `DOM.focus` 可用的站点使用 |

**为什么优先 JS 设值：**
1. `el.type()` 逐字符 dispatchKeyEvent，慢且依赖 focus 成功
2. JS 设值直接 `input.value = 'xxx'`，一次调用完成
3. 通过 `dispatchEvent(new Event('input', {bubbles:true}))` 触发 React/Vue 等框架的响应
4. 不依赖 CDP focus 的可用性

```python
async def search_form(keyword: str, *, input_selector: str, btn_selector: str = None):
    """通用搜索表单填值模板。"""
    session = BrowserSession(BrowserConfig(headless=False))
    try:
        await session.start()
        await session.navigate("https://example.com")
        page = await session.get_current_page()

        # Step 1: 聚焦输入框（scrollIntoView + click）
        input_el = await page.query_selector(input_selector)
        if input_el:
            await input_el.scroll_into_view()
            await input_el.click()
            await asyncio.sleep(0.3)

        # Step 2: JS 设值（最可靠）
        import json
        await page.evaluate(f"""
            (function() {{
                var el = document.querySelector({json.dumps(input_selector)});
                if (el) {{
                    el.value = {json.dumps(keyword)};
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    return 'ok';
                }}
                return 'not found';
            }})()
        """)

        # Step 3: 提交（优先点按钮 → 按回车 → JS submit）
        if btn_selector:
            btn = await page.query_selector(btn_selector)
            if btn:
                await btn.click()
        else:
            await page.press_key("Enter")

        await asyncio.sleep(3)
        await session.screenshot("search_result.png")
        return page
    finally:
        await session.stop()

# 百度搜索示例
# search_form("ai news", input_selector="#kw", btn_selector="#su")
```

### 3. 提取页面所有链接

```python
async def extract_links(url: str) -> list[dict]:
    session = BrowserSession(BrowserConfig(headless=True))
    try:
        await session.start()
        await session.navigate(url)
        page = await session.get_current_page()

        links = await page.query_selector_all("a[href]")
        result = []
        for link in links:
            text = await link.get_text()
            href = await link.get_attribute("href")
            if text.strip() and href:
                result.append({"text": text.strip(), "href": href})
        return result
    finally:
        await session.stop()
```

### 4. 多标签页操作

```python
async def open_multiple_tabs():
    session = BrowserSession(BrowserConfig(headless=True))
    try:
        await session.start()

        # 打开多个标签页
        await session.navigate("https://example.com", new_tab=True)
        await session.navigate("https://httpbin.org", new_tab=True)

        tabs = await session.get_tabs()
        for i, tab in enumerate(tabs):
            print(f"Tab {i}: {tab['title']} — {tab['url']}")

        # 切换到第一个标签页
        if tabs:
            await session.switch_to_tab(tabs[0]["target_id"])
            print(f"当前 URL: {await session.get_current_url()}")

    finally:
        await session.stop()
```

## 输入框交互深度指南

### 已知限制

`Element.type()` 内部调用 `DOM.focus`，部分网站的输入框会报错：
```
RuntimeError: {'code': -32000, 'message': 'Element is not focusable'}
```
常见于百度 (`#kw`)、使用了自定义组件封装原生 input 的站点。

### 决策流程

```
输入框填值
  ├─ 普通站点（Google、GitHub 等）→ el.type(text) 直接可用
  └─ 复杂站点（百度、SPA 等）
       ├─ Step 1: el.scroll_into_view() + el.click()  聚焦
       ├─ Step 2: page.evaluate(js设值)               填值（推荐）
       └─ Step 3: btn.click() / press_key("Enter")    提交
```

### JS 设值模板

```python
import json

keyword = "hello world"   # 可能含单引号，用 json.dumps 转义
selector = "#input-id"

await page.evaluate(f"""
    (function() {{
        var el = document.querySelector({json.dumps(selector)});
        if (el) {{
            el.value = {json.dumps(keyword)};
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            return 'ok';
        }}
        return null;
    }})()
""")
```

> **为什么用 `json.dumps`：** 当 keyword 含单引号（如 `it's`）时，Python f-string 直接拼接会破坏 JS 语法，`json.dumps` 自动转义为 `"it's"`。

## 注意事项

- 首次运行会创建临时 `user_data_dir`，如需持久化 cookie/登录态，请指定 `BrowserConfig(user_data_dir="/path/to/profile")`
- `headless=True` 时截图/渲染行为与有头模式可能不同，生产截图建议开启 `deterministic_rendering=True`
- 元素点击会自动尝试 3 种方式计算可点击中心（ContentQuads → BoxModel → getBoundingClientRect），大部分场景无需手动处理
- 连接已有浏览器时需先手动启动 Chrome：`google-chrome --remote-debugging-port=9222`
- 同域导航默认 3s 超时，跨域 8s，可通过 `_navigate_and_wait` 的 timeout 参数调整
- **导航超时警告不影响使用**：部分网站（如百度）可能触发 `Page readiness timeout` 日志，页面实际已可用，后续 query_selector / evaluate 不受影响
