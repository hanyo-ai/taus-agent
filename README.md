<div align="center">
  <img src="assets/taus.svg" alt="TAUS Agent Logo" width="200" />
  <h1>TAUS Agent</h1>
  <p><strong>一个高效、可扩展的多智能体 AI 代理框架</strong></p>
  <p>
    <img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" />
    <img src="https://img.shields.io/badge/status-alpha-orange.svg" alt="Status" />
  </p>
</div>

---

## 📖 简介

**TAUS Agent** 是一个轻量级但功能强大的 AI 代理框架，支持单智能体与多智能体协作。它内置了丰富的工具集、上下文压缩、持久化记忆和灵活的 API 接口，旨在帮助开发者快速构建智能、可交互的 AI 工作流。

无论是构建自动化脚本助手，还是搭建多 Agent 协作系统，TAUS Agent 都能提供开箱即用的支持。

---

## 🗺️ ROADMAP

- [ ] **内置工具集** —— `read`、`bash`、`edit`、`write`
- [ ] **上下文压缩** —— 消息太长时自动摘要
- [ ] **技能加载** —— 按需加载 `skills/<name>/SKILL.md`
- [ ] **全局记忆** —— `MEMORY.md` 摘要注入上下文
- [ ] **子 Agent** —— `create_agent` 创建隔离的子代理
- [ ] **CLI REPL / HTTP API**
- [ ] **模型自动切换分配**
- [ ] **Agent Manager** —— span 追踪支持
- [ ] **Agent 群组** —— 支持手动创建群组
- [ ] **渐进式记忆披露**
- [ ] **多 Agent 消息总线**
- [ ] **REST API** —— 支持外部脚本与任意 Agent 通信
- [ ] **SQLite 存储**
- [ ] **Office CLI & Browser 集成**

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！请确保在提交前：

1. 代码风格与项目保持一致
2. 添加必要的测试用例
3. 更新相关文档

---

## 📄 许可

本项目基于 [Apache 2.0](LICENSE) 许可证开源。

---

<div align="center">
  <sub>Built with ❤️ by the TAUS Team</sub>
</div>