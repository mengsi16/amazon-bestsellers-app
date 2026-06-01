# Amazon Bestsellers Summary Web App

Amazon Bestsellers 类目分析 Web 应用。前端提供任务创建、实时进度、报告阅读、追问对话、模型配置和 Credits 统计；后端通过 FastAPI 调用 `agent/` 中的分析流水线。

## 当前结构

```text
amazon-bestsellers/
├── agent/                 # Amazon 分析 Agent 与抓取/切块/审计逻辑
├── backend/               # FastAPI 后端
│   ├── main.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── tests/
├── frontend/              # React + Vite 前端
│   └── src/
│       ├── App.tsx
│       ├── api.ts
│       └── components/
│           ├── AuthPage.tsx
│           ├── LiveStream.tsx
│           ├── NewTaskDialog.tsx
│           ├── ReportViewer.tsx
│           ├── SettingsView.tsx
│           ├── Sidebar.tsx
│           ├── StageRail.tsx
│           └── Toast.tsx
├── scripts/               # 本地辅助脚本
├── docker-compose.yml
├── start.bat
└── ToDo.md
```

本地运行态目录不进仓库：`md/`、`workspace/`、`.playwright-*`、`test-results/`、日志文件、SQLite 数据库、`.env`。

## 功能

| 功能 | 说明 |
|---|---|
| 用户认证 | 注册、登录、JWT 鉴权 |
| 任务管理 | 创建、恢复、刷新、重分析、取消、删除任务 |
| 实时进度 | 通过 SSE 展示分析流水线事件 |
| 报告阅读 | 查看综合报告和各维度 Markdown 报告 |
| 追问对话 | 基于已完成任务继续提问 |
| 模型配置 | 多套模型配置，API Key 加密存储 |
| Credits 统计 | 从 CLI stream-json 结果中提取 token 与费用信息 |
| 历史恢复 | stream items 与 chat messages 持久化到 SQLite |

## 环境变量

复制 `.env.example` 为实际环境文件，生产环境必须显式设置密钥。

| 变量名 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `ENV` | 否 | `development` | `development` 或 `production` |
| `JWT_SECRET_KEY` | 生产环境是 | 开发环境随机生成 | JWT 签名密钥 |
| `JWT_SECRET_KEY_PREVIOUS` | 否 | 空 | 密钥轮换期间的旧 JWT 密钥 |
| `CORS_ORIGINS` | 否 | `http://localhost:5173` | 允许的前端来源，逗号分隔 |
| `CREDITS_ENCRYPTION_KEY` | 生产环境是 | 开发环境随机生成 | 模型配置 API Key 加密密钥 |
| `PORT` | 否 | `8000` | 后端端口 |
| `DB_PATH` | 否 | `backend/conversations.db` | SQLite 数据库路径 |
| `WORKSPACE_BASE` | 否 | `backend/workspace` | 分析产物目录 |

`.env` 和数据库文件只用于本地运行，不提交到 Git。

## Docker 部署

```bash
docker-compose up -d --build
```

访问地址：

| 服务 | 地址 |
|---|---|
| 前端 | `http://localhost` |
| 后端 API | `http://localhost:8000` |
| API 文档 | `http://localhost:8000/docs` |

Docker 使用命名 volume 保存运行态数据：

| Volume | 容器路径 | 内容 |
|---|---|---|
| `backend-data` | `/app/workspace` | 分析产物 |
| `backend-db` | `/app/data` | SQLite 数据库 |

## 本地开发

后端：

```bat
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

前端：

```bat
cd frontend
npm install
npm run dev
```

默认打开 `http://localhost:5173`。

## 测试

后端测试：

```bat
cd backend
pytest
```

前端构建检查：

```bat
cd frontend
npm run build
```

本地 Playwright 截图和测试产物会写入 `.playwright-*` 或 `test-results/`，这些目录已被 `.gitignore` 排除。

## 数据持久化

后端使用 SQLite 保存任务、会话、stream items、chat messages、模型配置和 Credits 记录。默认开发路径为 `backend/conversations.db`，Docker 路径为 `/app/data/conversations.db`。

数据库可能包含用户配置、加密后的 API Key、任务历史和聊天内容，不应提交到仓库。

## 本地辅助脚本

| 脚本 | 用途 |
|---|---|
| `scripts/check_products.ps1` | 检查指定 workspace 下商品 HTML 文件大小的本地排查脚本 |

该脚本默认保留为本地排查工具，使用前按实际 `browse_node_id` 修改 workspace 路径。

## License

本项目采用 Apache License 2.0，详见 `LICENSE`。

## Credits

| 项目 | 地址 |
|---|---|
| amazon-bestsellers-summary-agent | `https://github.com/anthropics/amazon-bestsellers-summary-agent` |
| Claude Code | `https://docs.anthropic.com/en/docs/claude-code` |
| FastAPI | `https://fastapi.tiangolo.com/` |
| React | `https://react.dev/` |
| Vite | `https://vite.dev/` |
