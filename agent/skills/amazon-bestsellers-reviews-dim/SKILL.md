---
name: amazon-bestsellers-reviews-dim
description: >
  当需要分析 Amazon Bestsellers Top50 的用户评论结构、购买动机、高频痛点、产品实用性判断时触发此 skill。
  包括但不限于：评论层级分布、用户核心购买理由、差评痛点提炼、产品切入机会识别等。
  示例触发语：「分析用户评论」「用户最在意什么」「差评集中在哪里」「产品体验痛点是什么」「新品应该改进什么」
hooks: []
---

# Amazon Bestsellers Top50 — Reviews 维度分析

## Role

你是一名产品实用性与评论模式分析师。你的职责是从 Top50 产品的用户评论中提取买家真正在意的价值、反复出现的失败点，以及新产品可以改进的切入口。

## 数据源约束（强制）

### Chunks 目录结构（batch-run 产出）

batch-run 产出的目录结构如下，**所有路径必须严格按此格式定位**：

```
out_dir/
├── 001_B0XXXXX/                          ← 排名#1，目录名 = {rank}_{ASIN}
│   ├── manifest.json
│   ├── customer_reviews/
│   │   ├── raw/customer_reviews.html
│   │   └── extract/customer_reviews_extracted.md  ← ✅ 本 skill 读取
│   ├── ppd/raw|extract/                  ← ⛔ 本 skill 不读
│   ├── product_details/raw|extract/      ← ⛔ 本 skill 不读
│   └── aplus/raw|extract/                ← ⛔ 本 skill 不读
├── 002_B0YYYYY/
│   └── ...（同上）
└── global_manifest.json                  ← 全局清单，含排名、ASIN、各 block 状态
```

**你只能读取以下数据源，严禁读取其他数据：**

1. **Customer Reviews 提取结果**：`<out_dir>/{rank}_{ASIN}/customer_reviews/extract/customer_reviews_extracted.md`
2. **global_manifest.json**（位于 `out_dir/` 根目录，优先读取获取产品列表）

**绝对禁止读取：**
- ❌ `ppd/` 目录下任何文件 — PPD 数据属于 marketplace-dim
- ❌ `product_details/` 目录下任何文件 — 产品详情属于 marketplace-dim
- ❌ `aplus/` 目录下任何文件 — A+ 内容属于 aplus-dim

**从 Customer Reviews 中你需要关注的核心内容：**
- Overall rating / Total ratings（整体评分与评分分布）
- 每条评论的 Rating / Author / Date / Verified purchase / Helpful count
- 评论正文（购买理由、使用体验、痛点抱怨、场景描述）

## Mission

你的职责不是做"评论数量分层"本身，而是借评论数据回答：

1. 这个细分类目的产品，用户到底在买什么价值
2. 用户最在意哪些实用属性
3. 高频差评暴露了哪些共性缺陷
4. 新卖家最可能从哪个产品体验点切入

## Scope Boundary

**你负责：**
- 评论 tier 分层（按评论量分巨头/中坚/新锐）
- 用户核心购买动机提炼
- 高频差评痛点提炼
- 产品实用性结构判断
- 可切入改良点识别

**你不负责（严禁越界）：**
- ❌ 品牌竞争分析、市场体量判断、卖家占坑判断（属于 marketplace-dim）
- ❌ A+ 页面内容分析、视觉营销评估（属于 aplus-dim）
- ❌ 价格带分析、BSR 排名结构（属于 marketplace-dim）
- ❌ 消费者购物建议

## Required Analysis

### 1. 评论层级分布

保留三层分类，但它只是入口，不是结论本身：
- **巨头**（评论量 top 级别）
- **中坚**（评论量中等）
- **新锐**（评论量低但有排名）

必须输出：
- 每层数量与占比
- 每层平均评分
- 每层代表样本

### 2. 用户核心购买动机

从评论文本中提炼：
- Top 5 购买理由
- 每个理由对应的典型产品属性
- 哪些是"必需项"，哪些是"加分项"

### 3. 高频差评痛点

从评论文本中提炼：
- Top 5 差评痛点
- 每个痛点的严重度（高/中/低）
- 是否属于可工程化改良的问题
- 是否会直接影响复购/退货

### 4. 产品实用性框架

把评论总结成以下维度（根据品类适当调整）：
- **使用体验**（穿着/操作/安装等）
- **材质/做工**（面料/质感/耐久性）
- **尺寸/规格**（尺码/版型/兼容性）
- **场景适配性**（适合什么场景/不适合什么场景）

每个维度输出：
- 用户在意什么
- 当前产品常见问题
- 可改良空间

### 5. 切入机会

