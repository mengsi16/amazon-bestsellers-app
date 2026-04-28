# Scraper Refactor: MCP Integration

**时间戳**：2026-04-23 12:40 UTC+08:00
**任务类型**：重构（refactor）
**范围**：scraper 模块、MCP Server、各 agent / skill 对爬虫产出的依赖

---

## 背景

旧 scraper 用单文件 `raw_amazon_spider.py` 同时处理类目列表页 + 商品详情页，爬取输出按 `raw_html_output/<run_id>/` 结构组织（时间戳目录，与类目弱耦合）。图片下载分散在 `fetch_aplus_images.py` / `fetch_product_images.py` 两个脚本里，由各 analyst agent 自己在提取阶段触发，走 Plan-JSON + CLI 调用的二段式。

该方案的问题：
- run_id 时间戳目录与用户会话 workspace 不对齐，多次分析同一类目数据分散
- ASIN 无去重，多次运行会重复爬取相同商品
- 图片下载由 analyst agent 触发，offline 分析时必须回到终端执行，打断链路
- 类目列表 + 商品详情耦合在一个 CLI 里，Phase 1 / Phase 2 切换靠 `--phase` 参数
- rank 编码进 HTML 文件名（`product_0001_B0XXX.html`），ASIN 去重后就无法保留 rank

---

## 用户确立的 workspace 布局

```
{workspace}/                                       ← 用户会话目录 + /workspace/{browse_node_id}
├── categories/{browse_node_id}/
│   ├── category_001.html
│   ├── meta.json                                  ← 首次发现时间 / 运行次数
│   └── rankings.jsonl                             ← append-only，每次运行追加一行
├── products/{ASIN}/                               ← 全局 ASIN 仓库（去重 & 幂等跳过）
│   ├── product.html
│   ├── meta.json
│   ├── listing-images/
│   │   ├── urls.json
│   │   └── images/listing_img_001.jpg ...
│   └── aplus-images/
│       ├── urls.json
│       ├── aplus_extracted.md
│       ├── aplus.html
│       └── images/aplus_img_001.png ...
└── products/requests.jsonl                        ← append-only 爬取日志
```

`rankings.jsonl` 每行格式：
```json
{"run_at_utc":"...","browse_node_id":"3744541","product_count":50,"asins":["B0X...","B0Y..."],"ranks":{"B0X...":1,"B0Y...":2}}
```

`category_slug = browse_node_id` —— 从 URL 尾部抽取的纯数字（例 `https://www.amazon.com/gp/bestsellers/fashion/1040658/` → `1040658`）。全局唯一、Amazon 分配、代码自动抽取。用户口径中称为 **"codied"**。

---

## 模块拆分（单一职责）

| 文件 | 职责 | 输入 | 输出 |
|---|---|---|---|
| `scraper/category_spider.py` | 类目列表页爬虫 | `--category-url` | `categories/{browse_node_id}/category_NNN.html` + `rankings.jsonl` 追加一行 |
| `scraper/product_spider.py` | 商品详情页爬虫（ASIN 去重） | `--urls` 或 `--urls-file` | `products/{ASIN}/product.html` + `meta.json` |
| `scraper/extract_listing_images.py` | 从 `product.html` 提取 listing 图 | `--root-dir` 或 `--asin-dir` | `products/{ASIN}/listing-images/` |
| `scraper/extract_aplus.py` | 从 `product.html` 提取 A+ 图 + 结构化内容 | `--root-dir` 或 `--asin-dir` | `products/{ASIN}/aplus-images/` 含 `aplus_extracted.md` |
| `scraper/downloader.py` | 通用图片下载工具（被两个 extractor 复用） | 下载 plan 或 URL 列表 | `images/` + `download_manifest.json` |
| `scraper/mcp_server.py` | MCP Server 入口（stdio） | Claude-Code MCP 协议 | 4 个工具（见下） |

---

## MCP 工具契约

| 工具 | 作用 | 关键参数 |
|---|---|---|
| `crawl_bestseller_list` | 爬类目列表页 | `category_url`、`output_dir`（= workspace 根目录）、`max_category_pages` |
| `crawl_product_details` | 爬商品详情页 + 默认串联 listing/A+ 提取 | `product_urls[]`、`output_dir`、`auto_extract_images=True`、`max_concurrency=3` |
| `extract_listing_images` | 补跑单个 ASIN 的 listing 图提取 | `asin`、`output_dir`、`download=True` |
| `extract_aplus_images` | 补跑单个 ASIN 的 A+ 图提取 | `asin`、`output_dir`、`download=True` |

**所有工具的 `output_dir` 必须传 workspace 根目录的绝对路径。** extract 工具基于本地缓存的 `products/{ASIN}/product.html` 重解析，不重访 Amazon。

---

## 设计决策

### 为什么拆成 category_spider + product_spider

1. **阶段职责独立**：类目列表是"发现 URL"，详情页是"全量内容抓取"，二者的反爬、超时、并发策略差异大
2. **rankings.jsonl 归属明确**：只由 category_spider 写入，与类目列表页绑定
3. **产品爬虫可独立复跑**：给定一批 URL 就能跑，不需要先过类目

### 为什么 products/ 按 ASIN 去重（而不是 rank）

1. **全局仓库**：同一 ASIN 可能出现在多个类目下，按 ASIN 只存一份
2. **幂等性**：product.html 存在且 >500KB 就跳过，支持反复运行
3. **rank 外置**：rank 是类目维度属性，放 rankings.jsonl，chunker 阶段再拼 `{rank}_{ASIN}/`

