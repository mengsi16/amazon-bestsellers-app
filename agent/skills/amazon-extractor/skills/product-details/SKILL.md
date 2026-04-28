---
name: product-details-extractor
description: Product Details 块提取器。从 product_details.html 中提取规格参数表、Best Sellers Rank、Warranty 信息，输出 product_details_extracted.md。支持两种 DOM 模板的 fallback。当 agent 需要提取商品详情块时调度此技能。
type: skill
---

# Product Details 提取器

**输入**：`product_details.html`
**输出**：`product_details_extracted.md`
**复杂度**：简单，无子阶段

重点是**清洗 DOM 噪声**，然后提取表格数据。

## 目标字段

- 结构化规格参数（按 section 分组的 Key-Value 表）
- Best Sellers Rank
- Warranty & Support 信息

## 两种模板的 fallback

- 主模板：`#productDetails_feature_div` — 多个 `<table>` 按 section 分组
- 备选模板：`#detailBullets_feature_div` — `<ul>` 列表格式
- 其它模版主动探测发现，直至 TDD 测试通过，获取到正确合理的结构化数据（除非没有）

## 输出格式

```markdown
# Product Details

## Structured Sections

### Section Name

| Field | Value |
| --- | --- |
| Key1 | Val1 |
| Key2 | Val2 |

## Warranty & Support

- Policy 1: ...
- Policy 2: ...
```

## 产出

- `chunker/product_details_extract.py` — 商品详情提取器实现
- `tests/test_product_details_extract.py` — 商品详情提取器测试
