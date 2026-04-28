# Amazon Bestsellers Scraper

本目录提供 Amazon Bestsellers 流水线的**爬虫 + 图片提取**层，既以 **MCP Server** 形式被 agent 调用，也保留独立 CLI 用于调试。

## 组件总览

| 文件 | 职责 | CLI 可用 |
|---|---|---|
| `mcp_server.py` | MCP Server：对外暴露 4 个工具（见下） | — |
| `category_spider.py` | 爬取 Bestsellers 类目列表页，写入 `categories/{browse_node_id}/`，追加 `rankings.jsonl` | ✅ |
| `product_spider.py` | 爬取商品详情页，按 ASIN 去重，写入 `products/{ASIN}/product.html` + `meta.json` | ✅ |
| `extract_listing_images.py` | 从 `products/{ASIN}/product.html` 提取 listing 图到 `listing-images/` | ✅ |
| `extract_aplus.py` | 提取 A+ 内容与图片到 `aplus-images/`（含 `aplus_extracted.md`） | ✅ |
| `downloader.py` | 通用图片下载工具（被上面两个 extractor 使用） | ✅ |

## 核心设计

- **workspace 根目录**：所有输出都以 workspace 为根，布局如下
  ```
  {workspace}/
  ├── categories/{browse_node_id}/    类目列表页 HTML + rankings.jsonl（append-only）
  ├── products/{ASIN}/                全局 ASIN 仓库（按 ASIN 去重）
  │   ├── product.html
  │   ├── meta.json
  │   ├── listing-images/
  │   └── aplus-images/
  ```
- **browse_node_id (codied)**：Amazon Bestsellers URL 尾部的数字 ID，如 `https://www.amazon.com/gp/bestsellers/fashion/1040658/` 的 `1040658`，作为 `category_slug` 使用。
- **幂等跳过**：`product_spider.py` 默认跳过 `products/{ASIN}/product.html` 已存在且 >500KB 的 ASIN。
- **排名快照**：`rankings.jsonl` 每次运行追加一行，记录该次爬取的 `{ASIN: rank}` 字典，用于追踪排名变化。

## MCP 工具

`mcp_server.py` 暴露以下 4 个工具供 Claude-Code agent 调用：

| 工具 | 作用 | 写入位置 |
|---|---|---|
| `crawl_bestseller_list` | 爬取类目列表页 | `categories/{browse_node_id}/` |
| `crawl_product_details` | 爬取商品详情页（ASIN 去重）+ 自动提取 listing + A+ 图 | `products/{ASIN}/` |
| `extract_listing_images` | 单独补跑某 ASIN 的 listing 图提取 | `products/{ASIN}/listing-images/` |
| `extract_aplus_images` | 单独补跑某 ASIN 的 A+ 图提取 | `products/{ASIN}/aplus-images/` |

所有工具的 `output_dir` 参数必须传 **workspace 根目录的绝对路径**。

## 安装

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## CLI 调试用法

### 1. 爬取类目列表页

```bash
python category_spider.py \
  --category-url "https://www.amazon.com/gp/bestsellers/home-garden/3744541/" \
  --output-dir ./workspace/3744541 \
  --max-category-pages 2
```

### 2. 爬取商品详情页

```bash
python product_spider.py \
  --urls "https://www.amazon.com/dp/B0XXXXXXX" "https://www.amazon.com/dp/B0YYYYYYY" \
  --output-dir ./workspace/3744541/products \
  --max-concurrency 3
```

`--force` 可强制重爬已存在的 ASIN。

### 3. 提取 listing / A+ 图

```bash
# 对整个 products/ 目录批量提取
python extract_listing_images.py --root-dir ./workspace/3744541/products
python extract_aplus.py --root-dir ./workspace/3744541/products

# 或对单个 ASIN 目录
python extract_listing_images.py --asin-dir ./workspace/3744541/products/B0XXXXXXX
```

`--no-download` 只提取 URL 不下载；`--force` 忽略已存在的 `urls.json`。

### 4. 启动 MCP Server

```bash
python mcp_server.py
```

stdio 模式，供 Claude-Code 通过 MCP 协议连接。

## 反爬特性

- `StealthyFetcher` + `DynamicFetcher` 双 fetcher fallback
- Cloudflare challenge 自动处理（`solve_cloudflare=True`）
- Amazon "Continue shopping" captcha 自动点击绕过
- 商品详情页自动滚动触发 A+ 懒加载模块
- 类目列表页自动滚动以发现 Top50 全量（不只首屏 30 条）
- 请求间隔 + 退避重试

## 依赖

见 `requirements.txt`：
- `playwright` + `scrapling[fetchers]` — 浏览器驱动
- `beautifulsoup4` + `lxml` — HTML 解析
- `mcp` — MCP 协议

## 日志与产出

- `{workspace}/products/requests.jsonl` — 每次商品爬取的日志（append-only）
- `{workspace}/categories/{browse_node_id}/rankings.jsonl` — 排名快照
- `{workspace}/categories/{browse_node_id}/meta.json` — 类目元信息（首次发现时间、运行次数）
- `{workspace}/products/{ASIN}/meta.json` — 单个 ASIN 的爬取元信息
