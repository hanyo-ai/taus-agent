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

## ⚠️ 运行环境：避免系统代理干扰

如果系统配置了全局代理（SOCKS/HTTP），CDP 和 websockets 连接 `localhost` 也会被代理拦截导致失败。**运行任何 browser 脚本时必须清除代理环境变量：**

```bash
no_proxy=* NO_PROXY=* python your_script.py
```

同时，`src/browser/session.py` 中 httpx 客户端已配置 `trust_env=False`（跳过系统代理），不要移除该参数。

**常见报错与原因：**

| 报错 | 原因 |
|------|------|
| `JSONDecodeError: Expecting value` 连接 CDP 时 | httpx 走了系统 HTTP 代理，返回 502 |
| `ImportError: python-socks is required` | websockets 走了系统 SOCKS 代理 |
| 解决办法 | `no_proxy=* NO_PROXY=* python ...` |

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
    # 代理（访问受限站点时使用）
    proxy=ProxySettings(server="http://127.0.0.1:12334"),
)

session = BrowserSession(config=config)
await session.start()                 # 启动或连接浏览器
await session.navigate("https://xxx.com", wait_until="load")
await session.stop()                  # 停止并清理
```

| 方法 | 说明 |
|------|------|
| `navigate(url, new_tab=False, wait_until='load')` | 导航到 URL，wait_until: `load` / `domcontentloaded` / `networkidle` / `commit` |
| `get_current_url()` | 返回当前页面 URL |
| `get_current_page()` | 返回当前页面的 Page 对象 |
| `get_pages()` | 返回所有页面的 Page 列表 |
| `get_tabs()` | 返回 `[{target_id, url, title}]` |
| `switch_to_tab(target_id)` | 切换标签页 |
| `close_tab(target_id)` | 关闭标签页 |
| `screenshot(path, full_page=False, format='png')` | 截图，返回 bytes |
| `evaluate(expression)` | 在当前页面执行 JS |
| `get_cookies()` / `clear_cookies()` | Cookie 管理 |
| `set_viewport(width, height)` | 设置视口 |

### `wait_until` 策略选择

`navigate()` 内部通过轮询 `document.readyState` 来判断页面是否加载完成。不同站点需用不同策略：

| wait_until | 含义 | 适用场景 | 超时 |
|------------|------|----------|------|
| `load` | `readyState === 'complete'` | 传统 SSR 页面（如 example.com） | 同域 3s / 跨域 8s |
| `domcontentloaded` | `readyState` 为 `interactive` 或 `complete` | 大部分现代网站 | 同上 |
| `commit` | 导航请求已提交，不等待 readyState | **SPA/持续轮询站点（X/Twitter、小红书等）** | 20s（仅导航请求） |
| `networkidle` | （暂未实现，预留） | — | — |

**⚠️ X/Twitter、小红书等 SPA 必须用 `commit`**：这类站点有持续的长轮询/WebSocket 连接，`document.readyState` 可能永远不会变为 `complete`（或需要非常久），导致 `load`/`domcontentloaded` 必然超时。

```python
# ❌ X.com 上这两个都会超时
await session.navigate("https://x.com/home", wait_until="load")             # 8s 超时
await session.navigate("https://x.com/home", wait_until="domcontentloaded") # 3s 超时

# ✅ 正确：用 commit 跳过 readyState 等待，然后手动轮询页面内容
await session.navigate("https://x.com/home", wait_until="commit")

# 手动等待页面就绪（轮询标题或关键元素）
for i in range(15):
    await asyncio.sleep(1)
    title = await session.evaluate("document.title")
    if title and title != "about:blank":  # 出现了有意义的标题
        print(f"页面就绪: {title}")
        break
else:
    print("页面加载超时，但可能需要检查网络/代理")
```

```python
# 推荐方式：连接已有 Chrome（速度最快，共享原生 profile）
# 先手动启动 Chrome：
#   arch -arm64 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
#     --remote-debugging-port=9222 \
#     --user-data-dir="$HOME/.taus-browser-profile" \
#     --profile-directory="Default" \
#     --window-size=1280,900 \
#     --proxy-server="socks5://127.0.0.1:12334"
config = BrowserConfig(cdp_url="http://localhost:9222")
session = BrowserSession(config=config)
await session.start()

