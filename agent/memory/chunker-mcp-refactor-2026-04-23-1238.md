# Chunker Refactor: MCP Integration

**时间戳**：2026-04-23 12:38 UTC+08:00
**任务类型**：重构（refactor）
**范围**：chunker-agent 及其依赖

---

## 背景

旧 chunker 流水线基于 MarkItDown 做 HTML → Markdown 中间转换，再做语义分块。
该方案与 MCP scraper 的输出目录结构不一致，且 MarkItDown 转换引入了信息损失（价格、图片 URL、DOM 语义标记丢失）。

MCP scraper 的输出目录结构：

```
{workspace}/
├── categories/{browse_node_id}/
│   ├── category_001.html
│   ├── meta.json
│   └── rankings.jsonl          ← append-only，最后一行是最新的排名快照
├── products/{ASIN}/            ← 全局 ASIN 去重仓库
│   ├── product.html
│   ├── meta.json
│   ├── listing-images/{urls.json, images/}
│   └── aplus-images/{urls.json, aplus.html, aplus_extracted.md, images/}
```

`rankings.jsonl` 每行格式：
```json
{"run_at_utc":"...","browse_node_id":"3744541","product_count":50,"asins":["B0X...","B0Y..."],"ranks":{"B0X...":1,"B0Y...":2}}
```

---

## 用户确立的工作流（四阶段）

```
MCP scraper 产出 products/{ASIN}/product.html + rankings.jsonl
        │
        ▼
  阶段零：LLM 黄金样本
    - 用 LLM 直接清洗 Top1/Top25 的 raw HTML
    - 产出 products/{ASIN}/{block}/golden/{block}_golden.md
    - 人工校验后作为后续测试的唯一锚点
        │
        ▼
  阶段一：静态分块（static_chunker.py）
    - BS4+lxml 按稳定 DOM id 切 4 个子 HTML
    - ppd / customer_reviews / product_details / aplus
        │
        ▼
  阶段二：静态提取（4 个 extractor）
    - 按黄金样本 TDD，形神兼备
    - Top2/Top3 做结构一致性补充测试
        │
        ▼
  阶段三：批量编排（batch_run.py）
    - 遍历所有 ASIN，已清洗的 --skip-extracted 跳过
```

---

## 设计决策

### 为什么去掉 MarkItDown

1. **信息损失**：HTML → Markdown 转换会丢失 `data-asin`、`data-hook`、价格节点的结构化标记
2. **重复劳动**：Markdown 再解析回结构化数据是二次提取
3. **调试困难**：出问题时无法定位到原始 DOM 节点
4. **BS4+lxml 直接解析**：保留完整 DOM 语义，selector 可复用

### 为什么保留 static_chunker.py 作为模板

用户之前有保留部分静态代码文件作为 templates 的习惯。`static_chunker.py` 提供基础 selector 表和分块骨架，chunker-agent 基于实际 HTML 样本调整 selector 即可，不用从零写。

### 为什么需要 LLM 黄金样本（阶段零）

- 静态代码的对齐目标必须是"神似"，不只是"形似"
- LLM 清洗 Top1/Top25 的结果作为黄金标准，后续静态代码的提取结果必须与黄金样本内容匹配
- 测试用例的断言锚点来自黄金样本

### 为什么 rank 从 rankings.jsonl 读取

- MCP scraper 的 products/{ASIN}/ 是全局 ASIN 去重仓库，不包含 rank 信息
- 同一个 ASIN 可能出现在多个类目下，rank 是类目维度的属性
- rankings.jsonl 最后一行是该类目最新一次爬取的快照
- 用 ranks[ASIN] 读取 rank 并零填充 3 位，拼 {rank}_{ASIN}/ 作为输出目录名

---

## CLI 契约

```bash
python -m chunker.batch_run \
    --products-dir {workspace}/products \
    --rankings-jsonl {workspace}/categories/{browse_node_id}/rankings.jsonl \
    --out-dir {workspace}/chunks \
    [--limit N] \
    [--skip-extracted]
```

---

## 输出目录契约（强制）

```
{out_dir}/
├── 001_B0XXXXX/
│   ├── manifest.json
│   ├── ppd/
│   │   ├── raw/ppd.html
│   │   └── extract/ppd_extracted.md
│   ├── customer_reviews/{raw/,extract/}
│   ├── product_details/{raw/,extract/}
│   └── aplus/{raw/,extract/}
├── 002_B0YYYYY/...
└── global_manifest.json
```

违反此结构的实现视为 BUG。

---

## 禁止事项（Hard Rules）

1. 严禁再引入 MarkItDown 或任何 HTML → Markdown 中间层
2. 严禁使用 `[class*='xxx']` 通配符 selector
3. 严禁纯 `{ASIN}/` 作为 chunks 输出目录名（必须带 rank 前缀）
4. 严禁共享 `extract/` 目录（必须按 block 独立）
5. 黄金样本必须基于实际 HTML，不得臆造字段值

---

## 关键文件

| 文件 | 职责 |
|------|------|
| `agents/amazon-product-chunker.md` | chunker-agent prompt，四阶段工作流 |
| `skills/amazon-chunker/SKILL.md` | 分块器技能定义 |
| `skills/amazon-extractor/SKILL.md` + 4 子技能 | 提取器技能定义 |
| `skills/amazon-test-chunker/SKILL.md` + 2 子技能 | TDD + Golden Fixture 技能 |
| `chunker/static_chunker.py` | 静态分块器（agent 基于此调整 selector） |
| `chunker/batch_run.py` | 批量编排 runner |
| `chunker/requirements.txt` | bs4 + lxml |

---

## 待 agent 后续产出的文件

- `products/{ASIN}/{block}/golden/{block}_golden.md`（Top1/Top25）
- `chunker/customer_reviews_extract.py` + 测试
- `chunker/product_details_extract.py` + 测试
- `chunker/ppd_extract.py` + 测试
- `chunker/aplus_extract.py` + 测试
- `tests/test_*.py`（所有提取器回归测试）
