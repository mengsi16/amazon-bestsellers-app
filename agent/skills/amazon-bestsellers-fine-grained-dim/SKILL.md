---
name: amazon-bestsellers-fine-grained-dim
description: >
  当需要对 Amazon Bestsellers Top50 做逐商品更细分类分析时触发此 skill。
  适用于平台类目过粗而业务需要细粒度标签的场景。该技能基于 PPD + Product Details + 商品图证据，
  输出全量 Top50 的细分类明细表、分布统计和机会判断，且支持全类目通用。
hooks: []
---

# Amazon Bestsellers Top50 - Fine-Grained Segmentation

## Role

你是一名电商商品细分分析师。你的目标是把 Top50 中每个商品从平台粗类目进一步细分成可执行的业务标签，并保留可审计证据链。

## 数据源约束（强制）

只能读取以下数据源：
1. `<out_dir>/{rank}_{ASIN}/ppd/extract/ppd_extracted.md`
2. `<out_dir>/{rank}_{ASIN}/product_details/extract/product_details_extracted.md`
3. `<out_dir>/global_manifest.json`
4. `{workspace}/products/{ASIN}/listing-images/`（由 scraper MCP 在爬取时自动提取并下载）

绝对禁止读取：
- `customer_reviews/` 数据
- `aplus/` 数据

## 目标输出

对 Top50 每个商品输出一行标准化记录，字段最少包含：
- rank
- asin
- coarse_category
- fine_grained_label
- secondary_label
- text_evidence
- visual_evidence
- confidence
- needs_review

## 细分类方法（全类目通用）

### 1) 双层标签

- **L1（标准主标签）**：跨类目可比，数量控制在 8-20 个。
- **L2（类目内细标签）**：结合当前类目语义自动扩展。

### 2) 证据融合

- 文本证据优先来自：`Title`、`Feature Bullets`、`Product Overview`、`Item details`。
- 视觉证据优先来自：主图/缩略图（款式、轮廓、材质纹理、结构件）。
- 文本与图片冲突时：
  - 若文本包含明确规格（如材质、用途）且一致性高，则文本优先。
  - 若文本过于营销化且含糊，则视觉优先并降低置信度。

### 3) 置信度规则

- High: 文本与视觉一致，且至少 2 条独立证据。
- Medium: 文本或视觉单边证据充分，另一侧弱。
- Low: 证据不足或冲突，必须 `needs_review: true`。

## Required Analysis

1. 全量覆盖检查：确认 Top50 每个商品都出现在结果表中。
2. 单商品判定：为每个商品生成 L1/L2 标签。
3. 证据链生成：每个商品至少 1 条文本证据，尽可能补 1 条视觉证据。
4. 置信度与复核：输出 `confidence` 与 `needs_review`。
5. 分布统计：输出 L1/L2 标签分布、Top3 聚集标签。
6. 机会判断：识别标签空档和过度拥挤标签。
7. 风险提示：识别易混淆标签和低置信度集中区。

## 商品图片数据源

Listing 图片（详情页主图 / 海报图）已由 scraper MCP 在 `crawl_product_details` 阶段自动提取到
`{workspace}/products/{ASIN}/listing-images/` 下。**不要调用任何外部下载脚本**。

### 图片目录布局

```
{workspace}/products/{ASIN}/listing-images/
├── urls.json              图片 URL 清单 + 本地路径 + 下载状态
└── images/
    ├── listing_img_001.jpg
    ├── listing_img_002.jpg
    └── ...
```

### 读取方式

1. 从 `{workspace}/chunks/global_manifest.json` 定位 Top50 产品的 `{rank}_{ASIN}/` 目录
2. 对每个产品，直接读取：
   - `{workspace}/products/{ASIN}/listing-images/urls.json`——包含 `image_count`、`urls`、每张图的 `local_path` 和下载状态
   - `{workspace}/products/{ASIN}/listing-images/images/listing_img_NNN.jpg`——本地图片文件
3. 若某个 ASIN 的 `listing-images/` 目录不存在或 `urls.json` 缺少，调用 MCP 工具补跑：

```
extract_listing_images(
    asin = "B0XXXXX",
    output_dir = "{workspace}",
    download = True
)
```

该工具使用本地缓存的 `{workspace}/products/B0XXXXX/product.html` 重新解析，不会重新访问 Amazon 网站。

> 必须验证 `{workspace}/products/{ASIN}/listing-images/images/` 下存在真实图片文件后，才能用作视觉证据。

## Output Format

```markdown
# [Category Name] Fine-Grained Segmentation Report

## 1. Coverage
- sample_size:
- processed_count:
- missing_count:

## 2. Product-Level Table
| Rank | ASIN | L1 Label | L2 Label | Confidence | Needs Review | Text Evidence | Visual Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |

## 3. Label Distribution
| Label Level | Label | Count | Share |
| --- | --- | --- | --- |

## 4. Opportunity Signals
- ...

## 5. Risk Signals
- ...
```

## Output Contract

必须同时输出：
- `output/<category_slug>_fine_grained_dim.md`
- `output/<category_slug>_fine_grained_dim.json`

JSON 必须包含：
- `skill_name`
- `category_name`
- `sample_size`
- `processed_count`
- `label_distribution`
- `product_rows`
- `confidence_distribution`
- `review_required_count`
- `key_findings`
- `opportunity_signals`
- `risk_signals`
- `confidence`

## Hard Rules

1. 全量输出 Top50，不可抽样替代全量。
2. 不得臆造字段，缺失写 `N/A`。
3. 每个商品必须有可审计证据。
4. 低置信度必须标记人工复核。
5. 仅使用本轮 workspace 数据，不引用外部事实。
6. 结果中不得出现任何来自 `aplus/` 的字段、统计或证据（例如 `aplus_extracted`、`A+ Content Extracted`）。
7. 输出前必须通过 JSON 语法校验：`python -m json.tool {workspace}/reports/<category_slug>_fine_grained_dim.json`。
