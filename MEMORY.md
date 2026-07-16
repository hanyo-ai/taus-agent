# 全局记忆

> 此文件记录跨会话的持久信息。Agent 每次对话自动读取摘要，需要详细信息时用 read 工具按需加载。

## 用户信息
- 称呼：hqj
- 系统：macOS ARM64 (Apple Silicon)，Python 3.14
- Shell 环境：注意 shell 可能在 Rosetta (x86_64) 下运行，启动 Chrome 必须用 `arch -arm64`

## 浏览器自动化 (Browser Automation)

### Chrome 启动
- **必须 `arch -arm64`**：shell 在 Rosetta 下时，直接启动 Chrome 会用 x64 版本（10x 慢）
- **`--remote-debugging-port` 需要 `--user-data-dir`**：Chrome 拒绝在默认 profile 路径开调试端口
- **持久化 profile**：`~/.taus-browser-profile`（从原生 Chrome Profile 复制，含 cookie/登录态）
- **推荐方式**：先手动启动 Chrome 再通过 `cdp_url="http://localhost:9222"` 连接

### 代理配置
- 代理软件：Hiddify，监听 `127.0.0.1:12334`（SOCKS5/HTTP），`12336`/`12337` 也是 Hiddify
- 还有 ClashX Pro：`127.0.0.1:7890`（HTTP），`127.0.0.1:9090`
- **Chrome 代理传参**：`ProxySettings(server="socks5://127.0.0.1:12334")`
- 注意：Hiddify HTTP 代理对 x.com 返回 000，SOCKS5 能通但有时也超时
- 国内站点不要走代理（直连更快）
- 运行脚本前必须 `no_proxy=* NO_PROXY=*` 避免系统代理干扰 CDP/websocket

### 关键修复记录 (2026-07-15)
| 问题 | 根因 | 修复 |
|------|------|------|
| 页面加载 8-12s | Shell 在 Rosetta (x86_64) | `session.py` 自动加 `arch -arm64` |
| 导航一直超时 | lifecycle events 未正确触发 | 改用 `document.readyState` 轮询 |
| HTTP 代理不通 | Hiddify 不支持 HTTP CONNECT | 改用 `socks5://` |

### 推荐启动命令
```bash
arch -arm64 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="/Users/hqj/.taus-browser-profile" \
  --profile-directory="Default" \
  --window-size=1280,900 \
  --proxy-server="socks5://127.0.0.1:12334"
```

### 项目路径
- 项目根目录：`/Users/hqj/data/HANYO/taus-agent`
- 虚拟环境：`.venv/bin/activate`
- 浏览器模块：`src/browser/`
- Skills 目录：`skills/`
