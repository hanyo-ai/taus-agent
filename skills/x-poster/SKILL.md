---
name: x-poster
description: 从多个新闻源聚合 AI/科技新闻，生成人类风格的帖子，通过代理在 X (Twitter) 上发帖——端到端社交媒体运营流程
---

# X Poster — AI 新闻聚合 + X (Twitter) 发帖

从英文科技媒体聚合今日 AI 新闻 → 挑选话题性强的故事 → 用人类语气生成帖子 → 通过代理在 X 发帖。

## 依赖

前提：已安装 `browser-automation` skill 的依赖 + `news-aggregator` skill。

```bash
source .venv/bin/activate
```

## ⚠️ 运行环境

```bash
# 必须清除系统代理，否则 CDP websocket 连接会被代理拦截
no_proxy=* NO_PROXY=* python your_script.py
```

### 提取 + 去噪

```python
import asyncio, json
from pathlib import Path
from src.browser import BrowserConfig, BrowserSession

NOISE_WORDS = [
    "Logo", "Consent", "Brand Studio", "Product Updates",
    "Skip to", "See all", "The homepage", "Media & Entertainment",
    "Pagination", "Privacy", "Cookie", "Manage Consent",
    "opens in a new window", "linkedin", "instagram",
    "Newsletter", "Subscribe", "RSS", "Advertisement",
    "Deals", "Buy Now", "Shop Now", "Sponsored",
    # VentureBeat 特有噪音
    "Credit:", "Image credit:", "CommentsComment", "Comment Icon",
]

def is_noise(title: str) -> bool:
    if len(title) < 25:
        return True
    if any(w.lower() in title.lower() for w in NOISE_WORDS):
        return True
    return False

async def extract_headlines(session, url, url_pattern=None, min_len=25, max_len=200, wait=4.0):
    """JS 一次性提取所有 <a> 标签文本 — 避免逐元素 CDP 往返"""
    await session.navigate(url, wait_until="load")
    await asyncio.sleep(wait)  # 等 SPA 渲染

    url_filter = f'&& href.includes("{url_pattern}")' if url_pattern else ""

    js_code = f"""
    (function() {{
        var results = [];
        var seen = {{}};
        var links = document.querySelectorAll('a');
        for (var i = 0; i < links.length; i++) {{
            var a = links[i];
            var text = a.textContent.trim();
            var href = a.href;
            if (text.length > {min_len}
                && text.length < {max_len}
                {url_filter}
                && !text.startsWith('Skip')
                && !text.startsWith('See all')
                && !text.includes('Cookie')
                && !text.includes('Privacy')
                && !text.includes('Subscribe')
                && !text.includes('Newsletter')
                && !text.includes('Logo')) {{
                if (!seen[href]) {{
                    seen[href] = true;
                    results.push({{title: text, url: href}});
                }}
            }}
        }}
        return JSON.stringify(results.slice(0, 20));
    }})()
    """
    raw = await session.evaluate(js_code)
    return json.loads(raw) if raw else []
```


## Step 2: 生成人类风格帖子

### ⚠️ 关键约束
- **X 字数限制 280 字符**（中文字符算 1 个）
- **链接处理**：X 的 t.co 短链固定算 **23 字符**，所以 `文字长度 + 23 = 实际占用`（不是 URL 原始长度）
- **附链接效果更好**：帖子末尾加原文链接可引导点击，且只占用 23 字符
- 内容要像真人说话，不能有 AI 味（避免 "在当今时代"、"综上所述" 等）
- 加个人观点/吐槽引发讨论

### 字符数计算（含链接）

```python
# X 把任何 URL 一律视为 23 字符（t.co）
# https 和 http 的 URL 都算 23
# 所以实际字符数 = 纯文字长度 + 23

import re

def x_char_count(text: str) -> int:
    """计算 X 实际字符数（URL=23）"""
    urls = re.findall(r'https?://\S+', text)
    count = len(text)
    for url in urls:
        count -= len(url)     # 去掉原始 URL
        count += 23            # 换成 t.co 短链
    return count

# 示例
tweet = "帖子正文...\n\nhttps://arstechnica.com/very-long-url-here/"
assert x_char_count(tweet) <= 280  # 验证不超限
```

### 下载封面图

```bash
# 从新闻源下载封面图缓存到本地
curl -sL -o /tmp/post_cover.jpg "https://cdn.arstechnica.net/.../cover-1024x648.jpg"
```

### 话题热度分析

