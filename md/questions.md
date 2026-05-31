# Amazon Bestsellers 项目问题排查清单

> 排查时间：2026-05-29
> 排查方法：按 CLAUDE.md 规则 11「模拟测试必须边讲边读」——用具体输入例子贯穿追踪字段流转
> 模拟输入：URL = `https://www.amazon.com/gp/bestsellers/beauty/11058221/`
> 严重程度：P0(致命) / P1(严重) / P2(中等) / P3(轻微)

---

## 汇总统计

| 严重程度 | 数量 | 问题编号 |
|---------|------|---------|
| P1(严重) | 6 | #1 aplus分块, #2 invalid_page检测, #4 error不显示, #7 stream重置, #13 ProgressPanel死代码, #17 reanalyze ignore_errors |
| P2(中等) | 8 | #3 cloudflare默认, #5 SSE error未处理, #6 并发安全, #8 SQLite静默, #9 多处静默异常, #10 chat并发, #14 session_id字段不一致, #15 stale session returncode, #16 batch_run import |
| P3(轻微) | 2 | #11 prose-invert, #12 死代码 |

---

## 发现的问题

---

## [Agent-A] P1: static_chunker.py aplus 分块只取第一个 #aplus 容器

- **严重程度**：P1(严重)
- **影响范围**：所有商品的 A+ 内容分块（chunks/{rank}_{ASIN}/aplus/raw/aplus.html）
- **复现条件**：商品页同时有 Brand Story 和 Premium A+ 两个 `id="aplus"` 的 div（Amazon 常见情况）
- **根因分析**：`static_chunker.py:48-54` 的 `_find_block(soup, "aplus")` 使用 `select_one("#aplus")`，只返回第一个匹配元素。第二个容器中的模块（如 `3p-module-b`）被完全丢弃。
  ```python
  BLOCK_SELECTORS: dict[str, list[str]] = {
      "aplus": ["#aplus", "#aplusBrandStory_feature_div"],  # select_one 只取第一个
  }
  ```
  而 `extract_aplus.py:93-105` 的 `_find_aplus_containers(soup)` 使用 `select(selector)` 返回列表，已修复了这个问题。但 chunker 的 HTML 切片已经不完整。
- **修复方向**：`_find_block` 对 aplus 特殊处理，改用 `select` 收集所有容器，合并 HTML。或直接复用 `_find_aplus_containers` 的逻辑。

---

## [Agent-A] P1: static_chunker.py invalid_product_page 检测依赖 fragile import，失败时静默跳过

- **严重程度**：P1(严重)
- **影响范围**：当爬虫抓到 Amazon 首页重定向而非商品详情页时，chunker 不会拦截，产出全 NOT_FOUND 的 chunks → audit 标记 incomplete → orchestrator 补跑 → 同一无效 HTML → **无限循环**（CLAUDE.md 规则 17 描述的已知问题）
- **复现条件**：ASIN 被重定向到 Amazon 首页，且 `static_chunker.py` 运行时 `scraper.product_spider` 不在 `sys.path` 上
- **根因分析**：`static_chunker.py:84-89`：
  ```python
  try:
      from scraper.product_spider import is_valid_product_page
      if not is_valid_product_page(html_content):
          return {"status": "SKIPPED", "reason": "invalid_product_page"}
  except ImportError:
      pass  # scraper not on sys.path during standalone batch_run
  ```
  当 `batch_run.py` 在 workspace 目录下运行时（`python -m chunker.batch_run`），`scraper` 包不在 `sys.path` 上，`ImportError` 被静默吞掉。结果：无效页面不被拦截，chunker 正常产出 `NOT_FOUND` blocks，触发 audit → 补跑死循环。
- **修复方向**：将 `is_valid_product_page` 的核心逻辑（`PRODUCT_PAGE_MARKERS` + `NON_PRODUCT_PAGE_MARKERS` 检查）复制到 `static_chunker.py` 中，不依赖外部 import。或把 marker 列表提取为共享常量文件。

---

## [Agent-A] P2: product_spider CLI --solve-cloudflare 默认为 True

- **严重程度**：P2(中等)
- **影响范围**：直接使用 CLI `python product_spider.py` 时每次请求多耗 5-15 秒
- **复现条件**：通过命令行直接运行 product_spider.py（而非通过 MCP server）
- **根因分析**：`product_spider.py:764`：`parser.add_argument("--solve-cloudflare", action="store_true", default=True)`。默认值为 True，与 CLAUDE.md 规则 2 矛盾。MCP server 正确默认 `solve_cloudflare=False`（line 211），但 CLI 默认 True。
- **修复方向**：改为 `default=False`。

