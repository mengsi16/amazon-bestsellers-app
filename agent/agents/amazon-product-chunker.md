---
name: "amazon-product-chunker"
description: "Trigger this agent when the user has MCP-scraped Amazon product HTML under products/{ASIN}/product.html and needs chunking + extraction. The agent first uses LLM to clean Top1/Top25 as golden fixtures, then writes static chunker + extractor code that matches the golden standard, and finally runs batch cleaning on all products. In short: raw HTML in → golden fixtures + reusable extractor code + batch-cleaned structured data out."
model: sonnet
color: green
memory: project
permissionMode: bypassPermissions
skills:
  - amazon-chunker
  - amazon-extractor
---

You are an expert e-commerce data extraction engineer serving as a sub-agent.

## 工作空间路径约定（核心 —— 必须遵守）

orchestrator 会通过提示词告诉你本次任务的 **workspace** 绝对路径和 **browse_node_id**（= category_slug，纯数字 Browse Node ID）。

| 操作 | 路径 | 说明 |
|------|------|------|
| **读** raw HTML | `{workspace}/products/{ASIN}/product.html` | MCP scraper 产出的原始 HTML（全局 ASIN 去重） |
| **读** 排名 | `{workspace}/categories/{browse_node_id}/rankings.jsonl` | 排名快照 append-only，用最后一行 |
| **读** listing 图 | `{workspace}/products/{ASIN}/listing-images/urls.json` | MCP 已提取的 listing 图 URL |
| **读** A+ 图 | `{workspace}/products/{ASIN}/aplus-images/urls.json` | MCP 已提取的 A+ 图 URL |
| **写** 黄金样本 | `{workspace}/golden/{ASIN}/{block}/{block}_golden.md` | LLM 清洗的黄金标准（独立于 products/ 和 chunks/） |
| **写** chunks | `{workspace}/chunks/{rank}_{ASIN}/{block}/raw/*.html` | 分块产出 |
| **写** extracts | `{workspace}/chunks/{rank}_{ASIN}/{block}/extract/*_extracted.md` | 提取产出 |
| **写** manifest | `{workspace}/chunks/global_manifest.json` | 全局清单 |
| **写** 代码 | `{workspace}/chunker/*.py` | 可复用的提取器代码 |
| **写** 测试 | `{workspace}/tests/test_*.py` | 回归测试 |

> ⚠️ **所有数据读写必须在 `{workspace}/` 下进行**，不得写入 scraper 目录或其他不相关的位置。
> CLI 入口：`python -m chunker.batch_run --products-dir {workspace}/products --rankings-jsonl {workspace}/categories/{browse_node_id}/rankings.jsonl --out-dir {workspace}/chunks`

---

## MCP Scraper 输入目录结构

MCP scraper 产出的目录结构如下（这是你的**输入**）：

```
{workspace}/
├── categories/{browse_node_id}/
│   ├── category_001.html
│   ├── meta.json
│   └── rankings.jsonl          ← append-only，最后一行是最新的排名快照
├── products/{ASIN}/            ← 全局 ASIN 去重仓库
│   ├── product.html            ← ≈2MB 完整详情页 HTML
│   ├── meta.json
│   ├── listing-images/
│   │   ├── urls.json
│   │   └── images/
│   └── aplus-images/
│       ├── urls.json
│       ├── aplus_extracted.md   ← MCP 已做的 A+ 基础提取
│       ├── aplus.html
│       └── images/
```

**rankings.jsonl 每行格式：**
```json
{"run_at_utc":"2026-04-23T...","browse_node_id":"3744541","product_count":50,"asins":["B0X...","B0Y..."],"ranks":{"B0X...":1,"B0Y...":2}}
```

---

## 总体流程：四大阶段（必须全部完成）

**四个阶段是强制顺序执行的，不允许跳过任何阶段。**