```python
def pick_top_stories(all_results, top_n=10):
    """从聚合结果挑选最有信息量的故事（按标题长度排序）"""
    stories = []
    for source, items in all_results.items():
        if isinstance(items, list):
            for item in items:
                stories.append({**item, "source": source})
    stories.sort(key=lambda x: len(x["title"]), reverse=True)
    return stories[:top_n]

def analyze_hot_topics(all_results):
    """按关键词统计热度"""
    all_titles = []
    for source, items in all_results.items():
        if isinstance(items, list):
            for item in items:
                all_titles.append(item['title'])

    keywords = ['OpenAI', 'GPT', 'Grok', 'Meta', 'Google', 'Anthropic',
                'Claude', 'Apple', 'robot', 'AGI', 'safety', 'lawsuit',
                'hardware', 'DeepMind', 'Siri', 'data center']
    
    for kw in keywords:
        matches = [t for t in all_titles if kw.lower() in t.lower()]
        if matches:
            print(f"🔥 {kw}: {len(matches)}篇")
```

## Step 3: X (Twitter) 发帖

### ⚠️ 关键踩坑：React contenteditable 不能用 JS 设值

X 的发帖框是 `<div contenteditable="true">`，React 控制的。用 `editor.textContent = 'xxx'` 虽然文字能显示，但 **React 内部状态未更新，发送按钮始终 disabled**。

**必须用 `page.type_text()` 真实键盘事件逐字符输入！**

### 纯文字发帖流程

```python
import asyncio, json

async def post_to_x(session, content, max_chars=280):
    """在 X 发帖。Content 必须 ≤ 280 字符。"""

    # 检查字符数
    if len(content) > max_chars:
        print(f"⚠️  内容 {len(content)} 字符，超过 {max_chars} 限制，请精简")
        return False

    await session.navigate("https://x.com/home", wait_until="load")
    await asyncio.sleep(4)

    # 1. 检查登录
    logged_in = await session.evaluate("""
        (function() {
            var el = document.querySelector('div[contenteditable="true"]');
            if (!el) el = document.querySelector('div[role="textbox"]');
            return el ? true : false;
        })()
    """)

    if not logged_in:
        url = await session.get_current_url()
        if "login" in url.lower() or "i/flow" in url.lower():
            print("⚠️  未登录，请在浏览器中手动登录 X 后重试")
            return False

    page = await session.get_current_page()

    # 2. 找到并点击发帖框
    editor = await page.query_selector('div[contenteditable="true"]')
    if not editor:
        editor = await page.query_selector('div[role="textbox"]')
    if not editor:
        print("❌ 找不到发帖框")
        return False

    await editor.click()
    await asyncio.sleep(0.3)

    # 3. 清空（如果有预填充文本）
    await page.press_key("Meta+a")
    await asyncio.sleep(0.1)
    await page.press_key("Backspace")
    await asyncio.sleep(0.2)

    # 4. ⚠️ 必须用 page.type_text() 真实键盘输入（React 才认）
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if line:
            await page.type_text(line)
            await asyncio.sleep(0.05)
        if i < len(lines) - 1:
            await page.press_key("Shift+Enter")
            await asyncio.sleep(0.05)

    print(f"✅ 已输入 {len(content)} 字符")

    # 5. 等待 React 激活发送按钮，然后点击
    for i in range(10):
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
            print("🎉 帖子已发送！")
            return True

    print("⚠️  发送按钮未激活，请手动点击")
    return False
```

### 附图片 + 链接发帖（增强版）

发布带封面图和原文链接的帖子：先用 CDP `DOM.setFileInputFiles` 注入图片，再 `type_text` 输入文字和链接。

#### ⚠️ 图片注入原理

X 的图片上传是通过 `<input type="file" accept*="image">` 实现的。直接 CDP 注入文件路径，绕过系统文件选择对话框，比模拟点击更可靠。

