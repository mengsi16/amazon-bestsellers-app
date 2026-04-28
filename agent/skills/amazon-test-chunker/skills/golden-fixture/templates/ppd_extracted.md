<!-- PPD Golden Template — 引导性模板，不写死字段 -->
<!-- LLM 读取 ppd.html 后，按实际内容自行组织章节 -->
<!-- 核心原则：有什么输出什么，不要遗漏，不要臆造 -->

# PPD Extracted

<!-- Core 信息：标题、价格、评分等基础字段 -->
## Core

- Title: ...
- Average stars: ...
- Rating count: ...
- Current price: ...
- Original/List price: ...
- Discount: ...
- Discount amount: ...

<!-- Buybox 信息：卖家、库存、配送等 -->
## Buybox

- Merchant ID: ...
- Availability: ...
- Ships from: ...
- Sold by: ...

<!-- 变体/款式选项 -->
## Style Options (Twister)

- Current selection: ...

| Option | Current Price | List Price | Discount | Prime | Status |
| --- | --- | --- | --- | --- | --- |
| ... | ... | ... | ... | ... | ... |

<!-- 产品属性区域：根据实际 DOM 结构输出 -->
<!-- 旧版产品会有 #productOverview_feature_div (KV表格) + #feature-bullets (bullet列表) -->
<!-- Voyager 版产品会有 #topHighlight + #productFactsDesktopExpander + #item_details -->
<!-- ⚠️ 不要预设结构，根据 HTML 实际有什么就输出什么 -->
<!-- KV 对用 Markdown 表格，分点文本用 bullet 列表，混合格式分段输出 -->

## 产品属性

<!-- （LLM 自行发现并命名章节，有什么输出什么） -->

## Feature Bullets / About This Item

<!-- 如果存在 bullet 列表就输出，不存在就写 N/A -->

## Image Assets

- https://...