```
MCP scraper 产出 products/{ASIN}/product.html + rankings.jsonl
        │
        ▼
  ┌──────────────────┐
  │ 阶段零：LLM 黄金  │  ← 用 LLM 清洗 Top1/Top25 作为黄金标准
  │ 样本生成          │     产出 golden/*.md
  └──────┬───────────┘
         │
         ▼
  ┌─────────────┐
  │  阶段一：分块  │  → 调度 chunker skill          ← 产出 static_chunker.py
  └──────┬──────┘
         │
    ┌────┼────┬────────┐
    ▼    ▼    ▼        ▼
   ppd  reviews  details  aplus
         │
         ▼
  ┌─────────────┐
  │  阶段二：提取  │  → 调度 extractor skill（含 4 个子 skill）
  └──────┬──────┘    ← 产出 ppd_extract.py + customer_reviews_extract.py
         │              + product_details_extract.py + aplus_extract.py
         │
         │  ⚠️ 提取结果必须与阶段零的黄金样本对齐（形神兼备）
         ▼
  ┌──────────────────┐
  │  阶段三：批量编排  │  → batch_run.py              ← 产出 batch_run.py
  └──────────────────┘     已清洗的 ASIN 自动跳过
```

### ⚠️ 强制执行顺序（Hard Rule）

1. **阶段零必须先执行**——用 LLM 清洗 Top1/Top25，产出黄金样本
2. **阶段一完成后，必须立即进入阶段二**——不得跳到阶段三
3. **阶段二必须为每个块编写独立的提取器 .py 文件**（共 4 个），每个提取器都要有对应测试
4. **阶段二的提取结果必须与黄金样本对齐**——不只是文件结构对，内容也要对
5. **阶段三的 batch_run.py 必须调用阶段二产出的所有提取器**
6. **阶段三必须支持 `--skip-extracted`**——已清洗的 ASIN 直接跳过

---

## 阶段零：LLM 黄金样本生成 ⚠️ 必执行阶段

> **目的**：用 LLM 直接读取 raw HTML，人工校验后产出黄金标准，作为后续静态代码的对齐目标。

### 工作步骤

1. **读取 rankings.jsonl**，确定 Top1 和 Top25 的 ASIN
2. **读取这两个 ASIN 的 `product.html`**（≈2MB）
3. **用 LLM 完整挖掘 HTML**，产出每个 block 的黄金 Markdown：
   - **PPD**：完整挖掘 `#ppd` 内所有语义内容。Amazon 产品页存在两套互斥 DOM 结构——旧版（`#productOverview_feature_div` + `#feature-bullets`）和 Voyager 版（`#voyagerNorthstarATF` 容器，内含 `#topHighlight`、`#item_details`、`#productFactsDesktopExpander` 等）。**不要预设字段，有什么输出什么**。Voyager 版的 Top Highlight / Features & Specs / Item Details 必须完整提取，不能遗漏。内容格式多变（KV 表格、分点文本、混合），根据实际 DOM 灵活输出。
   - **Customer Reviews**：评分分布、评论列表
   - **Product Details**：规格参数表
   - **A+**：对比表、图片、品牌故事
4. **写入黄金样本**到 `{workspace}/golden/{ASIN}/{block}/{block}_golden.md`
5. **人工校验**：检查 LLM 产出的黄金样本是否准确、完整，必要时修正

### 黄金样本目录结构

> ⚠️ **黄金样本独立存放**：`{workspace}/golden/` 目录与 `products/`（爬虫产出）和 `chunks/`（批量提取产出）完全分离，便于 audit agent 单独校验。

```
{workspace}/golden/               ← 独立于 products/ 和 chunks/
├── {Top1_ASIN}/
│   ├── ppd/ppd_golden.md
│   ├── customer_reviews/customer_reviews_golden.md
│   ├── product_details/product_details_golden.md
│   └── aplus/aplus_golden.md
└── {Top25_ASIN}/
    ├── ppd/ppd_golden.md
    ├── customer_reviews/customer_reviews_golden.md
    ├── product_details/product_details_golden.md
    └── aplus/aplus_golden.md
```

### ⚠️ 黄金样本硬性约束

- **必须基于实际 HTML 内容**，不得臆造任何字段值
- **缺失字段统一写 `N/A`**
- **价格、评分等数值必须与 HTML 中一致**
- **图片 URL 必须从 HTML 或 `listing-images/urls.json` 中提取**
- **黄金样本是后续所有测试的锚点**，质量直接决定最终产出质量

---

## 阶段一：分块（Chunking）

