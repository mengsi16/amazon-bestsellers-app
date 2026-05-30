# Amazon Bestsellers Summary — Web App

基于 `amazon-bestsellers-summary-agent` 的可视化 Web 产品，提供对话式分析界面。

## 项目结构

```
amazon-bestsellers-app/
├── agent/          ← amazon-bestsellers-summary-agent 副本（后端调用）
├── backend/        ← FastAPI 后端
│   ├── main.py
│   └── requirements.txt
├── frontend/       ← React + Vite + TailwindCSS 前端
│   └── src/
│       ├── App.tsx
│       ├── api.ts
│       └── components/
│           ├── Sidebar.tsx        — 任务历史侧边栏
│           ├── ProgressPanel.tsx  — 实时进度仪表盘
│           ├── ReportViewer.tsx   — Markdown 报告渲染
│           ├── ChatPanel.tsx      — Claude 追问对话
│           └── NewTaskDialog.tsx  — 新建分析弹窗
├── workspace/      ← 分析结果自动写入此目录
├── docker-compose.yml
└── start.bat       ← 一键启动脚本（Windows）
```

## 功能特性

| 功能 | 说明 |
|------|------|
| **新建分析** | 输入 Amazon Bestsellers URL，自动启动全流水线 |
| **实时进度** | 5 个阶段（CRAWL / CHUNK / ANALYZE / SUMMARY / QA）可视化 + 实时日志 |
| **报告查看** | 5 个 Tab（综合总结 + 4 维度）Markdown 渲染，支持下载 .md |
| **追问 Claude** | 分析完成后可对报告内容自由提问，流式回复 |
| **历史任务** | 左侧边栏记录所有分析任务，可随时回看报告 |
| **模型配置** | 支持多套模型配置，API Key 加密存储 |
| **Credits 追踪** | 自动记录 API 使用量（cache_hit / cache_miss / output） |

## 快速启动

### 前置要求

- Docker & Docker Compose
- Node.js >= 18（本地开发需要）
- Python >= 3.10（本地开发需要）
- Claude Code CLI（本地开发需要）

### 方式一：Docker 部署（推荐）

#### 1. 配置环境变量

创建 `backend/.env` 文件：

```env
# JWT 密钥 — 生产环境必须设置
JWT_SECRET_KEY=your-secret-key-here

# Credits 加密密钥 — 生产环境必须设置
CREDITS_ENCRYPTION_KEY=your-32-byte-encription-key-here

# CORS 允许的源
CORS_ORIGINS=http://localhost

# Anthropic API Key（Claude Code 使用）
ANTHROPIC_API_KEY=sk-ant-xxxxx
```

#### 2. 启动服务

```bash
# 构建并启动所有服务
docker-compose up -d --build

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

#### 3. 访问应用

- 前端：http://localhost
- 后端 API：http://localhost:8000
- API 文档：http://localhost:8000/docs

### 方式二：本地开发

#### 安装后端依赖

```bat
cd backend
pip install -r requirements.txt
```

#### 安装前端依赖

```bat
cd frontend
npm install
```

#### 启动

```bat
REM 终端1 — 后端
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

REM 终端2 — 前端
cd frontend
npm run dev
```

打开浏览器访问 http://localhost:5173

## 环境变量说明

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `JWT_SECRET_KEY` | 生产环境是 | 自动生成 | JWT 签名密钥，生产环境必须设置 |
| `JWT_SECRET_KEY_PREVIOUS` | 否 | 无 | 密钥轮换期间的旧密钥 |
| `CORS_ORIGINS` | 否 | `http://localhost:5173` | 允许的 CORS 源（逗号分隔） |
| `ENV` | 否 | `development` | 运行环境：`production` / `development` |
| `PORT` | 否 | `8000` | 服务端口 |
| `DB_PATH` | 否 | `backend/conversations.db` | 数据库路径 |
| `WORKSPACE_BASE` | 否 | `backend/workspace` | Workspace 根目录 |
| `ANTHROPIC_API_KEY` | 是 | 无 | Anthropic API Key（Claude Code 使用） |
| `CREDITS_ENCRYPTION_KEY` | 生产环境是 | 自动生成 | API Key 加密密钥，生产环境必须设置 |

