# X Reply — X (Twitter) 回复评论

在 X 上浏览时间线，找一条合适的帖子，以人类口吻发表回复。

## 依赖

前提：已安装 `browser-automation` skill 的依赖。

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
┌─────────────────────────────────────────────────────┐
│  Step 1: 浏览时间线，挑选目标帖子                     │
│  连接已有 Chrome → 导航 X 首页 → JS 提取帖子列表       │
├─────────────────────────────────────────────────────┤
│  Step 2: 生成回复内容                                │
│  读帖子内容 → 分析语境 → 人类语气写评论（≤280字符）    │
├─────────────────────────────────────────────────────┤
│  Step 3: 执行回复                                    │
│  导航到帖子 → 点回复按钮 → 逐字符输入 → 点击发送       │
└─────────────────────────────────────────────────────┘
```

## Step 1: 浏览时间线

### 提取帖子列表

```python
import asyncio, json
from src.browser import BrowserConfig, BrowserSession

async def get_timeline_posts(session):
    """从 X 首页提取时间线帖子"""
    await session.navigate("https://x.com/home", wait_until="load")
    await asyncio.sleep(4)

    posts = await session.evaluate("""
        (function() {
            var results = [];
            var articles = document.querySelectorAll('article');
            for (var i = 0; i < Math.min(articles.length, 20); i++) {
                var article = articles[i];
                var text = article.textContent.trim();
                if (text.length > 40) {
                    // 尝试提取作者和链接
                    var authorLink = article.querySelector('a[href*="/status/"]');
                    var href = authorLink ? authorLink.href : null;
                    results.push({
                        index: i,
                        preview: text.substring(0, 250),
                        href: href
                    });
                }
            }
            return JSON.stringify(results);
        })()
    """)
    return json.loads(posts)
```

### 挑选策略

- 优先挑选**互动量高**的帖子（转发/点赞多）
- 优先挑选**话题性强的**（AI/科技/热点）
- 优先挑选**语气轻松的**（更容易用人类口吻回复）
- 避免挑广告、纯转发、机器翻译

## Step 2: 生成回复内容

### ⚠️ 风格指南

| ✅ 好的回复 | ❌ 避免的回复 |
|-------------|---------------|
| 有观点、有态度 | "说得好！支持！"（太敷衍） |
| 适当幽默 | "我认为您的观点非常正确"（AI 腔） |
| 抛出问题引发进一步讨论 | 机械复述原帖内容 |
| 像朋友聊天一样 | 长篇大论像写论文 |
| 具体、有细节 | 含糊其辞 |

### 回复策略模板

**策略 1: 幽默调侃 + 正经提问**
- 前半句用轻松语气接梗
- 后半句提出一个技术性/深度问题
- 示例：对 Sam Altman 说"模型终于会设计" → 回"设计师们集体失眠了 😂 不过说真的，这次是风格迁移还是原生出图？"

**策略 2: 亲身经历**
- "我也遇到过……" / "之前试了……"
- 增加真实感，像真人分享经验

**策略 3: 反常识观点**
- 提出一个和主流相反的观点
- 引发辩论，增加互动

**策略 4: 延伸联想**
- 把话题引到更广的层面
- "这让我想到……"
- 显得有深度

### 生成函数

```python
def generate_reply(post_text, author):
    """根据帖子内容生成人类风格的回复。
    
    需要：理解帖子语境 → 选择策略 → 用口语化语言表达
    """
    # 这不是纯代码能做的事，需要 AI 分析帖子内容
    # 核心原则：
    # 1. 控制在 280 字符以内
    # 2. 像真人说话，不像 AI
    # 3. 有信息量，值得别人点赞
    pass
