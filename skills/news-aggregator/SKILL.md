---
name: news-aggregator
description: 从多个新闻站点聚合信息——使用 CDP 浏览器自动化批量导航、JS 提取结构化数据、去噪清洗、生成摘要报告
---

# News Aggregator

从多个新闻站点（The Verge、TechCrunch、Wired、VentureBeat 等）批量抓取标题和链接，清洗噪音后聚合为结构化报告。

## 依赖

前提：已安装 `browser-automation` skill 的依赖。

```bash
source .venv/bin/activate
```

## 核心模式：通用标题提取器

```python
import asyncio, json
from src.browser import BrowserConfig, BrowserSession

async def extract_headlines(
    session: BrowserSession,
    url: str,
    url_pattern: str | None = None,
    min_len: int = 25,
    max_len: int = 200,
    wait: float = 4.0,
) -> list[dict]:
    """
    导航到页面 → 等待 JS 渲染 → JS 提取所有符合条件的 <a> 标签文本。
    返回 [{"title": "...", "url": "..."}, ...]
    """
    await session.navigate(url, wait_until="load")
    await asyncio.sleep(wait)  # 关键：等待 SPA/动态内容渲染

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
                && !text.startsWith('The homepage')
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

## 完整示例：多源聚合

```python
import asyncio, json
from datetime import datetime
from src.browser import BrowserConfig, BrowserSession

async def aggregate_news(sources: list[tuple[str, str, str | None]]):
    """
    sources: [(name, url, url_pattern), ...]
    """
    config = BrowserConfig(
        headless=True,
        executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        window_size={"width": 1280, "height": 900},
    )
    session = BrowserSession(config=config)
    all_results = {}

    try:
        await session.start()

        for name, url, url_pattern in sources:
            print(f"--- {name} ---")
            try:
                items = await extract_headlines(session, url, url_pattern)
                items = [it for it in items if not is_noise(it["title"])]
                all_results[name] = items
                print(f"  {len(items)} articles")
                for item in items[:3]:
                    print(f"  • {item['title'][:80]}")
            except Exception as e:
                print(f"  Error: {e}")
                all_results[name] = {"error": str(e)}

    finally:
        await session.stop()

    output = "/tmp/news_aggregated.json"
    with open(output, "w", encoding="utf-8") as f:
        json.dump({"date": datetime.now().isoformat(), "sources": all_results},
                  f, ensure_ascii=False, indent=2)

    return all_results


def is_noise(title: str) -> bool:
    """过滤导航链接、品牌名、banner 文字等噪音。"""
    noise_words = [
        "Logo", "Consent", "Brand Studio", "Product Updates",
        "Skip to", "See all", "The homepage", "Media & Entertainment",
        "Pagination", "Privacy", "Cookie", "Manage Consent",
        "opens in a new window", "linkedin", "instagram",
        "Newsletter", "Subscribe", "RSS",
    ]
    if len(title) < 25:
        return True
    if any(w in title for w in noise_words):
        return True
    return False


# 使用示例
asyncio.run(aggregate_news([
    ("The Verge AI", "https://www.theverge.com/ai-artificial-intelligence", "theverge.com"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/", "techcrunch.com"),
    ("Wired AI", "https://www.wired.com/tag/artificial-intelligence/", "wired.com"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/", "venturebeat.com"),
]))
```

## 关键经验

### 1. JS 提取比 DOM API 可靠得多

❌ **不要用** `page.query_selector_all()` + `el.get_text()` — 慢、有 `executionContextId` 反序列化 bug、逐元素 CDP 往返开销大。

✅ **用 `session.evaluate(js_iife)`** — 一次 CDP 往返，在浏览器进程内完成所有循环和过滤，JSON.stringify 把结果带回来。

```python
# ✅ 正确模式
js = """
(function() {
    var results = [];
    document.querySelectorAll('a').forEach(function(a) {
        var t = a.textContent.trim();
        if (t.length > 20 && t.length < 200) {
            results.push({title: t, url: a.href});
        }
    });
    return JSON.stringify(results);
})()
"""
data = json.loads(await session.evaluate(js))
```

### 2. 导航超时不影响使用

新闻站常返回 200 但 CDP 的 `Page.loadEventFired` 很长时间不触发，日志里出现：
```
Page readiness timeout (8.0s) for https://...
```
**这是无害警告** — 页面实际已可用，后续 `evaluate` / 提取不受影响。

### 3. 等待 SPA 渲染

SPA / 动态加载站点需要 `asyncio.sleep(3~5)` 等 React/Vue/Web Component 渲染完，否则提取到的是初始 HTML 骨架。

```python
await session.navigate(url, wait_until="load")
await asyncio.sleep(4)  # 等客户端 JS 渲染
```

### 4. url_pattern 过滤外链

新闻站正文链接有域名规律，不加过滤会混入大量外部广告和社交链接：

```python
extract_headlines(session, url, url_pattern="theverge.com")  # 只抓自家文章
```

### 5. Python 侧二次去噪

JS 侧过滤不完善，拿到数据后再用 Python 过一遍站点特有噪音词：

```python
def is_noise(title: str) -> bool:
    return any(w in title for w in [
        "Logo", "Consent", "Cookie", "Skip to", "See all",
        "Newsletter", "Subscribe", "Brand Studio",
    ]) or len(title) < 25
```

### 6. 浏览器实例复用

一次 `session.start()` 跑所有源，不要每个源重启浏览器（Chrome 冷启动 ~2s）：

```python
# ✅ 好 — 复用 session
await session.start()
for url in urls:
    await session.navigate(url)
    data = await session.evaluate(...)
await session.stop()

# ❌ 差 — 每个源重启 Chrome
for url in urls:
    s = BrowserSession(...)
    await s.start()  # 逐次冷启动，浪费 2s+
    ...
```

## 已知问题与对策

| 站点 | 问题 | 对策 |
|------|------|------|
| Google News | 导航超时严重，Shadow DOM 渲染 | 不用 CDP，用 RSS 或 News API |
| 百度新闻 | 验证码拦截 + 页面结构频繁变 | 非必要不加入中文源 |
| 路透社 | 需 JS 渲染，可能检测 headless | 直接导航 `/technology/artificial-intelligence/` |
| MIT Tech Review | 文章链接动态分页加载 | 首次提取量少属正常 |
| Ars Technica | 同上 | 同上 |

## 生成 Markdown 摘要

```python
def render_markdown(results: dict) -> str:
    lines = [f"# 新闻聚合 — {datetime.now().strftime('%Y-%m-%d')}", ""]
    for source, items in results.items():
        if not isinstance(items, list) or not items:
            continue
        clean = [it for it in items if not is_noise(it["title"])]
        lines.append(f"## {source} ({len(clean)} 篇)")
        for i, item in enumerate(clean[:8], 1):
            lines.append(f"{i}. [{item['title']}]({item['url']})")
        lines.append("")
    return "\n".join(lines)
```

## 适用场景

- "搜索今天的 AI 新闻"
- "聚合 XX 领域的最新报道"
- 批量从多个博客/资讯站提取文章列表
- 构建个人日报 / RSS 替代方案
