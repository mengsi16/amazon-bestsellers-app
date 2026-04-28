---
name: customer-reviews-extractor
description: Customer Reviews 块提取器。从 customer_reviews.html 中提取评分摘要和逐条评论，输出 customer_reviews_extracted.md。此块 DOM 结构高度一致，强制复制模版。当 agent 需要提取评论块时调度此技能。
type: skill
---

# Customer Reviews 提取器

**输入**：`customer_reviews.html`
**输出**：`customer_reviews_extracted.md`
**复杂度**：简单，无子阶段

## ❗ 参考模版（Hard Rule）

**此块在所有商品间 DOM 结构高度一致，提供已验证的参考实现。**

参考模版位于本 skill 同级目录：`customer_reviews_extract.py`

### 使用方式

1. **先读取本目录下的 `customer_reviews_extract.py`**，完整理解其代码结构、Selector 选择、清洗逻辑
2. **以此为参考**编写 `chunker/customer_reviews_extract.py`，保持相同的架构和 Selector 策略
3. **必须严格通过 TDD 测试**——不是无脑复制，而是参考其结构后编写，确保所有测试用例通过
4. 测试失败时可微调实现，但**不得重写核心 Selector 和提取逻辑**
5. **严禁**使用 `[class*='review']` 等通配符 selector，必须使用精确的 `data-hook` 属性选择器

## 必须使用的精确 Selector（来自模版，不得替换）

| 字段 | Selector | 说明 |
| --- | --- | --- |
| 评论列表容器 | `li.review.aok-relative` | 每条评论的外层容器 |
| 评论标题 | `[data-hook="review-title"]` | 内层取最后一个非空 span |
| 评分 | `[data-hook="review-star-rating"]` 或 `[data-hook="cmps-review-star-rating"]` | 星级文本 |
| 作者 | `.a-profile-name` | 用户名 |
| 日期 | `[data-hook="review-date"]` | 含国家和日期 |
| 已认证购买 | `[data-hook="avp-badge"]` | 存在即 Yes |
| 有用投票 | `[data-hook="helpful-vote-statement"]` | 有用计数 |
| 评论正文 | `[data-hook="review-collapsed"]` → `[data-hook="review-body"]` | fallback 顺序 |
| 总体评分 | `[data-hook="rating-out-of-text"]` | "x.x out of 5" |
| 总评分数 | `[data-hook="total-review-count"]` | "N global ratings" |
| 评分分布 | `#histogramTable a[aria-label]` | aria-label 属性值 |

重点是**清洗 DOM 噪声**（`<br>`, `<span>` 嵌套, `Read more` 链接等），然后直接提取。

## 目标字段

- Overall rating
- Total ratings count
- Rating distribution（5→1 星各占百分比）
- Review items：逐条提取 title / rating / author / date / verified / helpful count / body

## 输出格式

```markdown
# Customer Reviews

## Summary

- Overall rating: x.x out of 5
- Total ratings: N global ratings
- Rating distribution:
  - 73 percent of reviews have 5 stars
  - ...

## Review Items (N)

### 1. Review Title
- Rating: x.x out of 5 stars
- Author: ...
- Date: ...
- Verified purchase: Yes/No
- Helpful: N people found this helpful

Review body text...
```

## 产出

- `chunker/customer_reviews_extract.py` — 评论提取器（从模版复制）
- `tests/test_customer_reviews_extract.py` — 评论提取器测试
