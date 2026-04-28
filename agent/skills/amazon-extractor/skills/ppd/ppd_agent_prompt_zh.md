# PPD 通用提取器生成提示词（类目版）

## 目的
- 输入：某个类目下一个或少量代表性商品的 PPD 原始 HTML。
- 输出：该类目可复用的“通用提取器”方案与实现（代码 + 规则 + 测试），而不是单商品抽取结果。

---

## 提示词模板（可直接给 agent）

你是一个电商数据抽取工程师。

请根据我提供的某类目商品 PPD 原始 HTML，设计并生成该类目的“通用提取器”。

重点要求：
1. 你的任务不是输出本次 HTML 的抽取结果，而是输出可复用的提取器实现。
2. 不要依赖我手工给出的 selector 列表。
3. 允许在实现中使用必要的 DOM 定位，但必须由你基于语义与结构自动归纳，并提供回退策略。

### 目标字段（语义块）
1. Core
   - Title
   - Average stars
   - Rating count
   - Current price
   - Original/List price
   - Discount
   - Discount amount
2. Buybox
   - Availability
   - Ships from
   - Sold by
   - Quantity options
   - Returns / Payment / Packaging（若存在）
3. Style Options (Twister)
   - Current selection
   - Option name / price / discount / stock / prime status
4. Product Overview（Key-Value）
5. Feature Bullets
6. Image Assets

### 产出要求
请按以下顺序输出：

1. **类目页面结构诊断**
   - 描述该类目 PPD 的主要语义块及其变体风险。
   - 说明哪些字段在不同商品中最可能缺失或位置变化。

2. **通用提取策略设计**
   - 字段识别优先级（主路径、备选路径、兜底规则）。
   - 价格口径定义（当前价、原价、折扣、优惠金额的计算规则）。
   - 去重与清洗规则（重复文本、噪声文案、空值处理）。

3. **提取器实现代码（Python）**
   - 输出一个完整可运行文件，建议命名：`<category>_ppd_extractor.py`。
   - 输入：`ppd.html`
   - 输出：`ppd_extracted.md`
   - 代码中必须包含：
     - 字段提取主函数
     - 关键子模块（价格、buybox、twister、overview、bullets、images）
     - 最少的 CLI 入口

4. **测试样例（unittest）**
   - 生成至少 1 个测试文件草案，验证核心字段与输出结构。
   - 测试重点：字段存在性、价格逻辑、缺失字段 N/A 处理。

5. **维护说明**
   - 当类目页面结构变化时，应优先调整哪些规则。
   - 如何最小代价扩展到新类目。

### 质量标准
1. 不能把“这个样本页面恰好如此”当作唯一规则。
2. 必须提供主备路径，避免单点失效。
3. 输出字段缺失时统一写 `N/A`，不得臆造。
4. 说明中要明确“通用性边界”。

---

## 建议输入格式

```text
[CATEGORY]
<类目名称>

[INPUT HTML START]
...ppd raw html...
[INPUT HTML END]

[OPTIONAL NOTES]
- 当前项目语言：Python
- 期望输出：Markdown
```

---

## 多样本增强版（推荐）

如果你有同类目多个商品 HTML，建议一次提供 3-5 个样本，提示词可改为：

"请先做结构共性分析，再输出通用提取器。对仅在个别样本出现的结构，标记为可选路径，不要当作主路径。"

