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
└── start.bat       ← 一键启动脚本（Windows）
```

## 快速启动

### 前置要求

- Node.js >= 18
- Python >= 3.10
- Claude Code CLI 已安装并可在终端执行 `claude`

### 安装后端依赖

```bat
cd backend
pip install -r requirements.txt
```

### 安装前端依赖（已完成，首次 clone 后执行）

```bat
cd frontend
npm install
```

### 启动

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

## 功能说明

| 功能 | 说明 |
|------|------|
| **新建分析** | 输入 Amazon Bestsellers URL，自动启动全流水线 |
| **实时进度** | 4 个阶段（CRAWL / CHUNK / ANALYZE / SUMMARY）可视化 + 实时日志 |
| **报告查看** | 5 个 Tab（综合总结 + 4 维度）Markdown 渲染，支持下载 .md |
| **追问 Claude** | 分析完成后可对报告内容自由提问，流式回复 |
| **历史任务** | 左侧边栏记录所有分析任务，可随时回看报告 |

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