# 查找代理浏览器端口
# lsof -i -P -n | grep chrome | grep LISTEN  或 
# ps aux | grep "remote-debugging-port" | grep -v grep
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

# 键盘 — type_text 模拟逐字符输入，press_key 按特殊键
await page.type_text("hello")
await page.press_key("Enter")
await page.press_key("Shift+Enter")       # 换行（contenteditable 中）
await page.press_key("Meta+a")            # Mac 全选
await page.press_key("Backspace")

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

# 输入 — 逐字符 keypress，站点兼容性最好
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

## 输入框交互深度指南

### 策略选择：JS 设值 vs 真实键盘输入

| 方式 | 适用场景 | 不适用场景 |
|------|----------|------------|
| `page.evaluate()` JS 设值 + dispatchEvent | 原生 `<input>` / `<textarea>` | React contenteditable div（不会激活框架状态） |
| `page.type_text()` 真实键盘事件 | **所有场景**，尤其是 React/Vue SPA | 速度稍慢 |
| `el.type()` CDP dispatchKeyEvent | 普通站点 | 百度等（`Element is not focusable`） |

### ⚠️ React contenteditable 的坑（X/Twitter、小红书等）

**JS 设值不会激活 React 的提交按钮。** X (Twitter) 发帖框是 `<div contenteditable="true">`，用 `editor.textContent = 'xxx'` + `dispatchEvent('input')` 虽然能看到文字，但 React 内部状态未更新，发送按钮始终 disabled。

```python
# ❌ 错误 — X 发帖按钮仍然 disabled
await page.evaluate(f"""
    var editor = document.querySelector('div[contenteditable="true"]');
    editor.textContent = {json.dumps(content)};
    editor.dispatchEvent(new Event('input', {{ bubbles: true }}));
""")

# ✅ 正确 — 用 page.type_text() 逐字符输入
page = await session.get_current_page()
editor = await page.query_selector('div[contenteditable="true"]')
await editor.click()
await asyncio.sleep(0.3)

lines = content.split('\n')
for i, line in enumerate(lines):
    if line:
        await page.type_text(line)
    if i < len(lines) - 1:
        await page.press_key("Shift+Enter")  # contenteditable 中换行
```

### 已知限制

`Element.type()` 内部调用 `DOM.focus`，部分网站的输入框会报错：
```
RuntimeError: {'code': -32000, 'message': 'Element is not focusable'}
```
常见于百度 (`#kw`)、使用了自定义组件封装原生 input 的站点。

### 决策流程

```
输入框填值
  ├─ 需要激活 React 按钮（X、小红书等）→ page.type_text() 真实键盘输入
  ├─ 原生 <input>/<textarea> → page.evaluate() JS 设值（快）
  └─ 普通站点 → el.type(text)
```

### JS 设值模板（仅用于原生 input/textarea）

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

## ⚠️ macOS ARM64：避免 Rosetta 模拟（否则慢 10 倍）

如果 shell 跑在 Rosetta (x86_64) 下，直接启动 Chrome 会使用 x64 版本经由 Rosetta 模拟运行，**页面加载会从 1-2s 变成 10-12s**。

`session.py` 的 `_launch_browser` 已内置检测：若 `sys.platform == 'darwin'` 且 `platform.machine() == 'arm64'`，自动在命令行前插入 `arch -arm64`，强制使用原生 ARM Chrome。

**手动验证：**
```bash
sysctl -n sysctl.proc_translated  # 输出 1 = 当前在 Rosetta 下
```

如果手动启动 Chrome（用于 `cdp_url` 连接），务必加 `arch -arm64`：
```bash
arch -arm64 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.taus-browser-profile"
```

## 代理配置

需要代理访问受限站点时，通过 `ProxySettings` 配置：

```python
from src.browser import BrowserConfig, BrowserSession, ProxySettings

config = BrowserConfig(
    headless=False,
    # ⚠️ 本机 Hiddify 使用 SOCKS5 代理，HTTP 代理对部分站点无效
    proxy=ProxySettings(server="socks5://127.0.0.1:12334"),
    window_size={"width": 1280, "height": 900},
)
session = BrowserSession(config=config)
await session.start()
```

代理只影响 Chrome 浏览器的网络请求，不影响 CDP 连接（CDP 是 localhost 直连）。

