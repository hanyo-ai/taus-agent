---
name: taus-wiki
description: 管理 LLM 维护的知识库：原始材料归档、知识文章合成、索引维护
---

# LLM Wiki 知识库管理

## 目录结构

```
├── raw/                    # 不可变的原始材料
│   └── topic/              # 按主题组织
│       └── YYYY-MM-DD-slug.md
├── wiki/                   # LLM 维护的知识文章
│   ├── index.md            # 全局索引（每篇文章一行）
│   ├── log.md              # 追加式操作日志
│   └── topic/              # 按主题组织
│       └── article.md
└── SKILL.md                # 架构层：定义结构和规则
```

## 核心原则

1. **分离关注点**：原始材料（raw/）不可变，知识文章（wiki/）可演化
2. **主题驱动**：按主题组织内容，不按时间或文件类型
3. **追加式日志**：所有操作追加到 log.md，保持完整历史
4. **单行索引**：index.md 每篇文章一行，便于快速浏览和维护

## 操作流程

### 1. 添加原始材料

```bash
# 归档到 raw/topic/ 目录
# 文件名格式：YYYY-MM-DD-slug.md
raw/ai-research/2026-06-18-transformer-attention.md
raw/power-standards/2026-06-18-dl-t-1710-2017.md
```

**规则**：
- 日期使用材料的发布日期或收集日期
- slug 使用小写字母和连字符，简明扼要
- 内容保持原样，不做修改
- 如果是已存在的标准或文档，保留原始标题和格式

### 2. 合成知识文章

基于 raw/ 中的材料，在 wiki/topic/ 下创建或更新文章：

```markdown
# 文章标题

## 概述
[简要总结，说明文章范围和目标读者]

## 核心概念
[提炼关键概念和原理]

## 详细内容
[结构化的深入内容]

## 相关资源
- [原始材料链接](../../raw/topic/YYYY-MM-DD-slug.md)
- [相关文章链接](../other-topic/article.md)

## 更新历史
- 2026-06-18: 初始版本，基于 raw/topic/2026-06-18-slug.md
```

**规则**：
- 文章名使用主题或概念名，不含日期
- 内容要综合、提炼、结构化，不是简单复制
- 必须链接回原始材料
- 更新历史追加在文末
- 支持跨主题交叉引用

### 3. 维护索引（index.md）

每次添加或更新文章后，在 `wiki/index.md` 中添加/更新条目：

```markdown
# Wiki 索引

## AI 研究
- [Transformer 注意力机制](ai-research/transformer-attention.md) - 深入解析自注意力的数学原理和实现细节

## 电力标准
- [电力通信站运维规范](power-standards/communication-station.md) - DL/T 1710-2017 标准要点总结

## [其他主题]
...
```

**格式**：
```
- [文章标题](相对路径) - 一句话描述（< 80 字符）
```

### 4. 记录操作日志（log.md）

所有操作追加到 `wiki/log.md`：

```markdown
## 2026-06-18 17:30

**操作**: 添加原始材料  
**文件**: raw/power-standards/2026-06-18-dl-t-1710-2017.md  
**说明**: 归档电力通信站运维技术规范 DL/T 1710-2017

---

## 2026-06-18 17:45

**操作**: 创建知识文章  
**文件**: wiki/power-standards/communication-station.md  
**说明**: 基于 DL/T 1710-2017 提炼运维要点和技术规范  
**来源**: raw/power-standards/2026-06-18-dl-t-1710-2017.md

---
```

**规则**：
- 使用 ISO 日期时间格式
- 每条日志包含：操作类型、文件路径、说明、来源（如适用）
- 条目之间用 `---` 分隔
- 只追加，不修改历史记录

## 主题分类指南

根据内容性质组织主题目录：

- **技术领域**：ai-research, power-standards, network-protocols
- **开发实践**：coding-patterns, architecture, testing
- **工具使用**：git-workflows, docker, kubernetes
- **业务领域**：energy-management, telecom, finance

新主题创建前检查是否可归入现有主题。

## 维护指令

### ingest
```
/llm-wiki ingest <file_or_url> --topic <topic_name>
```
将文件或 URL 内容归档到 raw/，使用当天日期。

### synthesize
```
/llm-wiki synthesize <raw_file> --output <article_name>
```
基于原始材料创建或更新 wiki 文章。

### update-index
```
/llm-wiki update-index
```
扫描 wiki/ 目录，重新生成完整索引。

### search
```
/llm-wiki search <keyword>
```
在 wiki 文章中搜索关键词，返回相关文章列表。

## 注意事项

1. **版本控制**：所有文件纳入 git，利用 commit 历史追踪演化
2. **命名一致性**：主题名在 raw/、wiki/ 和 index.md 中保持一致
3. **内容质量**：wiki 文章应该比原始材料更易理解和使用
4. **引用完整性**：定期检查文章间的链接是否有效
5. **适度抽象**：避免过度细分主题，保持结构清晰简洁

## 示例工作流

```bash
# 1. 收到新的技术文档
/llm-wiki ingest dl-t-1710-2017.pdf --topic power-standards

# 2. 提炼成知识文章
/llm-wiki synthesize raw/power-standards/2026-06-18-dl-t-1710-2017.md \
  --output wiki/power-standards/communication-station.md

# 3. 更新索引（自动完成）
# index.md 和 log.md 已自动更新

# 4. 交叉引用其他相关文章
# 在文章中添加相关链接
```

## 与现有 wiki/ 目录整合

当前项目已存在 `wiki/DL3032/` 目录，包含大量电力行业标准文档。整合方案：

1. **保留现有结构**：`wiki/DL3032/` 视为一个特殊的 raw 材料源
2. **创建索引**：为 DL3032 标准创建 `wiki/power-standards/index.md`
3. **按需合成**：根据使用频率，逐步将常用标准合成为 wiki 文章
4. **双向链接**：wiki 文章链接到 DL3032 原始文档，反之亦然

```markdown
# 电力标准索引

## 通信相关
- [通信站运维](communication-station.md) - 基于 DL/T 1710-2017
- [电力通信网](power-communication-network.md) - 跨多个 DL 标准综合

## 原始标准文档库
详见 [DL3032 标准目录](../DL3032/) - 3000+ 份完整标准文档
```
