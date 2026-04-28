---
name: aplus-extractor
description: A+ 品牌内容块提取器。从 aplus.html 中提取 Comparison Table / Image Assets / Brand Story 三个子阶段的结构化数据，输出 aplus_extracted.md。当 agent 需要提取 A+ 块时调度此技能。
type: skill
---

# A+ 提取器

**输入**：`aplus.html`
**输出**：`aplus_extracted.md`
**复杂度**：复杂，3 个子阶段

## 子阶段

### B1. Comparison Table（对比表）

- `#aplus` 内的 `<table>` 或对比网格
- 输出为 Markdown 表格
- 可能不存在（标记为可选）
- 自行发现其他可能的路径（有些并不是 `<table>` 标签，而是 `<div>` 标签）

### B2. Image Assets（图片资源）

- 提取所有 `<img>` 的 `src` 和 `alt`
- 输出为 `| alt | src |` 表格

### B3. Brand Story / 文案内容（可选）

- A+ 区域的文字段落
- 如果结构不稳定，仅提取存在的文本段，不强行解析

## 提取策略要求

1. A+ 内容变体极大（不同品牌的 A+ 模板差异很大）
2. 优先提取**对比表**和**图片资源**（这两者结构相对稳定）
3. 文案内容如果结构差异大，保留为清洗后的纯文本块即可
4. 如果某个子阶段在样本中不存在，跳过不报错

## 产出

- `chunker/aplus_extract.py` — A+ 提取器实现
- `tests/test_aplus_extract.py` — A+ 提取器测试