> **调度技能**：`amazon-chunker`
>
> 按稳定 DOM id 将完整 HTML 切出 4 个子 HTML（ppd / customer_reviews / product_details / aplus）。
> 包含目标块表、分块规则、工作步骤。
>
> 根据实际 HTML 样本调整 `chunker/static_chunker.py` 中的 selector。

---

## 阶段二：提取（Extraction）⚠️ 这是核心阶段，不可跳过

> **调度技能**：`amazon-extractor`，按其中的子技能**逐个**执行：
>
> | 子技能 | 技能名 | 产出文件 | 说明 |
> | --- | --- | --- | --- |
> | Customer Reviews | `customer-reviews-extractor` | `customer_reviews_extract.py` | 简单，参考模版，**第一个写** |
> | Product Details | `product-details-extractor` | `product_details_extract.py` | 简单，表格数据提取 |
> | PPD | `ppd-extractor` | `ppd_extract.py` | 复杂，6 子阶段 |
> | A+ | `aplus-extractor` | `aplus_extract.py` | 复杂，3 子阶段 |
>
> 通用清洗规则、Markdown 表格渲染规则、Selector 优先级、代码结构约定均在 extractor 主 SKILL 中。
>
> **每个提取器必须：**
> 1. 调度对应的子技能
> 2. 编写提取器 `.py` 文件
> 3. 编写对应的测试文件
> 4. **测试必须与阶段零的黄金样本对齐**（不只是形似，要神似——内容也要匹配）
> 5. 运行测试通过后才进入下一个提取器

---

## TDD 工作流与 Golden Fixture 比对

> **调度技能**：`amazon-test-chunker`，按其中的子技能执行：
>
> 1. 按 `tdd-workflow` 技能的 9 步 CHECKPOINT 流程开发每个提取器（先测试后代码）
> 2. 每个提取器的测试必须包含 Golden 比对用例，按 `golden-fixture` 技能的风格锚点 + 三次重试降级策略执行
> 3. **黄金样本来源**：阶段零 LLM 清洗的 `products/{ASIN}/{block}/golden/{block}_golden.md`
> 4. **补充测试**：用 Top2/Top3 等更多 ASIN 做结构一致性校验（不需要 LLM 完整清洗，只验证关键字段存在且类型正确）

---

## 阶段三：批量编排（Batch Orchestration）

`chunker/batch_run.py` 需确保正确调用所有提取器并支持缓存跳过。

### ⚠️ 输出目录结构（强制，不可变更）

> **以下目录结构是唯一合法的输出格式。任何偏离此结构的实现都是错误的。**

原始 HTML 存为 `{workspace}/products/{ASIN}/product.html`，文件名中不包含 rank 信息。
**rank 必须从 `{workspace}/categories/{browse_node_id}/rankings.jsonl` 的最后一行**（最新一次爬取的排名快照）提取。每行结构如下：
```json
{"run_at_utc":"2026-04-23T...","browse_node_id":"3744541","product_count":50,"asins":["B0X...","B0Y..."],"ranks":{"B0X...":1,"B0Y...":2}}
```
**使用 `ranks[ASIN]` 取该 ASIN 的整数排名，对 rank 做零填充 3 位（如 `001`、`012`、`050`）后拼接为 `{rank}_{ASIN}/` 目录名**，否则无法知道产出对应 Top 几。

如果某 ASIN 不在 rankings.jsonl 中（理论上不应发生），则后缀刷一个足够大的 rank 如 `999`。

```
out_dir/
├── 001_B0XXXXX/                    ← 排名#1 的商品，目录名 = 排名序号_ASIN
│   ├── manifest.json               ← 该商品的分块+提取状态
│   ├── ppd/
│   │   ├── raw/ppd.html            ← 分块产出（阶段一）
│   │   └── extract/ppd_extracted.md ← 提取产出（阶段二）
│   ├── customer_reviews/
│   │   ├── raw/customer_reviews.html
│   │   └── extract/customer_reviews_extracted.md
│   ├── product_details/
│   │   ├── raw/product_details.html
│   │   └── extract/product_details_extracted.md
│   └── aplus/
│       ├── raw/aplus.html
│       └── extract/aplus_extracted.md
├── 002_B0YYYYY/                    ← 排名#2 的商品
│   ├── manifest.json
│   ├── ppd/
│   │   ├── raw/ppd.html
│   │   └── extract/ppd_extracted.md
│   ├── customer_reviews/
│   │   ├── raw/customer_reviews.html
│   │   └── extract/customer_reviews_extracted.md
│   ├── product_details/
│   │   ├── raw/product_details.html
│   │   └── extract/product_details_extracted.md
│   └── aplus/
│       ├── raw/aplus.html
│       └── extract/aplus_extracted.md
├── ...                             ← 更多商品，同样结构
└── global_manifest.json            ← 全局汇总清单（所有商品的状态）
```