```python
import asyncio
from pathlib import Path

async def post_to_x_with_image(session, content: str, image_path: str, max_chars=280):
    """在 X 发帖，附带图片和原文链接。
    
    Args:
        session: BrowserSession 实例
        content: 帖子文字（末尾应包含 t.co 按 23 字符计算的原文链接）
        image_path: 本地图片路径（已通过 curl 下载）
        max_chars: 字符上限，默认 280
    """

    # X 的 URL 按 23 字符算
    import re
    urls = re.findall(r'https?://\S+', content)
    adjusted_len = len(content) - sum(len(u) for u in urls) + len(urls) * 23
    if adjusted_len > max_chars:
        print(f"⚠️  调整后 {adjusted_len} 字符，超过 {max_chars}")
        return False

    await session.navigate("https://x.com/home", wait_until="load")
    await asyncio.sleep(4)

    # 1. 检查登录
    logged_in = await session.evaluate("""(function() {
        var el = document.querySelector('div[contenteditable="true"]');
        if (!el) el = document.querySelector('div[role="textbox"]');
        return el ? true : false;
    })()""")
    if not logged_in:
        print("⚠️  未登录")
        return False

    # 2. 通过 CDP 直接注入图片文件
    cdp_session = await session._get_focused_session()
    
    # 获取 DOM 文档根节点
    doc = await cdp_session.cdp_client.send.DOM.getDocument(
        params={"depth": -1}, session_id=cdp_session.session_id
    )
    
    # 查找 file input 节点
    file_node = await cdp_session.cdp_client.send.DOM.querySelector(
        params={
            "nodeId": doc['root']['nodeId'],
            "selector": 'input[type="file"]'
        },
        session_id=cdp_session.session_id
    )
    
    if not file_node.get('nodeId'):
        # 如果 file input 还没渲染，先点一下 media 按钮触发
        print("   file input 未找到，点击 media 按钮触发...")
        await session.evaluate("""(function() {
            var btn = document.querySelector('button[aria-label*="Add"]');
            if (!btn) btn = document.querySelector('button[aria-label*="photo" i]');
            if (btn) btn.click();
        })()""")
        await asyncio.sleep(1)
        # 重新获取
        doc = await cdp_session.cdp_client.send.DOM.getDocument(
            params={"depth": -1}, session_id=cdp_session.session_id
        )
        file_node = await cdp_session.cdp_client.send.DOM.querySelector(
            params={"nodeId": doc['root']['nodeId'], "selector": 'input[type="file"]'},
            session_id=cdp_session.session_id
        )

    if file_node.get('nodeId'):
        # 🔑 关键：DOM.setFileInputFiles 直接注入文件路径
        await cdp_session.cdp_client.send.DOM.setFileInputFiles(
            params={
                "files": [str(Path(image_path).resolve())],
                "nodeId": file_node['nodeId']
            },
            session_id=cdp_session.session_id
        )
        print("✅ 图片已注入")
    else:
        print("❌ 找不到 file input")
        return False

    # 3. 等待图片上传完成
    await asyncio.sleep(4)
    preview = await session.evaluate("""(function() {
        var imgs = document.querySelectorAll('[data-testid="attachments"] img');
        return imgs.length;
    })()""")
    print(f"   图片预览: {preview} 张")

    # 4. 输入文字（含链接）— 用 type_text 逐字符输入
    page = await session.get_current_page()
    editor = await page.query_selector('div[contenteditable="true"]')
    if not editor:
        editor = await page.query_selector('div[role="textbox"]')
    await editor.click()
    await asyncio.sleep(0.3)

    lines = content.split('\n')
    for i, line in enumerate(lines):
        if line:
            await page.type_text(line)
            await asyncio.sleep(0.03)
        if i < len(lines) - 1:
            await page.press_key("Shift+Enter")
            await asyncio.sleep(0.03)

    print(f"✅ 已输入文字")

    # 5. 等待发送按钮激活并点击
    for i in range(20):
        await asyncio.sleep(0.5)
        btn_info = await session.evaluate("""(function() {
            var btn = document.querySelector('[data-testid="tweetButton"]');
            if (!btn) btn = document.querySelector('[data-testid="tweetButtonInline"]');
            return btn ? {disabled: btn.disabled} : null;
        })()""")
        if btn_info and not btn_info.get("disabled"):
            await session.evaluate("""(function() {
                var btn = document.querySelector('[data-testid="tweetButton"]');
                if (!btn) btn = document.querySelector('[data-testid="tweetButtonInline"]');
                if (btn) btn.click();
            })()""")
            print("🎉 帖子已发送！")
            return True

    print("⚠️  发送按钮未激活")
    return False
```

#### 调用示例

```python
# 帖子内容：文字 + 末尾链接（链接算 23 字符）
TWEET = """🍎 Apple 正式起诉 OpenAI

前工程师离职加入 OpenAI 后，利用认证漏洞+未归还工作笔记本，连续数周窃取硬件机密文件。

聊天记录："LOL 我发现还能访问共享文件夹 🤣"——成了呈堂证供。

Apple 称 OpenAI 已挖走 400+ 前员工，这只是"冰山一角"。

https://arstechnica.com/tech-policy/2026/07/apple-sues-openai-after-ex-engineer-allegedly-used-bug-to-steal-trade-secrets/"""

# 先下载封面图
# curl -sL -o /tmp/cover.jpg "https://cdn.arstechnica.net/.../cover.jpg"

await post_to_x_with_image(session, TWEET, "/tmp/cover.jpg")
```

#### 操作顺序（重要）

