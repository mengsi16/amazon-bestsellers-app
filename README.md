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

## 快速启动

### 前置要求

- Node.js >= 18
- Python >= 3.10
- Claude Code CLI 已安装并可在终端执行 `claude`
- Docker & Docker Compose（用于容器化部署）

### 方式一：Docker 部署（推荐）

#### 1. 配置环境变量

创建 `backend/.env` 文件：

```env
# 生产环境必须设置，JWT 密钥
JWT_SECRET_KEY=your-secret-key-here

# CORS 允许的源
CORS_ORIGINS=http://localhost

# Anthropic API Key（Claude Code 使用）
ANTHROPIC_API_KEY=sk-ant-xxxxx
```

#### 2. 配置 Docker Compose

```yaml
# docker-compose.yml
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
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
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

#### 3. 启动服务

```bash
# 构建并启动所有服务
docker-compose up -d --build

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

#### 4. 访问应用

- 前端：http://localhost
- 后端 API：http://localhost:8000
- API 文档：http://localhost:8000/docs

### 方式二：本地开发

#### 安装后端依赖

```bat
cd backend
pip install -r requirements.txt
```

#### 安装前端依赖（已完成，首次 clone 后执行）

```bat
cd frontend
npm install
```

#### 启动

```bat
start.bat
```

或分别启动：

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

## 模型配置

系统支持自定义模型配置，可通过 API 设置：

```bash
# 获取当前模型配置
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/settings/model

# 更新模型配置
curl -X PUT -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-opus-4-7", "base_url": "https://api.anthropic.com"}' \
  http://localhost:8000/api/settings/model
```

支持的配置项：
- `model`: 模型名称（如 `claude-opus-4-7`、`claude-sonnet-4-6` 等）
- `base_url`: API 基础 URL（默认 `https://api.anthropic.com`）
- `api_key`: API Key（优先使用环境变量中的 `ANTHROPIC_API_KEY`）

## Credits 系统

系统自动追踪 API 使用量：

```bash
# 查询 Credits 余额
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/credits

# 记录使用量（通常由系统自动调用）
curl -X POST -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"cache_hit_input": 1000, "cache_miss_input": 500, "output_tokens": 2000}' \
  http://localhost:8000/api/credits/record
```

Credits 包含：
- `cache_hit_input`: 缓存命中的输入 tokens
- `cache_miss_input`: 缓存未命中的输入 tokens
- `output`: 输出的 tokens
- `total_used`: 总使用量

## 功能说明

| 功能 | 说明 |
|------|------|
| **新建分析** | 输入 Amazon Bestsellers URL，自动启动全流水线 |
| **实时进度** | 4 个阶段（CRAWL / CHUNK / ANALYZE / SUMMARY）可视化 + 实时日志 |
| **报告查看** | 5 个 Tab（综合总结 + 4 维度）Markdown 渲染，支持下载 .md |
| **追问 Claude** | 分析完成后可对报告内容自由提问，流式回复 |
| **历史任务** | 左侧边栏记录所有分析任务，可随时回看报告 |
| **模型配置** | 支持自定义 API 模型和配置 |
| **Credits 追踪** | 自动记录 API 使用量 |

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
