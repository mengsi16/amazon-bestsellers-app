---
name: ppd-extractor
description: PPD（Product Page Data）块提取器。从 ppd.html 中提取 Core/Buybox/Twister/Overview-Bullets（含 Voyager 双路径 fallback）/Images 等子阶段的结构化数据，输出 ppd_extracted.md。当 agent 需要提取 PPD 块时调度此技能。
type: skill
---

# PPD 提取器

**输入**：`ppd.html`
**输出**：`ppd_extracted.md`
**复杂度**：复杂，6+ 个子阶段

> ⚠️ **Amazon 产品页存在两套互斥的 DOM 结构**，同一产品只会出现其中一套：
> - **旧版**：`#productOverview_feature_div`（KV 表格）+ `#feature-bullets`（bullet 列表）
> - **Voyager 版**：`#voyagerNorthstarATF` 容器，内含 `#topHighlight`、`#item_details`、`#productFactsDesktopExpander` 等
>
> 提取器必须支持双路径 fallback，优先检测 Voyager 结构，不存在时回退旧版路径。

## 子阶段与目标字段

### A1. Core

- Title
- Average stars
- Rating count
- Current price
- Original/List price
- Discount %
- Discount amount
- 自行发现其他可能的路径

### A2. Buybox

- Merchant ID
- Availability
- Quantity options
- Ships from / Sold by
- Returns / Payment / Packaging（若存在）
- 自行发现其他可能的路径

### A3. Style Options (Twister)

- Current selection
- 每个变体：Option name / price / discount / stock / prime status
- 输出为表格
- 自行发现其他可能的路径

### A4. Product Overview / Features & Specs（双路径）

Amazon 产品页存在两套互斥的 DOM 结构，提取时必须先检测再提取：

**路径 A — Voyager 版**（检测 `#voyagerNorthstarATF` 存在时使用）：
- `#topHighlight`：Top Highlight 区域，通常是 KV 对（材质/护理说明/产地/版型等），格式多变
- `#productFactsDesktopExpander`：Features & Specs 折叠面板，内容可能是 KV 表格、分点文本、或混合格式
- `#item_details`：Item Details 区域，包含品牌/型号/UPC/BSR 等详细属性
- ⚠️ Voyager 版中 `#productOverview_feature_div` 和 `#feature-bullets` **不存在**，不要尝试

**路径 B — 旧版**（Voyager 不存在时使用）：
- `#productOverview_feature_div`：Key-Value 表格（品牌/材质/颜色/尺寸等）
- `#feature-bullets`：Feature Bullets 列表

**检测逻辑**：先查 `#voyagerNorthstarATF`，存在则走路径 A，否则走路径 B。

⚠️ **Voyager 内容格式高度多变**：有的产品 Top Highlight 是纯 KV 对，有的是分点文本；Features & Specs 可能是手风琴折叠（`#voyagerAccordian_feature_div`）内含多个子面板，每个子面板可能是表格或文本。提取时必须根据实际 DOM 结构灵活处理，不能用固定正则。

- 自行发现其他可能的路径

### A5. Feature Bullets（仅旧版）

- `#feature-bullets` 下的 `<li>` 列表
- 过滤掉过短的噪声项（< 15 字符）
- ⚠️ Voyager 版产品没有此区域，Feature Bullets 内容已整合到 A4 的 Top Highlight / Features & Specs 中
- 自行发现其他可能的路径

### A6. Image Assets

- 主图 + 缩略图 URL 列表
- 从 `#altImages` 或 `#imageBlock` 中提取
- 自行发现其他可能的路径

## 提取策略要求

参照 `ppd_agent_prompt_zh.md` 的完整规范：
1. **结构诊断**：描述该类目 PPD 的主要语义块及变体风险
2. **主备路径**：每个字段至少有主 selector + 备选 selector + 兜底规则
3. **价格口径**：明确当前价、原价、折扣、优惠金额的计算逻辑
4. **去重清洗**：价格文本中的重复片段（如 `$33.99 $ 33 . 99`）、空值处理
5. **缺失字段**：统一写 `N/A`，不得臆造
6. **自行发现其他可能的路径**

### ⚠️ 价格必填约束（Hard Rule）

**`Current price` 不允许为 `N/A`，必须提取到真实的购买价格。**

提取优先级（逐级 fallback）：
1. **Core 区域**的价格节点（常见 selector：`#corePrice_feature_div`, `.a-price .a-offscreen` 等）
2. **Buybox 区域**的价格节点（`#buybox`, `#newBuyBoxPrice`, `#price_inside_buybox` 等）
3. **Twister / 变体表**中当前选中项的价格
4. 自行探测页面中其他包含价格的区域

如果所有路径均未命中，才写 `N/A` 并在 manifest 中标记 `price_missing: true` 以便人工复查。

## 输出格式

### 旧版产品（无 Voyager）

```markdown
# PPD Extracted

## Core

- Title: ...
- Average stars: ...
- Rating count: ...
- Current price: $xx.xx
- Original/List price: $xx.xx
- Discount: xx%
- Discount amount: $x.xx

## Buybox

- Merchant ID: ...
- Availability: ...
- Quantity options: 1, 2, 3, ...
- Ships from: ...
- Sold by: ...

## Style Options (Twister)

- Current selection: ...

| Option | Current Price | List Price | Discount | Prime | Status |
| --- | --- | --- | --- | --- | --- |
| ... | ... | ... | ... | ... | ... |

## Product Overview

| Field | Value |
| --- | --- |
| Brand | ... |
| Material | ... |

## Feature Bullets

- Bullet 1 text...
- Bullet 2 text...

## Image Assets

- https://...jpg
- https://...jpg
```

### Voyager 版产品

```markdown
# PPD Extracted

## Core

- Title: ...
- Average stars: ...
- Rating count: ...
- Current price: $xx.xx
- Original/List price: $xx.xx
- Discount: xx%
- Discount amount: $x.xx

## Buybox

- Merchant ID: ...
- Availability: ...
- Quantity options: 1, 2, 3, ...
- Ships from: ...
- Sold by: ...

## Style Options (Twister)

- Current selection: ...

| Option | Current Price | List Price | Discount | Prime | Status |
| --- | --- | --- | --- | --- | --- |
| ... | ... | ... | ... | ... | ... |

## Top Highlight

| Field | Value |
| --- | --- |
| Fabric type | ... |
| Care instructions | ... |
| Origin | ... |
| Fit type | ... |

## Features & Specs

<!-- 格式多变：可能是 KV 表格、分点文本、或混合。根据实际 DOM 结构输出 -->

| Field | Value |
| --- | --- |
| ... | ... |

## Item Details

| Field | Value |
| --- | --- |
| Brand Name | ... |
| Model Name | ... |
| Unit Count | ... |
| UPC | ... |
| Best Sellers Rank | ... |
| ASIN | ... |

## Image Assets

- https://...jpg
- https://...jpg
```

> ⚠️ **Voyager 版的 Features & Specs 格式高度多变**：有的产品是 KV 表格，有的是分点文本（带编号或 bullet），有的是混合格式。提取时必须根据实际 DOM 灵活处理，不能硬套固定格式。如果内容是分点文本，就用 bullet 列表输出；如果是表格，就用 Markdown 表格；如果是混合，就分段输出。

## 产出

- `chunker/ppd_extract.py` — PPD 提取器实现
- `tests/test_ppd_extract.py` — PPD 提取器测试