### 为什么图片提取收归到 MCP 阶段

1. **链路完整性**：爬完立即提取图片，单次 MCP 调用返回"已就绪的 workspace"
2. **analyst 职责清晰**：A+ / fine-grained analyst 只读 `products/{ASIN}/{listing,aplus}-images/`，不再负责下载
3. **本地缓存可回放**：补跑只用 `product.html`，不再访问 Amazon
4. **单一下载实现**：`downloader.py` 是唯一的图片下载路径，selector / 反爬 / 限速只维护一处

### 为什么 rankings.jsonl 是 append-only

1. **历史可追溯**：每次爬取都是一个时间快照，可做排名趋势分析
2. **不会误删**：append 语义下单次失败不影响历史数据
3. **chunker 读最后一行**：取最新 `ranks[ASIN]` 作为当前 rank，零填充 3 位拼目录名

### 为什么 browse_node_id 作为 category_slug

1. **代码可抽取**：`re.search(r'/(\d+)/?$', url)` 即可
2. **全局唯一**：由 Amazon 分配，不会重名
3. **禁止模型起名**：历史教训是模型会自造 `home-garden` / `womens-hoodies` 之类的名字，不同 Agent 起的名字不一致导致数据分散

---

## 输出目录契约（强制）

```
{workspace}/
├── categories/{browse_node_id}/
│   ├── category_NNN.html
│   ├── meta.json
│   └── rankings.jsonl
└── products/{ASIN}/
    ├── product.html
    ├── meta.json
    ├── listing-images/{urls.json, images/}
    └── aplus-images/{urls.json, aplus_extracted.md, aplus.html, images/}
```

违反此结构的实现视为 BUG。

---

## 禁止事项（Hard Rules）

1. 严禁在 HTML 文件名或目录名中编码 rank（products/ 按 ASIN 去重，rank 外置到 rankings.jsonl）
2. 严禁让模型自造 category_slug，必须从 URL 尾部抽取 browse_node_id
3. 严禁 analyst agent 调用任何外部下载脚本，图片由 MCP 的 extract 工具负责
4. 严禁 MCP 工具的 output_dir 传 `raw_html_output` 或 run_id 时间戳目录，必须是 workspace 根目录
5. 严禁重复调用 `crawl_bestseller_list` / `crawl_product_details`（每种只调 1 次，阻塞等返回）
6. 严禁 extract 工具重访 Amazon，必须只读本地缓存的 product.html
7. 严禁使用 `[class*='xxx']` 通配符 selector（延续 chunker 规则）

---

## 关键文件

| 文件 | 职责 |
|------|------|
| `scraper/mcp_server.py` | MCP 入口（stdio），4 个工具 |
| `scraper/category_spider.py` | 类目列表页爬虫 + rankings.jsonl 管理 |
| `scraper/product_spider.py` | 详情页爬虫 + ASIN 去重 + 幂等跳过 |
| `scraper/extract_listing_images.py` | listing 图提取 + 下载 |
| `scraper/extract_aplus.py` | A+ 图 + 结构化内容提取 + 下载 |
| `scraper/downloader.py` | 通用图片下载工具 |
| `scraper/requirements.txt` | playwright + scrapling[fetchers] + bs4 + lxml + mcp |
| `scraper/README.md` | scraper 模块开发者文档 |
| `agents/amazon-bestsellers-orchestrator.md` | 顶层编排器，workspace 路径约定、6 步流程、Exit Checklist |
| `agents/amazon-product-chunker.md` | chunker-agent，输入 products/{ASIN}/product.html |
| `agents/amazon-bestsellers-aplus-analyst.md` | A+ analyst，读 products/{ASIN}/aplus-images/ |
| `agents/amazon-bestsellers-fine-grained-analyst.md` | fine-grained analyst，读 products/{ASIN}/listing-images/ |
| `skills/amazon-bestsellers-aplus-dim/SKILL.md` | A+ 维度技能，图片数据源指向 aplus-images/ |
| `skills/amazon-bestsellers-fine-grained-dim/SKILL.md` | 细分类维度技能，图片数据源指向 listing-images/ |
| `README.md` / `README_en.md` | 主项目文档，四维度流程图 + 目录结构 |

---

## 反爬与鲁棒性

- **双 Fetcher 降级**：`StealthyFetcher` 优先，失败 fallback 到 `DynamicFetcher`
- **Cloudflare challenge**：`solve_cloudflare=True`
- **Amazon captcha**：自动检测并点击 "Continue shopping" 绕过
- **A+ 懒加载**：商品详情页自动滚动触发 `aplus-media` 模块加载
- **类目列表滚动**：触发 Top50 全量加载（不只首屏 30 条）
- **ASIN 幂等跳过**：`products/{ASIN}/product.html` 存在且 >500KB 则跳过

---

## 待 agent 后续产出的文件

- `products/{ASIN}/{block}/golden/{block}_golden.md`（chunker 阶段零产出）
- `chunker/static_chunker.py` + 4 个 extractor + batch_run.py（chunker 阶段一/二/三产出）
- `tests/test_*.py`（chunker 回归测试）
- `reports/{browse_node_id}_{marketplace,reviews,aplus,fine_grained}_dim.{md,json}`（四 analyst 产出）
- `summary.md`（orchestrator 产出）
