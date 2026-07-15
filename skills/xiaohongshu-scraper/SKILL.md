---
name: xiaohongshu-scraper
description: 从小红书（RED）搜索/聚合笔记——连接已有 Chrome 使用登录态，通过 CDP 提取 section.note-item 结构化数据，支持滚动加载、去噪去重、按关键词筛选
---

# Xiaohongshu Scraper

从小红书（xiaohongshu.com）搜索并提取笔记数据（标题、作者、日期、点赞数、链接），输出结构化 JSON。

## 为什么需要这个 Skill

小红书是典型的 **SPA + 反爬** 站点：
- 必须登录才能看搜索结果
- headless 模式被检测 → 只能看到页脚（body 长度 746）
- 内容由 JS 动态渲染，DOM 结构嵌套深
- 无限滚动加载更多
- 链接没有 `href*="/202"` 那种规律——要依赖 `section.note-item` 结构

## 依赖

前提：已安装 `browser-automation` skill。

```bash
source .venv/bin/activate
```

## 前置准备：启动带登录态的 Chrome

**关键：使用用户已有的 Chrome Profile，否则每次都要重新登录小红书。**

```bash
# 第一步：确保没有 Chrome 在运行
pkill -9 -f "Google Chrome" 2>/dev/null
sleep 2

# 第二步：用 CDP 端口启动 Chrome（自动继承你的登录态）
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --no-first-run \
  > /dev/null 2>&1 &
sleep 3

# 第三步：验证 CDP 就绪
curl -s http://localhost:9222/json/version | python3 -c "import sys,json; print(json.load(sys.stdin)['Browser'])"
```

> **macOS 特殊注意**：不要传 `--user-data-dir` 到临时目录（会丢失登录态），不加该参数则自动使用 `~/Library/Application Support/Google/Chrome/Default`。

## 核心提取模式

### 小红书笔记卡的 DOM 结构

```
section.note-item
  └── a[href*="/explore/"]          ← 笔记链接
  └── innerText:
      居家无器械训练方案        ← 第1行：标题
      健身达人小明               ← 第2行：作者昵称
      06-21                      ← 第3行：日期
      265                        ← 第4行：点赞数
```

### 通用提取函数

```python
import asyncio, json, re
from src.browser import BrowserConfig, BrowserSession

async def scrape_xhs(keyword: str, max_scrolls: int = 8) -> list[dict]:
    """
    搜索小红书并提取笔记。

    Args:
        keyword: 搜索关键词
        max_scrolls: 滚动次数（每次加载更多笔记）

    Returns:
        [{"title": "...", "author": "...", "date": "...", "likes": "...", "url": "..."}, ...]
    """
    config = BrowserConfig(cdp_url="http://127.0.0.1:9222")
    session = BrowserSession(config=config)
    await session.start()

    # URL 编码关键词
    from urllib.parse import quote
    url = f"https://www.xiaohongshu.com/search_result?keyword={quote(keyword)}&type=51"
    await session.navigate(url, wait_until="load")
    await asyncio.sleep(6)  # 等 SPA 渲染

    # 无限滚动加载更多
    for _ in range(max_scrolls):
        await session.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(0.8)

    # 核心提取 JS：遍历 section.note-item
    js = r'''
    (function() {
        var sections = document.querySelectorAll('section.note-item');
        var results = [];
        var seen = {};

        sections.forEach(function(sec) {
            var a = sec.querySelector('a[href*="/explore/"]');
            if (!a) return;

            var text = (sec.innerText || '').trim();
            var lines = text.split('\n').filter(function(l) {
                return l.trim().length > 0;
            });
            if (lines.length < 2 || seen[a.href]) return;
            seen[a.href] = true;

            // 第1行 = 标题，后面几行解析 author / date / likes
            var title = lines[0].trim();
            if (title.length < 5 || title.length > 100) return;

            var author = '', date = '', likes = '';
            for (var i = 1; i < Math.min(lines.length, 5); i++) {
                var line = lines[i].trim();
                if (/^\d{2}-\d{2}$/.test(line) ||
                    /^\d{4}-\d{2}-\d{2}$/.test(line) ||
                    /(天前|小时前|分钟前)/.test(line)) {
                    if (!date) date = line;
                } else if (/^[\d,.]+万?$/.test(line)) {
                    if (!likes) likes = line;
                } else if (line.length <= 25 && !author) {
                    author = line;
                }
            }

            results.push({
                title: title,
                author: author,
                date: date,
                likes: likes,
                url: a.href
            });
        });

        return JSON.stringify(results);
    })()
    '''
    raw = await session.evaluate(js)
    notes = json.loads(raw)

    # 去重（按标题前15字符）
    seen = set()
    unique = []
    for n in notes:
        key = n['title'][:15]
        if key not in seen:
            seen.add(key)
            unique.append(n)

    await session.stop()
    return unique
```

## 完整示例：搜索并按关键词筛选

