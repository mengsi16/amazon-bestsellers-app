---
description: how to add a new director agent in amazon-bestsellers-app without turning it into a worker prompt
---
本工作流用于新增一个 **director agent**。director 的定位是“导演”，不是“工人”。

## 什么时候应该新增 director agent

只有满足以下任一条件才新增：

- 存在明确的阶段边界，需要单独调度和验收
- 存在复杂分支判断（full / refresh / resume / repair）
- 存在“检查者”和“执行者”必须隔离的要求
- 一个现有 agent 已经因为上下文过长而明显失控

如果只是新增一个固定步骤，不要新增 agent，优先新增 skill。

## director agent 的职责边界

director 负责：
- 接收阶段目标
- 判断前置条件是否满足
- 选择要调用哪些 skills
- 验收技能产物是否完整
- 决定失败时是重试、降级还是上报

director 不负责：
- 长篇的数据处理细节
- 一大段逐条执行步骤
- 把所有业务规则硬编码在 agent prompt 里

## 新增 director 的步骤

### 1. 先定义它的“唯一使命”

必须能用一句话说清：

- 这个 director 只对哪个阶段负责？
- 它要交付什么结果？
- 哪些事情它明确不负责？

如果说不清，就说明不该拆成 agent。

### 2. 先列出它依赖的 skills

在创建 agent 文件之前，先把它依赖的 skills 列清楚。

示例：
- `crawl-director` 依赖 `plan-refresh-delta`、`crawl-bestseller-list`、`crawl-product-details-batch`
- `analysis-director` 依赖四个 report compile skills 和一个 report validation skill

如果没有明确 skills 列表，不要开始写 director。

### 3. 编写 agent 文件时必须包含

- 触发条件
- 输入契约
- 不变量
- 可调用的 skills 列表
- 验收规则
- 失败处理规则
- 明确的 scope boundary

## director prompt 推荐结构

```md
---
name: "example-director"
description: "负责某个阶段的编排与验收"
model: sonnet
skills:
  - skill-a
  - skill-b
---

You are a stage director.

## Mission

## Inputs

## Preconditions

## Scope Boundary

## Execution Policy

## Acceptance Criteria

## Failure Policy

## Hard Rules
```

## 强制规则

1. director 不得把工具调用细节全部写死在自己 prompt 里，细节应下沉到 skill。
2. director 不得同时承担“执行”和“独立审计”两个角色。
3. director 必须显式写出自己不负责的内容。
4. director 必须优先依赖显式传入的 `workspace`、`browse_node_id`、`mode`。
5. director 必须能被上游 agent 复用，而不是只服务单个 prompt 语境。

## 评审清单

新增 director 后，检查以下问题：

- 这个 agent 是否真的在做“决策”，而不是在重复 skill 文档？
- 它是否有清晰的输入 / 输出 / 验收边界？
- 它是否减少了顶层 orchestrator 的复杂度？
- 它是否引入了不必要的多级嵌套？
- 它的存在是否让某个 skill 可以被多处复用？
