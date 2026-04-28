---
name: amazon-refresh-delta-planner
description: >
  增量更新时的 Delta 计算技能。对比 current_ranking.json 中的当前在榜 ASIN 与 products/ 目录下已有详情页的 ASIN，
  输出三类列表：新增 ASIN（需要爬取详情页）、跌出 ASIN（rank=-1，禁止分析）、不变 ASIN（已有数据，跳过）。
  当 refresh-director 需要确定增量爬取范围时调度此技能。
type: skill
---

# 增量 Delta 计算器

## Goal

对比最新排名与已有数据，计算出增量更新需要处理哪些 ASIN。

## Inputs

- `workspace` — workspace 绝对路径
- `browse_node_id` — 类目 ID

## Preconditions

- `{workspace}/categories/{browse_node_id}/current_ranking.json` 存在（R-Step 2a 已更新）
- `{workspace}/products/` 目录存在

## Step-by-step Procedure

1. 读取 `{workspace}/categories/{browse_node_id}/current_ranking.json`
2. 提取 `asins` 字段，得到 `{ASIN: rank}` 映射
3. 将 ASIN 分为两组：
   - **在榜 ASIN**：rank >= 1
   - **跌出 ASIN**：rank = -1
4. 扫描 `{workspace}/products/` 目录，列出所有已有 `product.html` 的 ASIN
5. 对比在榜 ASIN 与已有 products，计算：
   - **新增 ASIN**：在榜但 products/ 下没有 product.html 的 ASIN → 需要爬取详情页
   - **不变 ASIN**：在榜且 products/ 下已有 product.html 的 ASIN → 跳过
6. 对于新增 ASIN，构造其 canonical URL：
   - 格式：`https://www.amazon.com/dp/{ASIN}`
   - 如果 `current_ranking.json` 或 `rankings.jsonl` 中包含 `canonical_url`，优先使用原始 URL

## Output Artifacts

不写文件。直接向调用方（refresh-director）返回以下信息：

```
Delta 计算结果：
- 在榜 ASIN 总数：{N}
- 新增 ASIN（需爬取详情页）：{list} — 共 {M} 个
- 跌出 ASIN（rank=-1，禁止分析）：{list} — 共 {K} 个
- 不变 ASIN（已有数据，跳过）：{list} — 共 {L} 个

新增 ASIN 的 URL 列表：
- https://www.amazon.com/dp/{ASIN_1}
- https://www.amazon.com/dp/{ASIN_2}
- ...
```

## Failure Handling

- `current_ranking.json` 不存在或格式错误 → **FAIL**，报告错误，refresh-director 应回退到全量模式
- `products/` 目录不存在 → **FAIL**，说明全量数据缺失
- `current_ranking.json` 中 `asins` 字段为空 → **WARN**，可能是爬取失败，报告异常但不阻塞

## Done Criteria

- 已完成在榜/跌出/新增/不变四组 ASIN 的分类
- 已输出新增 ASIN 的 URL 列表（如果新增为空则明确告知"无需爬取新增详情页"）

## Handoff Contract

refresh-director 拿到 delta 结果后：
- 如果新增 ASIN 列表非空 → 调用 `crawl_product_details` 爬取这些 ASIN
- 如果新增 ASIN 列表为空 → 跳过 R-Step 2b，直接进入 R-Step 3
- 跌出 ASIN 列表 → 传递给 analyst agents 作为访问控制依据