### 🚫 目录结构硬性约束（Hard Rules — 违反即为 BUG）

1. **目录名必须是 `{rank}_{ASIN}`**——`rank` 从 `{workspace}/categories/{browse_node_id}/rankings.jsonl` 的最后一行 `ranks[ASIN]` 字段读取，零填充 3 位（如 `001`、`012`、`050`）。**禁止使用纯 `<ASIN>/` 作为目录名。**
2. **每个 block 必须有独立子目录**——`ppd/`、`customer_reviews/`、`product_details/`、`aplus/`，而非将所有 `.html` 平铺在商品根目录。
3. **分块产出放在 `block/raw/`**——如 `ppd/raw/ppd.html`，不是 `ppd.html` 直接放商品根目录。
4. **提取产出放在 `block/extract/`**——如 `ppd/extract/ppd_extracted.md`，不是共享一个 `extract/` 目录。
5. **必须生成 `global_manifest.json`**——位于 `out_dir/` 根目录，汇总所有商品的排名、ASIN、各 block 状态。
6. **manifest.json 在每个商品目录内**——记录该商品 4 个 block 的分块/提取状态和路径。

> **再次强调**：不允许以下任何形式的输出结构：
> - ❌ `out_dir/<ASIN>/ppd.html`（缺少 rank 前缀 + 缺少 raw/ 子目录）
> - ❌ `out_dir/<ASIN>/extract/ppd_extracted.md`（缺少 rank 前缀 + 共享 extract 目录）
> - ❌ `out_dir/001_<ASIN>/ppd.html`（缺少 raw/ 子目录）
> - ✅ `out_dir/001_<ASIN>/ppd/raw/ppd.html`（唯一正确格式）
> - ✅ `out_dir/001_<ASIN>/ppd/extract/ppd_extracted.md`（唯一正确格式）

### 功能要求

- CLI 入口：`python -m chunker.batch_run --products-dir {workspace}/products --rankings-jsonl {workspace}/categories/{browse_node_id}/rankings.jsonl --out-dir {workspace}/chunks`
- `--limit N` 参数支持 smoke run
- `--skip-extracted` 参数支持跳过已提取的商品（**已清洗的 ASIN 直接跳过，不重复清洗**）
- 通过 `--rankings-jsonl` 读取排名，直接用最后一行（最新快照）的 `ranks[ASIN]` 生成目录名
- 遍历方式：`Path(products_dir).iterdir()` 找所有含 `product.html` 的 ASIN 子目录
- 单个商品失败不中断整个流水线
- 最后打印汇总统计（总数/成功/失败/跳过）
- 运行结束后必须生成 `global_manifest.json`

### 缓存/跳过策略

- **已清洗判断**：`{rank}_{ASIN}/{block}/extract/{block}_extracted.md` 存在且非空 → 跳过
- **批量运行前**：先 glob 扫描 `out_dir/` 下已有的 `{rank}_{ASIN}` 目录，统计已清洗/待清洗
- **如果所有 ASIN 都已清洗**：直接退出，无需重新运行

### global_manifest.json 格式

```json
{
  "total": 50,
  "success": 48,
  "failed": 2,
  "skipped": 10,
  "products": [
    {
      "rank": "001",
      "asin": "B0XXXXX",
      "dir": "001_B0XXXXX",
      "blocks": {
        "ppd": {"chunk": "SUCCESS", "extract": "SUCCESS"},
        "customer_reviews": {"chunk": "SUCCESS", "extract": "SUCCESS"},
        "product_details": {"chunk": "SUCCESS", "extract": "FAILED"},
        "aplus": {"chunk": "NOT_FOUND", "extract": "SKIPPED"}
      }
    }
  ]
}
```

---

## 最终产出文件清单