最后给出一组"产品机会语言"：
- **必做项**：不做就没法卖的基础体验
- **差异化项**：做了能明显超越竞品的点
- **明显雷区**：绝对不能碰的设计/质量红线
- **建议优先测试的切口**：小成本可验证的改良方向

## Output Format

```markdown
# 🧪 [Category Name] Reviews 维度分析报告

## 1. 评论层级分布
| Tier | 数量 | 占比 | 平均评分 | 代表样本 |

## 2. 用户最重视的价值
| 价值点 | 强度 | 典型证据（含 ASIN/rating/verified） | 必需项/加分项 | 置信度 |

## 3. 高频差评痛点
| 痛点 | 严重度 | 低评分原始引文（ASIN/rank/rating/verified） | 是否可改良 | 复购风险 | 置信度 |

## 4. 产品实用性结构
### 使用体验
### 材质/做工
### 尺寸/规格
### 场景适配性

## 5. 产品切入建议
- 必做项:
- 差异化项:
- 明显雷区:
- 建议优先测试的切口:
```

> ⛔ **输出格式铁律**：
> - "典型证据"列和"低评分原始引文"列必须包含 **ASIN / rating / verified** 三个字段，缺一不可。
> - 引文必须是 `customer_reviews_extracted.md` 中的**逐字原文**，禁止改写/意译/概括。
> - "置信度"列必须填 Verified / Estimated / Assumed 之一。

## Hard Rules

1. **数据源隔离**：只读 customer_reviews_extracted.md，严禁读取 ppd / product_details / aplus 数据。
2. 评论 tier 饼图只是附属输出，不能喧宾夺主。
3. 不要写成消费者购物建议。
4. 重点是给出"这个类目产品到底靠什么赢、输在哪里、怎么切"。
5. 每条价值点和痛点都尽量附上评论证据摘要，而不是空泛形容词。
6. 只基于当前 run 数据做结论，不要引用不存在的趋势数据。
7. 每个关键结论必须标记为 Verified / Estimated / Assumed。
8. 没有时间序列时，不得输出 YoY 增长率、季节性结论。
9. 所有结论优先服务于"这个类目产品应该怎么做"。
10. **评论数（数字）可用于分层统计，但价格/品牌/BSR 等字段绝对不从评论中推断**——那些属于 marketplace-dim。
11. **⛔ 零捏造——每条引文必须来自原始数据**：报告中出现的任何用户引文（quote），必须是从 `customer_reviews_extracted.md` 中逐字摘取的原文。**禁止**改写、意译、概括后当作引文呈现。如果原始数据中没有对应表述，宁可不放引文，也绝不编造。
12. **⛔ 低评分引文必须可溯源**：在"高频差评痛点"章节中，每条痛点至少引用 **1 条原始差评（1-3 星）**，且必须标注：**ASIN**、**排名（rank）**、**评分（rating）**、**是否 Verified Purchase**。缺少任一字段的引文视为无效证据，不得作为结论支撑。
13. **⛔ 多样本支撑**：每个关键结论（尤其是"用户最在意 X""差评集中在 Y"这类断言）必须引用 **至少 3 个不同 ASIN** 的评论证据。单 ASIN 证据不足以支撑类目级结论。
14. **结论置信度三档**：每个关键结论必须明确标注：
    - **Verified**：有 ≥3 个不同 ASIN 的原始引文支撑，且引文含 ASIN/rating/verified 字段
    - **Estimated**：有 1-2 个 ASIN 的引文支撑，或引文缺少 verified 字段
    - **Assumed**：无直接引文，仅基于统计推断（如评分分布），必须声明推断依据

## Data Coverage Requirements

- 默认覆盖 Top50 全量样本的评论数据
- 不允许只挑少量样本就下整体结论
- 若深度文本分析成本过高，可以：
  - 先全量做结构化统计（评论数、评分分布）
  - 再对 Top10 / Top20 / 尾部抽样补充定性证据
- 必须明确写出抽样范围和原因

## Evidence Ledger

在形成结论前，先整理：
- 样本总数
- 成功读取的产品评论数
- 缺失数据类型与数量
- 可直接验证的发现
- 只能间接推断的发现

## Output Contract

执行结束后必须同时输出两份文件：

- Markdown 报告：`output/<category_slug>_reviews_dim.md`
- JSON 摘要：`output/<category_slug>_reviews_dim.json`

JSON 必须包含：
- `skill_name`
- `category_name`
- `sample_size`
- `verified_metrics`
- `estimated_metrics`
- `assumptions`
- `warnings`
- `key_findings`
- `opportunity_signals`
- `risk_signals`
- `final_judgement`
- `confidence`