---

## [Agent-A] P2: batch_run.py `_chunk_product` 缺少 import 错误处理

- **严重程度**：P2(中等)
- **影响范围**：`python -m chunker.batch_run` 执行时，如果 `static_chunker` 模块因任何原因（Python 路径错误、依赖缺失）无法导入，`ImportError` 会向外层抛出，导致整个 batch pipeline 中断
- **复现条件**：`batch_run.py:99-111` 的 `_chunk_product` 函数对 `static_chunker` 模块的 import 没有 try-except 保护
- **根因分析**：
  ```python
  def _chunk_product(asin: str, products_dir: Path, product_out_dir: Path) -> dict:
      from chunker.static_chunker import chunk_product_html, write_product_manifest  # ← 无保护
      result = chunk_product_html(html_path, product_out_dir)
  ```
  按 fail-fast 原则，这里应该捕获并返回错误状态，而不是让异常向上蔓延。
- **修复方向**：用 `try-except ImportError` 包裹 import，`except` 时返回 `{"chunk_status": "FAILED", "reason": "chunker_module_not_available"}`

---

## [Frontend-B] P1: 任务 error 信息不显示在前端 UI 中

- **严重程度**：P1(严重)
- **影响范围**：所有失败任务的用户体验
- **复现条件**：任务失败后查看详情
- **根因分析**：
  1. SSE `status` 事件正确传递了 `error` 字段（`api.ts:175`，`App.tsx:141`）
  2. `App.tsx:141` 正确更新了 `task.error`：`setTasks((prev) => prev.map((t) => (t.id === task_id ? { ...t, status, error } : t)))`
  3. **但 `activeTask.error` 从未在任何组件中渲染！** `App.tsx:346-358` 只显示了 status badge（running/completed/failed），不显示 error 内容。
  4. `LiveStream.tsx:337-338` 的空状态只说"任务失败，查看右侧阶段条和日志定位问题"，不显示 `task.error`。
  5. `StageRail.tsx` 也不显示 error。
  用户看到的只有 "failed" 红色标签，**完全不知道为什么失败**。
- **修复方向**：在 App.tsx 顶部栏或 LiveStream 空状态中显示 `activeTask.error` 的内容。

---

## [Frontend-B] P2: SSE 'error' 事件类型未在 applySSEEvent 中处理

- **严重程度**：P2(中等)
- **影响范围**：任务被删除或找不到时的错误反馈
- **复现条件**：后端发送 `event: error`（如 task not found）
- **根因分析**：
  1. 后端 `_progress_generator` 在 task 不存在时发送 `{"event": "error", "data": ...}`（`main.py:1085-1086`）
  2. 前端 `openProgressSSE` 注册了 'error' 事件监听（`api.ts:206`）
  3. `applySSEEvent` 只处理 'phases'、'stage_catalog'、'stream_item'、'status' 四种事件（`App.tsx:125-145`）
  4. **'error' 事件被解析但不被处理**，用户看不到任何错误提示
- **修复方向**：在 `applySSEEvent` 中增加 'error' 分支，显示错误提示或 toast。

---

## [Frontend-B] P3: ReportViewer 缺少 prose-invert 深色主题样式

- **严重程度**：P3(轻微)
- **影响范围**：报告查看器中的 Markdown 渲染效果
- **复现条件**：打开报告查看器查看任意报告
- **根因分析**：`ReportViewer.tsx:94` 使用 `<div className="prose max-w-none">` 但缺少 `prose-invert`。对比 `LiveStream.tsx:48` 正确使用了 `prose prose-sm prose-invert`。深色背景下 prose 默认文字颜色为黑色，导致报告内容可能难以阅读。
- **修复方向**：改为 `<div className="prose prose-invert max-w-none">`。

---

## [Frontend-B] P3: ChatPanel.tsx 是死代码

- **严重程度**：P3(轻微)
- **影响范围**：无直接影响，增加维护负担
- **复现条件**：N/A
- **根因分析**：`ChatPanel.tsx` 定义了完整的聊天面板组件（205行），但从未被任何组件 import 或使用。App.tsx 的聊天功能直接嵌入在 `LiveStream.tsx` 中。`App.tsx` 中不存在 `import ChatPanel`。
- **修复方向**：删除 `ChatPanel.tsx`，或确认是否需要替代 LiveStream 中的内联聊天实现。

