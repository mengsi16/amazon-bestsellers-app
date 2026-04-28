---
description: how to refactor amazon-bestsellers-app into director agents plus executable runtime skills
---
本工作流定义了 `amazon-bestsellers-app` 的 agent + skill 架构规范。

## 架构：三层分离

1. **Director Agents（导演层）** — 阶段编排、分支判断、失败处理、结果验收
2. **Runtime Skills（执行层）** — 可执行工作流，写清输入/步骤/输出/完成条件
3. **Tools / MCP（工具层）** — 抓取、文件处理、脚本执行

## 后端只启动两种 Agent

后端通过 `task.agent_id` 字段决定启动哪个 agent：

| 操作 | agent_id | 说明 |
|------|----------|------|
| 创建任务 | `amazon-bestsellers-orchestrator` | 全量分析（0→1） |
| 增量刷新 | `amazon-bestsellers-refresh-director` | 增量更新 |
| 全量重分析 | `amazon-bestsellers-orchestrator` | 清空 workspace 从头来 |
| 断点续跑 | 沿用 `task.agent_id` | resume 用上次同一个 agent |

前端不感知 agent 切换，同一个任务窗口。

## Agent 清单

### `amazon-bestsellers-orchestrator`（全量层）

全量编排器，从零开始调度 scraper → chunker → audit → 四个 analyst → summary。

- 接收 `full / reanalyze / resume`
- 不处理增量更新

### `amazon-bestsellers-refresh-director`（增量层）

增量层导演，在已有全量数据基础上刷新排名、处理新增 ASIN、重新生成报告。

- 前置条件：workspace 已有全量数据（categories/ + products/ + chunks/ + chunker/batch_run.py）
- Skills：`amazon-refresh-delta-planner`

### `amazon-chunker-audit`

独立 agent，只检查不修复。

### `amazon-product-chunker`

全量层 chunker agent，负责黄金样本 + 分块 + 提取 + 测试。

### 四个维度 analyst agents

- `amazon-bestsellers-marketplace-analyst`
- `amazon-bestsellers-reviews-analyst`
- `amazon-bestsellers-aplus-analyst`
- `amazon-bestsellers-fine-grained-analyst`

## Skill 模板

所有 runtime skills 统一包含：
- Goal
- Inputs
- Preconditions
- Step-by-step Procedure
- Output Artifacts
- Failure Handling
- Done Criteria
- Handoff Contract

## 关键约束

- backend 是 `workspace_path` 的唯一权威来源
- director 不得从 CWD 推导 workspace
- refresh 与 reanalyze 必须严格分流
- audit 必须独立，禁止自审自修
- 每个 skill 都必须声明产物路径与完成标志
- 禁止在文件中写入仅存在于特定修改上下文的内容