## 模型配置系统

系统支持多套模型配置，每套配置包含：

- `name`: 配置名称（如"默认配置"）
- `model`: 模型名称（如 `claude-3-5-sonnet-20241022`）
- `api_key`: API Key（加密存储）
- `base_url`: API 基础 URL（可选，默认 `https://api.anthropic.com`）
- `is_default`: 是否为默认配置

### API 端点

```bash
# 列出当前用户所有配置（不返回 api_key）
GET /api/model-configs

# 创建新配置
POST /api/model-configs
{
  "name": "我的配置",
  "model": "claude-3-5-sonnet-20241022",
  "api_key": "sk-ant-xxxxx",
  "base_url": "https://api.anthropic.com",
  "is_default": true
}

# 删除配置
DELETE /api/model-configs/{config_id}

# 设为默认配置
PUT /api/model-configs/{config_id}/default
```

### API Key 加密

API Key 使用 `cryptography.fernet.Fernet` 对称加密：

- 密钥从环境变量 `CREDITS_ENCRYPTION_KEY` 读取
- 生产环境必须设置此密钥，否则启动失败
- 加密后的 API Key 存储在数据库 `api_key_encrypted` 字段

## Credits 系统

系统自动追踪 API 使用量，记录每次调用的 tokens 消耗。

### 数据表 `credits_log`

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | TEXT | 任务 ID |
| `user_id` | TEXT | 用户 ID |
| `cache_hit_input` | INTEGER | 缓存命中的输入 tokens |
| `cache_miss_input` | INTEGER | 缓存未命中的输入 tokens |
| `output_tokens` | INTEGER | 输出的 tokens |
| `cost_usd` | REAL | 费用（美元） |
| `model` | TEXT | 使用的模型 |
| `created_at` | TEXT | 创建时间 |

### 从 stream-json 提取

在 `_feed_stream_line` 的 `result` 事件处理中：

- 从 `ev.get('usage', {})` 提取 `cache_hit_input`、`cache_miss_input`、`output_tokens`
- 如果 usage 中没有 cache 字段，则全部计入 `cache_miss_input`
- 写入 `credits_log` 表

### API 端点

```bash
# 获取当前用户总消耗（聚合查询）
GET /api/credits
# 返回: { "cache_hit_input": 0, "cache_miss_input": 0, "output": 0, "total_cost_usd": 0.0 }

# 获取消耗明细
GET /api/credits/logs?limit=50&offset=0
```

**说明**：Credits 由系统在分析任务完成后自动记录（从 stream-json 的 `result` 事件提取 usage 数据），无需手动调用 API。`POST /api/credits/record` 是内部接口，用于自动记录。

## Docker 部署说明

### docker-compose.yml 配置

```yaml
services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      - ENV=production
      - JWT_SECRET_KEY=${JWT_SECRET_KEY}
      - CORS_ORIGINS=http://localhost
      - CREDITS_ENCRYPTION_KEY=${CREDITS_ENCRYPTION_KEY}
    volumes:
      - backend-data:/app/workspace
      - ./backend/conversations.db:/app/conversations.db
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    ports:
      - "80:80"
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  backend-data:
```

### Claude Code 配置

Docker 容器中已预装 Claude Code CLI（固定版本 1.0.0）。

## 架构

```
浏览器 (React)
  │  HTTP + SSE
  ▼
FastAPI (port 8000)
  │  subprocess
  ▼
claude CLI → agent/  (amazon-bestsellers-summary-agent)
  │  写入文件
  ▼
workspace/{browse_node_id}/
  ├── reports/*_dim.md
  └── summary.md
```

## Credits

- **amazon-bestsellers-summary-agent**: https://github.com/anthropics/amazon-bestsellers-summary-agent
- **Claude Code**: https://docs.anthropic.com/en/docs/claude-code
- **FastAPI**: https://fastapi.tiangolo.com/
- **React**: https://react.dev/
- **Tailwind CSS**: https://tailwindcss.com/