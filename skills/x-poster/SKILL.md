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

## 完整流程

```
┌─────────────────────────────────────────────────────────┐
│  Step 1: 聚合新闻                                        │
│  连接已有 Chrome → JS 提取 5 个新闻源标题 → 去噪保存       │
├─────────────────────────────────────────────────────────┤
│  Step 2: 生成帖子                                        │
│  分析话题热度 → 挑选 3-5 个故事 → 人类语气重写             │
│  （控制在 280 字符内，X 字数限制）                         │
├─────────────────────────────────────────────────────────┤
│  Step 3: 代理发帖                                        │
│  启动代理 Chrome → 导航 X → 检查登录 → type_text 输入     │
│  → 等待按钮激活 → 点击发送                                │
└─────────────────────────────────────────────────────────┘
```

## Step 1: 新闻聚合

### 新闻源配置

```python
NEWS_SOURCES = [
    ("The Verge AI", "https://www.theverge.com/ai-artificial-intelligence", "theverge.com"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/", "techcrunch.com"),
    ("Wired AI", "https://www.wired.com/tag/artificial-intelligence/", "wired.com"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/", "venturebeat.com"),
    ("Ars Technica AI", "https://arstechnica.com/ai/", "arstechnica.com"),
]
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

### 多源聚合

```python
async def aggregate_ai_news(session):
    all_results = {}
    for name, url, url_pattern in NEWS_SOURCES:
        print(f"\n📰 {name}")
        try:
            items = await extract_headlines(session, url, url_pattern, wait=4.0)
            clean_items = [it for it in items if not is_noise(it["title"])]
            all_results[name] = clean_items
            print(f"   ✅ {len(clean_items)} 篇文章")
            for item in clean_items[:3]:
                print(f"      • {item['title'][:90]}")
        except Exception as e:
            print(f"   ❌ 错误: {e}")
            all_results[name] = {"error": str(e)}
    return all_results
```

## Step 2: 生成人类风格帖子

### ⚠️ 关键约束
- **X 字数限制 280 字符**（中文字符算 1 个）
- 内容要像真人说话，不能有 AI 味（避免 "在当今时代"、"综上所述" 等）
- 挑 3-5 个最具话题性的故事，不要简单罗列新闻标题
- 加个人观点/吐槽引发讨论

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

### 帖子生成模板（人类语气）

```python
def generate_post_content(stories):
    """根据新闻生成人类风格的帖子。

    策略：不要翻译新闻标题，要用自己的话重写，加观点、吐槽、上下文。
    控制 280 字符以内。
    """
    # 分析话题后手动挑选 3-5 个最有料的故事
    # 用口语化表达，不要用 AI 腔

    post = (
        "今天 AI 圈几个事：\n\n"
        "OpenAI 要做硬件了——一个会动的 ChatGPT 音箱，没屏幕。"
        "软件还没整明白就搞硬件\n\n"
        "Grok 编程工具把用户代码库默认上传云端，"
        "用AI写代码的兄弟注意检查\n\n"
        "Meta 被起诉用AI做裁员，还把你IG照片拿去训练AI\n\n"
        "纽约州禁建新数据中心一年\n\n"
        "一边狂飙一边一地鸡毛，你们怎么看？"
    )
    # 上面是精简版 ~160 字符，留足余量
    return post

# ⚠️ 帖子风格指南：
# ✅ 好的开头：
#   "今天 AI 圈几个事挺值得聊的："
#   "刷了一圈今天的 AI 新闻，几个有意思的点："
#   "早上刷了一遍 AI 资讯，说几个值得关注的："
#
# ❌ 避免：
#   "在当前的 AI 发展格局下……"（AI 腔）
#   "综上所述……"（AI 腔）
#   "值得关注的是……"（太正式）
#   直接用编号列表堆新闻标题（缺少个人加工）
#
# ✅ 好的结尾（引发互动）：
#   "你们怎么看？👇"
#   "你们觉得哪个方向最值得关注？"
#   "有什么我漏掉的吗？评论区补充"
```

## Step 3: X (Twitter) 发帖

### ⚠️ 关键踩坑：React contenteditable 不能用 JS 设值

X 的发帖框是 `<div contenteditable="true">`，React 控制的。用 `editor.textContent = 'xxx'` 虽然文字能显示，但 **React 内部状态未更新，发送按钮始终 disabled**。

**必须用 `page.type_text()` 真实键盘事件逐字符输入！**

### 完整发帖流程

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
        # 检查是否在登录页
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
    await page.press_key("Meta+a")      # 全选
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
            # contenteditable 中 Shift+Enter 换行
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
| X 超字数发不出 | 375 字符按钮灰 | X 限制 280 字符 | 精简到 ~160 字符，用口语化短句 |
| VentureBeat 大量噪音 | "Credit: VentureBeat made with Midjourney" | 图片 credit 文本在 `<a>` 标签 | noise_words 加 "Credit:" |
| 新闻站导航超时 | `Page readiness timeout` 警告 | CDP loadEventFired 慢 | 无害，忽略，sleep 等渲染即可 |