**已知代理细节：**
- Hiddify 端口 `12334`（SOCKS5 + HTTP），推荐用 `socks5://`
- ClashX Pro 端口 `7890`/`9090`（HTTP）
- 国内站点不要走代理，走 `--proxy-server` 时 Chrome 一般会自动对国内 IP 直连

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

### 2. X (Twitter) 发帖完整流程

```python
async def post_to_x(session: BrowserSession, content: str) -> bool:
    """在 X 上发帖。Content 需 ≤ 280 字符。"""
    import json

    await session.navigate("https://x.com/home", wait_until="commit")
    # 手动等待页面就绪（X 有持续长轮询，readyState 永远不会 complete）
    for _ in range(10):
        await asyncio.sleep(1)
        title = await session.evaluate("document.title")
        if title and title != "about:blank":
            break

    # 检查登录状态
    logged_in = await session.evaluate("""
        (function() {
            var el = document.querySelector('div[contenteditable="true"]');
            return el ? true : false;
        })()
    """)
    if not logged_in:
        print("未登录，请在浏览器中手动登录后重试")
        return False

    page = await session.get_current_page()

    # 点击发帖框
    editor = await page.query_selector('div[contenteditable="true"]')
    if not editor:
        editor = await page.query_selector('div[role="textbox"]')
    if not editor:
        print("找不到发帖框")
        return False

    await editor.click()
    await asyncio.sleep(0.3)

    # 清空已有内容
    await page.press_key("Meta+a")
    await asyncio.sleep(0.1)
    await page.press_key("Backspace")
    await asyncio.sleep(0.2)

    # 逐行输入（真实键盘事件，React 才能识别）
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if line:
            await page.type_text(line)
            await asyncio.sleep(0.05)
        if i < len(lines) - 1:
            await page.press_key("Shift+Enter")
            await asyncio.sleep(0.05)

    # 等待发送按钮激活
    for _ in range(10):
        await asyncio.sleep(0.5)
        btn_info = await session.evaluate("""
            (function() {
                var btn = document.querySelector('[data-testid="tweetButton"]');
                if (!btn) btn = document.querySelector('[data-testid="tweetButtonInline"]');
                return btn ? {disabled: btn.disabled} : null;
            })()
        """)
        if btn_info and not btn_info["disabled"]:
            await session.evaluate("""
                (function() {
                    var btn = document.querySelector('[data-testid="tweetButton"]');
                    if (!btn) btn = document.querySelector('[data-testid="tweetButtonInline"]');
                    if (btn) btn.click();
                })()
            """)
            print("帖子已发送")
            return True

    print("按钮未激活，请手动点击发送")
    return False
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

## 注意事项

- **macOS ARM64 必须 `arch -arm64`**：Rosetta x64 模拟导致页面加载慢 10 倍，`session.py` 已自动处理
- 首次运行会创建临时 `user_data_dir`，如需持久化 cookie/登录态，请指定 `BrowserConfig(user_data_dir="/path/to/profile")`
- **推荐持久化 profile**：`~/.taus-browser-profile`，从原生 Chrome Profile 复制而来，含 cookie/扩展/缓存
- `headless=True` 时截图/渲染行为与有头模式可能不同，生产截图建议开启 `deterministic_rendering=True`
- 元素点击会自动尝试 3 种方式计算可点击中心（ContentQuads → BoxModel → getBoundingClientRect），大部分场景无需手动处理
- 连接已有浏览器时需先手动启动 Chrome，**必须指定 `--user-data-dir`**（Chrome 拒绝在默认 profile 开调试端口）
- **导航等待改用 `document.readyState` 轮询**：lifecycle events 不可靠，`_navigate_and_wait` 现已直接用 CDP `Runtime.evaluate` 轮询
- 同域导航默认 3s 超时，跨域 8s，可通过 `_navigate_and_wait` 的 timeout 参数调整
- **运行前清除代理**：`no_proxy=* NO_PROXY=* python ...` 避免系统代理干扰 CDP/websocket 连接
- **React contenteditable 必须真实键盘输入**：`page.type_text()` / `page.press_key()`，JS 设值不会激活框架状态
- **SPA/长轮询站点用 `wait_until='commit'`**：X/Twitter、小红书等有持续 WebSocket/长轮询的站点，`readyState` 永远不会 `complete`，`load`/`domcontentloaded` 必然超时。用 `commit` 跳过等待，然后手动轮询 `document.title` 或关键 DOM 元素确认页面就绪
