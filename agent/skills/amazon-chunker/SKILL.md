---
name: amazon-chunker
description: Amazon 商品完整 HTML 页面的分块器。读取 ≈2MB 的完整详情页 HTML，按稳定 DOM id 切出 4 个子 HTML（ppd / customer_reviews / product_details / aplus）。当 agent 需要对完整 HTML 进行分块切分时调度此技能。
type: skill
---

# 分块器（Chunker）

## 任务

读取完整 HTML，找出 Amazon 稳定的大块 DOM `id`，写好分块器（`chunker.py`），利用分块器切出 4 个子 HTML 文件，并验证切分是否正确合理。

## 常见目标块

| 块名 | 主 selector | 备选 selector | 内容 |
|------|------------|--------------|------|
| `ppd` | `#ppd` | `#dp-container` | 标题/价格/评分/buybox/变体/卖点/图片 |
| `customer_reviews` | `#customerReviews` | `#reviewsMedley` | 评分分布 + 用户评论 |
| `product_details` | `#productDetails_feature_div` | `#detailBullets_feature_div` | 规格参数表 |
| `aplus` | `#aplus` | `#aplusBrandStory_feature_div` | A+ 品牌内容/对比表/图片 |

## 分块规则

1. 用 `BeautifulSoup(html, "lxml")` 解析完整 HTML
2. 对每个块，按主 selector → 备选 selector 顺序尝试 `soup.select_one()`
3. 命中后，**移除所有 `<script>` 标签**，其他原样保留
4. 输出为独立 HTML 文件：`<block_name>.html`
5. 未命中的块记录到 manifest，不报错

## 输入来源

MCP scraper 产出：`{workspace}/products/{ASIN}/product.html`

## 分块器工作步骤

1. 读取 `products/{ASIN}/product.html` 样本文件（优先 Top1/Top25）
2. 用 Python 脚本探测所有带 `id` 的顶层节点，对比多个样本找出共有块
3. 确认 4 个目标块的 selector 在所有样本上都能命中
4. 如果发现某个块使用了备选 selector，记录下来
5. 根据实际 HTML 样本确定 `chunker/static_chunker.py` 中每个 block 的 selector，生成分块器代码 + 测试

## 产出

- `chunker/static_chunker.py` — 分块器实现
- `tests/test_static_chunker.py` — 分块器测试
