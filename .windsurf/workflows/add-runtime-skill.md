---
description: how to add a runtime skill as an executable workflow in amazon-bestsellers-app
---
本工作流用于新增一个 **runtime skill**。这里的 skill 不应只是“分析要求说明书”，而应是**真正可执行的工作流**。

## skill 的定位

skill 应该回答这几个问题：

- 输入是什么？
- 前置条件是什么？
- 具体按什么顺序做？
- 成功时会产出什么？
- 失败时怎么处理？
- 何时算完成，可以交给下游？

如果一个 `SKILL.md` 只有“你是一个专家，请分析……”而没有执行顺序、产物和完成条件，那它更像 role prompt，不像 workflow skill。

## 推荐 skill 模板

```md
---
name: skill-name
description: one-line description
hooks: []
---

# Goal

一句话说明这个 skill 完成什么任务。

# Inputs

- `workspace`
- `browse_node_id`
- `mode`
- 其他必要输入

# Preconditions

列出调用前必须满足的条件。

# Step-by-step Procedure

1. 第一步做什么
2. 第二步做什么
3. 第三步做什么

# Output Artifacts

- 会写哪些文件
- 会读哪些文件
- 每个文件的路径规则是什么

# Failure Handling

- 哪些错误可以重试
- 哪些错误必须立即失败
- 哪些错误只记 WARN

# Done Criteria

明确什么情况下算成功完成。

# Handoff Contract

告诉上游 / 下游 agent：这个 skill 完成后你能依赖什么。
```

## 在本项目里新增 skill 的规则

### 1. 按“产物”拆，不按“角色名”拆

好的 skill 名称：
- `plan-refresh-delta`
- `run-chunker-batch`
- `audit-chunk-coverage`
- `compile-marketplace-report`
- `synthesize-summary`

不好的 skill 名称：
- `marketplace-expert`
- `best-amazon-analyst`
- `super-summary-agent`

前者是 workflow，后者只是人格。

### 2. 每个 skill 只交付一类核心产物

示例：
- `compile-marketplace-report` 只负责产出 marketplace 报告
- `audit-chunk-coverage` 只负责产出 audit_report
- `reanalyze-reset-workspace` 只负责清理旧状态并返回可重新运行的 workspace 状态

不要让一个 skill 同时负责“抓取 + 分析 + 总结”。

### 3. 必须写清文件契约

每个 skill 都必须写清：
- 读哪些路径
- 写哪些路径
- 哪些文件存在即表示阶段完成
- 哪些文件缺失必须视为失败

### 4. 必须区分 FAIL 与 WARN

示例：
- `golden` 缺失但 chunks 可用 → 可能是 WARN
- `summary.md` 缺失 → 必须 FAIL
- `current_ranking.json` 缺失但全量模式首次运行 → 可以允许

### 5. 必须能被 refresh / reanalyze / resume 明确约束

skill 要说明自己适用于：
- full only
- refresh only
- resume only
- all modes

## 迁移现有 skill 的办法

把现有偏“分析规范”的 skill 改造成 workflow skill 时，按这个顺序处理：

1. 保留原有分析范围和 hard rules
2. 补上 Inputs / Preconditions / Procedure / Output / Done Criteria
3. 把“必须输出什么文件”写成显式 contract
4. 把“覆盖范围”和“缺失数据处理”写成 failure policy
5. 明确这个 skill 是被哪个 director 调用

## 评审清单

新增或改造 skill 后，检查以下问题：

- 是否存在明确输入？
- 是否存在明确输出文件？
- 是否存在顺序化步骤？
- 是否写清楚成功标准？
- 是否写清楚失败标准？
- 是否能被两个不同 director 复用？
- 是否避免把导演职责塞回 skill？