```python
async def search_and_filter(keyword: str, target_keywords: list[str], top_n: int = 10):
    """搜索 → 提取 → 按标题关键词筛选 → 取 Top N"""
    all_notes = await scrape_xhs(keyword, max_scrolls=8)

    # 筛选：标题包含任一目标关键词
    matched = [
        n for n in all_notes
        if any(kw in n['title'] for kw in target_keywords)
    ]

    # 额外过滤：排除"纯作者名"笔记（标题 == 作者名）
    matched = [n for n in matched if n['title'] != n['author']]

    return matched[:top_n]


# 示例：搜索"居家无器械健身"
results = asyncio.run(search_and_filter(
    keyword="徒手无器械健身",
    target_keywords=["无器械", "徒手", "居家", "在家", "自重", "不去健身",
                     "无器材", "零器械", "家庭", "不需要", "家里练", "室内"],
    top_n=10,
))

for i, n in enumerate(results, 1):
    print(f"{i}. {n['title']}")
    print(f"   👤 {n['author']}  |  ❤️ {n['likes']}  |  📅 {n['date']}")
    print(f"   🔗 {n['url']}")
```

## 关键经验

### 1. headless 被小红书检测

❌ 用 `headless=True` 启动 → 页面 body 只有 746 字节，全是页脚文本，看不到笔记内容。

✅ 用 `headless=False` 或连接已有 Chrome（用户已登录），body 可达 5000+ 字符。

```python
# ❌ 不行：无头模式被拦截
config = BrowserConfig(headless=True, ...)

# ✅ 可行方案 A：可见窗口 + 自动登录
config = BrowserConfig(headless=False, ...)

# ✅ 可行方案 B：连接已有 Chrome（推荐）
config = BrowserConfig(cdp_url="http://127.0.0.1:9222")
```

### 2. DOM 选择器：只认 `section.note-item`

小红书笔记卡不在 `<article>`、不在自定义 element 里，就是 `section.note-item`。

```python
# ✅ 正确
sections = document.querySelectorAll('section.note-item')

# ❌ 以下都不会匹配到笔记
document.querySelectorAll('article')
document.querySelectorAll('[class*="card"]')
document.querySelectorAll('a[href*="/202"]')  # 小红书没有年份路径
```

### 3. innerText 解析比 HTML 遍历可靠

`section.note-item` 内部 div 嵌套很深，逐层 `querySelector` 极其脆弱。

✅ 直接读 `sec.innerText`，按 `\n` 分割，第 1 行 = 标题，然后正则匹配日期/点赞。

```
居家无器械训练
健身小明         ← author（短文本，非日期非数字）
06-21            ← date（正则 \d{2}-\d{2}）
265              ← likes（正则 \d+）
```

### 4. 链接提取：`a[href*="/explore/"]`

笔记链接统一走 `/explore/{note_id}`，不是 `/discovery/item/`。

```python
a = sec.querySelector('a[href*="/explore/"]')
```

### 5. 滚动加载

小红书无限滚动，一次最多显示 ~20 条。要获取更多必须滚动：

```python
for _ in range(8):  # 8 次 = 大约可加载 100+ 条
    await session.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(0.8)
```

### 6. 去重策略

同一笔记可能在 DOM 中出现多次（不同的 wrapper div），用标题前 15 字符去重最实用：

```python
seen = set()
unique = []
for n in notes:
    key = n['title'][:15]
    if key not in seen:
        seen.add(key)
        unique.append(n)
```

### 7. 导航超时不影响

```
Page readiness timeout (3.0s) for https://www.xiaohongshu.com/...
```
正常现象，页面已可用，无视即可。

### 8. 排除噪音笔记

部分 card 的 innerText 格式异常（标题==作者名、纯营销号），需要二次过滤：

```python
# 去掉"标题即作者名"的噪音
matched = [n for n in matched if n['title'] != n['author']]

# 去掉导航/tag/页脚关键词
skip_words = ['沪ICP', '营业执照', '©', '大家都在搜', '关注']
matched = [n for n in matched if not any(w in n['title'] for w in skip_words)]
```

## 已知限制

| 问题 | 原因 | 对策 |
|------|------|------|
| 需要登录 | 小红书搜索结果仅对登录用户开放 | 复用 Chrome profile 或手动登录一次 |
| 无法 headless | 反爬检测 headless 模式 | 用 `headless=False` 或连接已有浏览器 |
| 每次搜索最多 ~150 条 | 无限滚动有上限 | `max_scrolls=8` 通常够用 |
| 笔记内容（正文）不可直接提取 | 需要点进详情页 | 目前只提取标题/作者/点赞/链接 |
| 有时 innerText 解析错位 | 部分 card 有额外标签行 | 正则兜底 + 二次过滤 |

## 适用场景

- "整理小红书 XX 话题的 N 篇精选笔记"
- "搜索小红书某个关键词并汇总链接"
- 批量收集他人笔记的点赞/互动数据用于分析
- 将小红书内容聚合为 Markdown 日报

## 与 news-aggregator 的区别

| | news-aggregator | xiaohongshu-scraper |
|---|---|---|
| 站点类型 | 传统新闻站（SSR+SPA） | 纯 SPA + 反爬 |
| 登录需求 | 无需 | **必须** |
| 连接方式 | 自启动 Chrome | **连接已有 Chrome** |
| DOM 选择器 | `a[href*="/202"]` | **`section.note-item`** |
| 提取方式 | body innerText 解析 | **section.innerText 逐行解析** |
| 分页 | 单一页面 | **无限滚动** |