```

## Step 3: 执行回复

### 完整回复流程

```python
async def reply_to_tweet(session, tweet_url, comment):
    """在 X 上回复指定帖子。Comment 必须 ≤ 280 字符。"""

    if len(comment) > 280:
        print(f"⚠️  评论 {len(comment)} 字符，超过 280 限制")
        return False

    # 1. 导航到帖子
    await session.navigate(tweet_url, wait_until="load")
    await asyncio.sleep(3)

    page = await session.get_current_page()

    # 2. 点击回复按钮
    reply_btn = await page.query_selector('[data-testid="reply"]')
    if not reply_btn:
        print("❌ 找不到回复按钮")
        return False

    await reply_btn.click()
    await asyncio.sleep(1.5)

    # 3. 找到回复框（弹窗中的 contenteditable）
    editor = await page.query_selector('div[contenteditable="true"]')
    if not editor:
        editor = await page.query_selector('div[role="textbox"]')
    if not editor:
        print("❌ 找不到回复框")
        return False

    await editor.click()
    await asyncio.sleep(0.3)

    # 4. ⚠️ 必须用 page.type_text() 真实键盘输入（React 才认）
    print(f"✍️  输入评论 ({len(comment)} 字符)...")
    await page.type_text(comment)
    await asyncio.sleep(0.3)

    # 5. 等待发送按钮激活并点击
    for i in range(15):
        await asyncio.sleep(0.5)
        btn_info = await session.evaluate("""
            (function() {
                var btn = document.querySelector('[data-testid="tweetButton"]');
                if (!btn) btn = document.querySelector('[data-testid="tweetButtonInline"]');
                if (!btn) {
                    var all = document.querySelectorAll('[role="button"]');
                    for (var i = 0; i < all.length; i++) {
                        var a = all[i].getAttribute('aria-label');
                        if (a && (a.includes('Reply') || a.includes('回复')))
                            return {disabled: all[i].disabled};
                    }
                }
                return btn ? {disabled: btn.disabled} : null;
            })()
        """)
        if btn_info and not btn_info["disabled"]:
            await session.evaluate("""
                (function() {
                    var btn = document.querySelector('[data-testid="tweetButton"]');
                    if (!btn) btn = document.querySelector('[data-testid="tweetButtonInline"]');
                    if (btn) { btn.click(); return; }
                    var all = document.querySelectorAll('[role="button"]');
                    for (var i = 0; i < all.length; i++) {
                        var a = all[i].getAttribute('aria-label');
                        if (a && (a.includes('Reply') || a.includes('回复'))) {
                            all[i].click();
                            return;
                        }
                    }
                })()
            """)
            print("🎉 评论已发送！")
            return True

    print("⚠️  发送按钮未激活，请手动点击发送")
    return False
```

## 完整端到端脚本模板

```python
import asyncio, json
from src.browser import BrowserConfig, BrowserSession

async def main():
    config = BrowserConfig(
        cdp_url="http://localhost:60684",
        headless=False,
    )
    session = BrowserSession(config=config)
    await session.start()
    print("✅ 已连接浏览器\n")

    # === Step 1: 刷时间线 ===
    print("📋 浏览 X 时间线...")
    await session.navigate("https://x.com/home", wait_until="load")
    await asyncio.sleep(4)

    posts = await session.evaluate("""
        (function() {
            var results = [];
            var articles = document.querySelectorAll('article');
            for (var i = 0; i < Math.min(articles.length, 15); i++) {
                var text = articles[i].textContent.trim();
                if (text.length > 40) {
                    var statusLink = articles[i].querySelector('a[href*="/status/"]');
                    results.push({
                        index: i,
                        preview: text.substring(0, 250),
                        href: statusLink ? statusLink.href : null
                    });
                }
            }
            return JSON.stringify(results);
        })()
    """)

    posts_list = json.loads(posts)
    for p in posts_list:
        print(f"\n[{p['index']}] {p['preview'][:200]}")
        if p['href']:
            print(f"    🔗 {p['href']}")

    # === Step 2: 挑一条 + 写回复 ===
    # 手动挑选最合适的一条
    # 根据帖子内容，用人类口吻写回复

    comment = "你的回复内容（≤280字符）"
    target_url = posts_list[0]['href']  # 替换为实际挑选的

    # === Step 3: 发送回复 ===
    print(f"\n🐦 回复帖子: {target_url}")
    await reply_to_tweet(session, target_url, comment)


if __name__ == "__main__":
    asyncio.run(main())
```

## 踩坑总结

| 问题 | 原因 | 解决 |
|------|------|------|
| 回复按钮找不到 | 不同 UI 状态按钮位置不同 | 用 `[data-testid="reply"]` 定位 |
| 回复框不在预期位置 | 弹窗模式，DOM 结构变化 | 用 `div[contenteditable]` 通用选择器 |
| 发送按钮在弹窗中 | 弹窗内按钮可能不在主文档流 | 多 fallback：tweetButton → tweetButtonInline → aria-label |
| 评论字数超限 | X 限制 280 字符 | 回复前检查 len() |
| React 不认 JS 设值 | contenteditable + React | 必须 `page.type_text()` |