```
1. 导航到 x.com/home
2. CDP 注入图片 (DOM.setFileInputFiles)
3. 等待图片上传完成 (sleep 4s)
4. type_text 输入文字 + 链接
5. 等待发送按钮激活 → 点击
```

> **为什么先注入图片再打字？** 图片上传需要时间，先上传可以并行等待；文字输入必须在前一步完成后进行，否则 React 状态可能冲突。

### 代理配置

```python
from src.browser import BrowserConfig, BrowserSession, ProxySettings

# X 需要代理访问
config = BrowserConfig(
    headless=False,
    proxy=ProxySettings(server="http://127.0.0.1:12334"),
    window_size={"width": 1280, "height": 900},
)
session = BrowserSession(config=config)
await session.start()
# → 然后调用 post_to_x(session, content)
```

## 完整端到端脚本模板

```python
import asyncio, json, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from src.browser import BrowserConfig, BrowserSession, ProxySettings

# === 新闻源配置 ===
NEWS_SOURCES = [
    ("The Verge AI", "https://www.theverge.com/ai-artificial-intelligence", "theverge.com"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/", "techcrunch.com"),
    ("Wired AI", "https://www.wired.com/tag/artificial-intelligence/", "wired.com"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/", "venturebeat.com"),
    ("Ars Technica AI", "https://arstechnica.com/ai/", "arstechnica.com"),
]

async def main():
    print("=" * 60)
    print("📡 Step 1: 聚合 AI 新闻")
    print("=" * 60)

    # 用已有浏览器聚合新闻（不走代理，节省带宽）
    config_news = BrowserConfig(cdp_url="http://localhost:60889", headless=False)
    session_news = BrowserSession(config=config_news)
    await session_news.start()

    all_results = await aggregate_ai_news(session_news)
    stories = pick_top_stories(all_results)

    # 保存数据
    with open("/tmp/ai_news_today.json", "w", encoding="utf-8") as f:
        json.dump({"date": datetime.now().isoformat(), "sources": all_results},
                  f, ensure_ascii=False, indent=2)

    # 生成帖子（这里手动写，因为需要人类判断哪些故事有话题性）
    post = "今天 AI 圈几个事：\n\n...(你的帖子内容)...\n\n你们怎么看？"
    print(f"\n📝 帖子 ({len(post)} 字符):\n{post}")

    # === Step 2: 代理发帖 ===
    print(f"\n{'='*60}")
    print("🐦 Step 2: 代理发帖到 X")
    print("=" * 60)

    config_x = BrowserConfig(
        headless=False,
        proxy=ProxySettings(server="http://127.0.0.1:12334"),
        window_size={"width": 1280, "height": 900},
    )
    session_x = BrowserSession(config=config_x)
    await session_x.start()
    await post_to_x(session_x, post)

    # 保持浏览器打开让用户确认
    print("\n⏳ 浏览器保持打开，确认后关闭")

if __name__ == "__main__":
    asyncio.run(main())
```

## 踩坑总结

| 问题 | 现象 | 原因 | 解决 |
|------|------|------|------|
| CDP 连接 502 | `JSONDecodeError` | httpx 走系统代理 | `trust_env=False` + `no_proxy=*` |
| websocket ImportError | `python-socks required` | websockets 走 SOCKS 代理 | `no_proxy=* NO_PROXY=*` |
| X 发帖按钮一直 disabled | JS 设值后文字可见但按钮灰 | React contenteditable 不认 JS 设值 | `page.type_text()` 真实键盘输入 |
| X 换行不生效 | `press_key("Enter")` 直接提交 | contenteditable 换行是 Shift+Enter | `page.press_key("Shift+Enter")` |
| X 超字数发不出 | 375 字符按钮灰 | X 限制 280 字符 | 精简到 ~160 字符，末尾加链接（t.co=23字符） |
| VentureBeat 大量噪音 | "Credit: VentureBeat made with Midjourney" | 图片 credit 文本在 `<a>` 标签 | noise_words 加 "Credit:" |
| 新闻站导航超时 | `Page readiness timeout` 警告 | CDP loadEventFired 慢 | 无害，忽略，sleep 等渲染即可 |
| 图片注入：file input 未找到 | `nodeId` 为 0 | X 懒加载，file input 初始不在 DOM | 先点击 media 按钮触发渲染，再 querySelector |
| 图片注入：上传后预览不出现 | attachments 为空 | 上传需要 3-4s，太快就打字会冲突 | 注入后 sleep 4s 等上传完成，再输入文字 |
| 链接太长超字符 | 原始 URL 100+ 字符 | 误以为 URL 全字符计数 | X 的 t.co 短链固定 23 字符，用 `x_char_count()` 正确计算 |