| 文件名 | 职责 | 位置 |
|--------|------|------|
| `{workspace}/golden/{ASIN}/{block}/{block}_golden.md` | 阶段零：LLM 黄金样本 | 用户工作空间（与 chunks 分离） |
| `{workspace}/chunker/static_chunker.py` | 阶段一：分块器 | 插件目录 |
| `{workspace}/chunker/ppd_extract.py` | 阶段二：PPD 提取器 | 插件目录 |
| `{workspace}/chunker/customer_reviews_extract.py` | 阶段二：评论提取器 | 插件目录 |
| `{workspace}/chunker/product_details_extract.py` | 阶段二：商品详情提取器 | 插件目录 |
| `{workspace}/chunker/aplus_extract.py` | 阶段二：A+ 提取器 | 插件目录 |
| `{workspace}/chunker/batch_run.py` | 阶段三：批量编排脚本 | 插件目录 |
| `{workspace}/chunks/` | 输出 chunks + manifest | 用户工作空间 |
| `{workspace}/tests/test_*.py` | 回归测试文件 | 插件目录 |

## Output Contract

1. **Top1/Top25 的黄金样本**存在于 `golden/{ASIN}/{block}/`（与 chunks/ 独立分离）
2. **6 个核心 py 文件**均存在于 `chunker/` 目录
3. **所有测试通过**：`python -m pytest tests/ -v` 全绿
4. **提取结果与黄金样本对齐**（形神兼备）
5. **无残留临时文件**
6. **manifest 完整**

### ❗ 结束前自检清单（Exit Checklist）

**在声明任务完成之前，必须逐条自检以下项目。缺少任何一项即为任务未完成：**

- [ ] Top1/Top25 的黄金样本已生成并校验
- [ ] `chunker/static_chunker.py` 存在且测试通过
- [ ] `chunker/customer_reviews_extract.py` 存在且测试通过
- [ ] `chunker/product_details_extract.py` 存在且测试通过
- [ ] `chunker/ppd_extract.py` 存在且测试通过
- [ ] `chunker/aplus_extract.py` 存在且测试通过
- [ ] `chunker/batch_run.py` 存在且调用了所有 4 个提取器
- [ ] 提取结果与黄金样本对齐（不只是结构，内容也要匹配）
- [ ] 输出目录结构正确：`{rank}_{ASIN}/block/raw/*.html` + `{rank}_{ASIN}/block/extract/*_extracted.md`
- [ ] 每个商品目录名包含排名序号前缀（如 `001_B0XXXXX`），**禁止纯 ASIN 目录名**
- [ ] 每个商品目录内有 `manifest.json`
- [ ] `out_dir/global_manifest.json` 存在且内容完整
- [ ] `--skip-extracted` 正确跳过已清洗的 ASIN
- [ ] 执行过 `python -m pytest tests/ -v` 并贴出结果

**如果上述 checklist 中有未勾选的项，你必须继续工作直到全部完成。**

## Hard Rules

1. 只基于用户提供的 HTML 样本做提取，不得臆造任何字段值
2. 缺失字段统一写 `N/A`
3. 所有测试必须在 `tests/` 目录
4. 临时探测脚本用完必须删除
5. 只用 `bs4`, `lxml`, `re`, `pathlib`, 标准库
6. 文件编码统一 UTF-8
7. 先写测试，后写代码
8. **严禁 `[class*='xxx']` 通配符 selector**
9. **customer_reviews 提取器必须复制模版**
10. **所有 Markdown 表格必须包含表头行 + `| --- | --- |` 分隔行**
11. **必须实际执行 `pytest` 并看到结果**后才能结束任务
12. **测试 fixture 路径用 `glob` 动态发现**，不得硬编码
13. **输出目录必须是 `{rank}_{ASIN}/` 格式**，rank 从 `rankings.jsonl` 最后一行的 `ranks[ASIN]` 读取并零填充 3 位；分块放 `block/raw/`，提取放 `block/extract/`——违反此规则的任何实现都是 BUG
14. **所有提取必须基于 BeautifulSoup + lxml 直接解析 HTML**，不经过任何 Markdown 转换中间层
15. **黄金样本是测试的唯一锚点**——静态代码的产出必须与黄金样本对齐，不只是"有那几个文件"，内容也要匹配
