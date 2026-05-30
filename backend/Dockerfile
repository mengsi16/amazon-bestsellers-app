FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖（包括 curl 用于健康检查）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 Node.js 18.x（Claude Code 需要 Node.js）
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g npm@latest

# 安装 Claude Code CLI（固定版本）
# https://docs.anthropic.com/en/docs/claude-code/setup
# 推荐版本：v1.0.0+
ARG CLAUDE_VERSION=1.0.45
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_VERSION}

# 验证 Claude Code 安装
RUN claude --version || echo "Warning: claude CLI installation failed"

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安装 Playwright（用于 PDF 导出）
RUN playwright install --with-deps chromium

# 复制后端代码
COPY . .

# 创建 workspace 目录
RUN mkdir -p /app/workspace

# 暴露端口
EXPOSE 8000

# 启动命令（单进程，因为有全局状态和 SQLite）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]