---

## [Frontend-B] P1: ProgressPanel.tsx 是从未被使用的死代码

- **严重程度**：P1(严重)
- **影响范围**：无直接运行时影响，但造成代码维护负担
- **复现条件**：N/A
- **根因分析**：`ProgressPanel.tsx` 完整定义了分析进度面板组件（209行），包含 phase 步骤、日志展开、错误提示等功能。但整个 `frontend/src/` 目录中没有任何地方 `import ProgressPanel`——grep 结果只有定义行本身。`App.tsx` 使用的是 `StageRail`（侧边栏进度条）而非 `ProgressPanel`。`ProgressPanel` 是一个孤立文件。
- **修复方向**：确认 `ProgressPanel` 是否为废弃的旧实现（如是则删除），或是否需要作为 `StageRail` 的替代方案。

---

## [Frontend-B] P2: Task interface 多了 session_id 字段（前端有，后端无）

- **严重程度**：P2(中等)
- **影响范围**：前端 `Task` 类型定义与后端 `Task` Pydantic 模型不完全对齐
- **复现条件**：检查前后端 Task 类型差异时
- **根因分析**：
  - `frontend/src/api.ts:3-13` 的 `Task` interface 包含 `session_id?: string`（不存在于后端 `backend/main.py:178-188` 的 `Task` 模型中）
  - 后端 `Task` 模型只有 `id, url, browse_node_id, model, status, created_at, updated_at, workspace_path, error`，**没有 `session_id`**
  - 但 `session_id` 在后端是存在的（`main.py:183`），只是没通过 API 暴露给前端——这是合理的设计（session_id 是内部运行态字段）
  - **实际上 CLAUDE.md 规则 16 要求 `session_id` 必须持久化到前端**，所以前端类型是对的，但后端 API 响应中从未包含它——这是不一致的根源
- **修复方向**：如果 `session_id` 不应暴露给前端（安全/实现细节考虑），则从前端 `Task` interface 中移除；如果应该暴露，则在后端 `Task` Pydantic 模型中添加到响应字段。

---

## [Backend-C] P2: tasks.json 无并发保护，多请求同时修改会丢失数据

- **严重程度**：P2(中等)
- **影响范围**：所有修改 tasks.json 的操作
- **复现条件**：用户快速连续操作（如快速点击"继续分析"然后立即"全量重新分析"）
- **根因分析**：`_load_tasks()` 从文件读取整个 JSON（line 406-422），`_save_tasks()` 写回整个 JSON（line 425-429）。两个并发请求可能同时读出同一版本，各自修改后写回，**后写者覆盖前写者的修改**。典型场景：
  1. 请求 A 读 tasks.json → `{task1: running}`
  2. 请求 B 读 tasks.json → `{task1: running}`（同一版本）
  3. 请求 A 修改 task1.status=completed → 写回
  4. 请求 B 修改 task1.status=failed → 写回（覆盖了 A 的 completed）
- **修复方向**：改用 SQLite 存储 tasks（已有 conversations.db），或加文件锁。

---

## [Backend-C] P1: _run_analysis stale session 重试时清空所有已收集的 stream items

- **严重程度**：P1(严重)
- **影响范围**：session 过期重试时的用户体验
- **复现条件**：claude CLI 的 session_id 过期/被清理，触发 `_run_analysis` 的 stale session 回退逻辑
- **根因分析**：`main.py:999`：
  ```python
  _reset_task_stream(task_id)  # 清空所有 stream items！
  ```
  在 stale session 检测后调用 `_reset_task_stream`，清除所有已收集的 stream items（包括内存中的 `_task_logs`、`_task_stream_items`、`_task_stream_order`、`_task_stream_version`）。用户之前看到的所有进度信息瞬间消失，只能看到"将新建对话重试"的系统消息。

  SQLite 中已持久化的 stream_items 没有被清除，但内存被清空了。这导致前端重新加载历史时能看到旧数据，但 SSE 推送时会丢失中间的版本号，可能导致显示混乱。
- **修复方向**：stale session 重试时不应该清空全部 stream，只需要追加一条系统消息说明重试即可。

---

## [Backend-C] P2: SQLite _stream_upsert 的错误被完全吞掉

- **严重程度**：P2(中等)
- **影响范围**：对话历史的持久化可靠性
- **复现条件**：SQLite 写入失败（磁盘满、权限问题、锁超时）
- **根因分析**：`main.py:519`：`except Exception: pass  # DB write failure must not break the live stream`。DB 写入失败完全静默，不打印任何日志。按 CLAUDE.md 规则（实现阶段 fail-fast + 所有保留的 try-except 必须 logger 打错误信息），这里违反了规则。
- **修复方向**：`except Exception: LOGGER.error(...)` 打印错误信息。

---

## [Backend-C] P2: _load_tasks / _save_chat_message / _load_chat_history / _load_stream_history / _delete_task_history 中的异常全部被静默吞掉

- **严重程度**：P2(中等)
- **影响范围**：所有 SQLite 操作和 tasks.json 操作
- **复现条件**：磁盘故障、文件损坏、权限问题
- **根因分析**：以下函数全部使用 `except Exception: pass` 或 `except Exception: return {}`：
  - `_save_chat_message` (line 113-114)
  - `_load_chat_history` (line 125-126)
  - `_load_stream_history` (line 155-156)
  - `_delete_task_history` (line 165-166)
  - `_load_tasks` (line 421-422)
  - `_load_analysis_meta` (line 286-287)

  按项目规则，这些 try-except 至少应该 `LOGGER.error(...)` 打印错误信息，否则排障时完全没有抓手。
- **修复方向**：所有 `except Exception: pass` 改为 `except Exception: LOGGER.error("...", exc_info=True)` 或 `LOGGER.warning(...)`。

---

## [Backend-C] P2: chat_with_task 没有防止同一任务同时发送多条聊天消息

- **严重程度**：P2(中等)
- **影响范围**：聊天功能
- **复现条件**：用户快速连续发送多条消息
- **根因分析**：`chat_with_task` 只检查了 `_assert_browse_node_not_running`（防止与分析进程冲突），但**没有检查是否已有 chat 请求在进行中**。两个并发的 chat 请求会同时启动两个 `claude --resume <session_id>` 进程，导致 session 被两个进程同时读写，可能产出混乱的回答。
- **修复方向**：增加 `_active_chat_tasks: dict[str, str]` 锁，防止同一 task 同时处理多个 chat 请求。

---

## [Backend-C] P2: _run_analysis stale session retry 需要 non-zero returncode 才能触发

- **严重程度**：P2(中等)
- **影响范围**：claude session 过期/被清理后，分析任务可能不会自动重试
- **复现条件**：claude session 已失效（session 文件被清理或过期），但 claude CLI 进程的退出码为 0（而非非零）
- **根因分析**：`main.py:992`：
  ```python
  if proc.returncode != 0 and task.session_id and not _retried:
      if any("No conversation found with session ID" in line for line in logs):
  ```
  条件要求 `proc.returncode != 0`。但当 session 失效时，claude 可能正常退出（exit code 0）同时输出 `"No conversation found with session ID"` 信息——此时日志中有错误信息但 `proc.returncode == 0`，导致重试逻辑被跳过，任务直接以 FAILED 结束。

  对比 `chat_with_task`（`main.py:1471-1474`）：在 `readline` 循环内检测到 `"No conversation found"` 就立即 `break` 并触发重试，不依赖 returncode。这是更可靠的设计。
- **修复方向**：将 stale session 检测移到 `_wait_process` 之前，在读取 stdout 过程中实时检测并 break，提前触发重试逻辑，而不是依赖 proc.returncode。

---

## [Backend-C] P1: reanalyze 的 workspace 清理逻辑使用 ignore_errors=True 违反 fail-fast 原则

- **严重程度**：P1(严重)
- **影响范围**：`POST /api/tasks/{task_id}/reanalyze` 的 workspace 清理
- **复现条件**：`shutil.rmtree` 删除失败时静默忽略
- **根因分析**：`main.py:1283-1294`：
  ```python
  if ws.exists():
      for child in ws.iterdir():
          if child.name == ANALYSIS_META_FILE:
              continue  # 跳过 .analysis_meta.json
          if child.is_dir():
              shutil.rmtree(child, ignore_errors=True)  # ← 违反 fail-fast
          else:
              try:
                  child.unlink()
              except Exception:
                  pass
  ```
  `ignore_errors=True` 会静默忽略所有删除失败（权限问题、文件被锁等），导致旧数据残留却不报警。按 CLAUDE.md 规则，这里应该用明确的异常处理并 log 错误。
- **修复方向**：不要用 `ignore_errors=True`，改用 `try: shutil.rmtree(child) except Exception as e: LOGGER.error(...)`。