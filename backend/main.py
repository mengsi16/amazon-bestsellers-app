#!/usr/bin/env python3
"""
Amazon Bestsellers Summary — Web Backend (FastAPI)

Provides:
  POST /api/tasks                   — Start a new analysis task
  GET  /api/tasks                   — List all tasks
  GET  /api/tasks/{task_id}         — Get task status + report paths
  GET  /api/tasks/{task_id}/progress — SSE stream of progress events
  GET  /api/tasks/{task_id}/reports  — Get report file contents
  POST /api/tasks/{task_id}/chat     — Proxy a follow-up question to claude
  POST /api/tasks/{task_id}/resume   — Resume an incomplete task
  POST /api/tasks/{task_id}/refresh  — Incremental update (re-crawl ranks only)
  POST /api/tasks/{task_id}/reanalyze — Full re-analysis (wipe workspace, start fresh)
  DELETE /api/tasks/{task_id}        — Delete a task record
"""

import atexit
import logging
import asyncio
import json
import os
import re
import secrets
import signal
import shutil
import sqlite3
import subprocess
import sys
import traceback
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import AsyncGenerator, Optional

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

import aiofiles
import bcrypt
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

# ── Paths ──────────────────────────────────────────────────────────────────────

BACKEND_DIR = Path(__file__).parent
APP_DIR = BACKEND_DIR.parent
AGENT_DIR = APP_DIR / "agent"
PLUGIN_DIR = str(AGENT_DIR)
AGENT_ID = "amazon-bestsellers-summary:amazon-bestsellers-orchestrator"

# Workspace is ALWAYS at <APP_DIR>/workspace/ — fully deterministic regardless of
# the process launch directory. The claude subprocess is started with cwd=APP_DIR
# so the agent orchestrator writes to the exact same location.
WORKSPACE_BASE = APP_DIR / "workspace"
WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
SUBPROCESS_CWD = APP_DIR

# Simple JSON file-based task store
TASKS_FILE = BACKEND_DIR / "tasks.json"
ANALYSIS_META_FILE = ".analysis_meta.json"

# ── Logging ────────────────────────────────────────────────────────────────────

LOGGER = logging.getLogger("amazon_bestsellers")
if not LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)

# ── SQLite for persistent conversation history ─────────────────────────────────
DB_PATH = BACKEND_DIR / "conversations.db"


def _init_db() -> sqlite3.Connection:
    """Create tables if they don't exist and return a connection."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stream_items (
            task_id   TEXT NOT NULL,
            item_id   TEXT NOT NULL,
            kind      TEXT NOT NULL,
            role      TEXT NOT NULL DEFAULT '',
            content   TEXT NOT NULL DEFAULT '',
            meta_json TEXT NOT NULL DEFAULT '{}',
            final     INTEGER NOT NULL DEFAULT 0,
            version   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (task_id, item_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id    TEXT NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_stream_items_task
        ON stream_items(task_id, version)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_task
        ON chat_messages(task_id, id)
    """)
    # T4: sessions 表 — 持久化 Claude Code session 信息
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            task_id      TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT '',
            last_used_at TEXT NOT NULL DEFAULT '',
            expires_at   TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_task
        ON sessions(task_id)
    """)
    # tasks 表 — 持久化任务信息（替代 tasks.json）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id            TEXT PRIMARY KEY,
            url           TEXT NOT NULL,
            browse_node_id TEXT NOT NULL,
            model         TEXT,
            session_id    TEXT,
            status        TEXT NOT NULL DEFAULT 'pending',
            created_at    TEXT NOT NULL DEFAULT '',
            updated_at    TEXT NOT NULL DEFAULT '',
            workspace_path TEXT NOT NULL DEFAULT '',
            error         TEXT,
            owner_id      TEXT,
            is_public     INTEGER NOT NULL DEFAULT 0,
            data_json     TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_owner
        ON tasks(owner_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_status
        ON tasks(status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_browse_node
        ON tasks(browse_node_id)
    """)
    # T1: users 表 — 用户注册与登录
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT ''
        )
    """)
    # model_configs 表 — 用户模型配置
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_configs (
            id              TEXT PRIMARY KEY,
            user_id         TEXT NOT NULL,
            name            TEXT NOT NULL,
            model           TEXT NOT NULL DEFAULT 'claude-3-5-sonnet-20241022',
            api_key_encrypted TEXT NOT NULL,
            base_url        TEXT,
            is_default      INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_configs_user ON model_configs(user_id)")
    # credits_log 表 — API 使用量明细
    conn.execute("""
        CREATE TABLE IF NOT EXISTS credits_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id          TEXT NOT NULL,
            user_id          TEXT NOT NULL,
            cache_hit_input  INTEGER NOT NULL DEFAULT 0,
            cache_miss_input INTEGER NOT NULL DEFAULT 0,
            output_tokens    INTEGER NOT NULL DEFAULT 0,
            cost_usd         REAL NOT NULL DEFAULT 0,
            model            TEXT,
            created_at       TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_credits_log_user ON credits_log(user_id)")
    conn.commit()
    return conn


_db = _init_db()

# ── Fernet 加密工具 ───────────────────────────────────────────────────────────

def _get_fernet() -> Fernet:
    """获取 Fernet 加密实例，密钥从环境变量读取。"""
    key = os.environ.get("CREDITS_ENCRYPTION_KEY")
    if not key:
        if os.environ.get("ENV") == "production":
            raise RuntimeError("CREDITS_ENCRYPTION_KEY 环境变量未设置")
        # 开发环境使用临时密钥
        key = Fernet.generate_key().decode() if hasattr(Fernet, 'generate_key') else "devkey1234567890"
    if len(key) < 32:
        key = key.zfill(32)[:32]
    # Fernet 密钥需要是 32 字节且用 base64 编码
    import base64
    key_bytes = key.encode()[:32]
    while len(key_bytes) < 32:
        key_bytes += key_bytes
    fernet_key = base64.urlsafe_b64encode(key_bytes[:32])
    return Fernet(fernet_key)


def _encrypt_api_key(api_key: str) -> str:
    """加密 API key。"""
    try:
        f = _get_fernet()
        return f.encrypt(api_key.encode()).decode()
    except Exception:
        LOGGER.warning("API key 加密失败，将存储明文")
        return api_key


def _decrypt_api_key(encrypted: str) -> str:
    """解密 API key。"""
    try:
        f = _get_fernet()
        return f.decrypt(encrypted.encode()).decode()
    except Exception:
        # 可能不是加密的，直接返回
        return encrypted


# ── JWT 认证 ──────────────────────────────────────────────────────────────────

_jwt_env = os.environ.get("JWT_SECRET_KEY")
if not _jwt_env:
    if os.environ.get("ENV") == "production":
        LOGGER.error("JWT_SECRET_KEY 环境变量未设置，生产环境必须配置。拒绝启动。")
        raise SystemExit(1)
    LOGGER.warning("JWT_SECRET_KEY 环境变量未设置，使用随机密钥（每次启动不同，仅适合开发环境）")
    _jwt_env = secrets.token_urlsafe(32)
JWT_SECRET_KEY_CURRENT = _jwt_env
JWT_SECRET_KEY_PREVIOUS = os.environ.get("JWT_SECRET_KEY_PREVIOUS", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

# 需要排除在认证之外的路径
_AUTH_EXEMPT_PATHS = {
    "/api/auth/register",
    "/api/auth/login",
    "/api/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def _create_jwt(user_id: str, username: str) -> str:
    from datetime import timedelta
    expire = datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)
    payload = {"sub": user_id, "username": username, "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY_CURRENT, algorithm=JWT_ALGORITHM)


def _decode_jwt(token: str) -> Optional[dict]:
    """尝试用 current key 解码，失败则用 previous key。"""
    for key in [JWT_SECRET_KEY_CURRENT, JWT_SECRET_KEY_PREVIOUS]:
        if not key:
            continue
        try:
            return jwt.decode(token, key, algorithms=[JWT_ALGORITHM])
        except JWTError:
            continue
    return None


def _get_current_user_id(request: Request) -> Optional[str]:
    """从 Authorization header 提取 user_id，未认证返回 None。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    payload = _decode_jwt(token)
    if payload is None:
        return None
    return payload.get("sub")


def _require_user(request: Request) -> str:
    """强制要求登录，返回 user_id；未登录抛 401。"""
    user_id = _get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录，请先登录")
    return user_id


# ── Models (auth) ─────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


def _save_chat_message(task_id: str, role: str, content: str) -> None:
    """Persist a chat message (user or assistant) to SQLite."""
    try:
        _db.execute(
            "INSERT INTO chat_messages (task_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (task_id, role, content, datetime.utcnow().isoformat()),
        )
        _db.commit()
    except Exception:
        LOGGER.error("Failed to save chat message for task '%s'", task_id, exc_info=True)


def _load_chat_history(task_id: str) -> list[dict]:
    """Load all chat messages for a task from SQLite, ordered by id."""
    try:
        rows = _db.execute(
            "SELECT id, role, content, created_at FROM chat_messages WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        return [{"id": r[0], "role": r[1], "content": r[2], "created_at": r[3]} for r in rows]
    except Exception:
        LOGGER.error("Failed to load chat history for task '%s'", task_id, exc_info=True)
        return []


def _load_stream_history(task_id: str) -> tuple[list[dict], list[str]]:
    """Load all stream items for a task from SQLite.

    Returns (items_list, order) where items_list is in version order.
    """
    try:
        rows = _db.execute(
            """SELECT item_id, kind, role, content, meta_json, final, version
               FROM stream_items WHERE task_id = ? ORDER BY version""",
            (task_id,),
        ).fetchall()
        items = []
        order = []
        for r in rows:
            item = {
                "id": r[0],
                "kind": r[1],
                "role": r[2],
                "content": r[3],
                "meta": json.loads(r[4]) if r[4] and r[4] != "{}" else None,
                "final": bool(r[5]),
                "v": r[6],
            }
            items.append(item)
            order.append(r[0])
        return items, order
    except Exception:
        LOGGER.error("Failed to load stream history for task '%s'", task_id, exc_info=True)
        return [], []


def _delete_task_history(task_id: str) -> None:
    """Delete all SQLite records for a task."""
    try:
        _db.execute("DELETE FROM stream_items WHERE task_id = ?", (task_id,))
        _db.execute("DELETE FROM chat_messages WHERE task_id = ?", (task_id,))
        _db.execute("DELETE FROM sessions WHERE task_id = ?", (task_id,))
        _db.commit()
    except Exception:
        LOGGER.error("Failed to delete task history for task '%s'", task_id, exc_info=True)

# ── Models ─────────────────────────────────────────────────────────────────────


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task(BaseModel):
    id: str
    url: str
    browse_node_id: str
    model: Optional[str] = None
    session_id: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    created_at: str
    updated_at: str
    workspace_path: str
    error: Optional[str] = None
    # T2: 用户权限与任务归属
    owner_id: Optional[str] = None
    is_public: bool = False


class CreateTaskRequest(BaseModel):
    url: str
    model: Optional[str] = None


class ChatRequest(BaseModel):
    message: str


# ── Model Configs API ──────────────────────────────────────────────────────────

class ModelConfigCreate(BaseModel):
    name: str
    model: str = "claude-3-5-sonnet-20241022"
    api_key: str  # 明文传入，内部加密存储
    base_url: Optional[str] = None
    is_default: bool = False


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    is_default: Optional[bool] = None


# ── In-memory state ────────────────────────────────────────────────────────────

# task_id -> subprocess.Popen
_running_processes: dict[str, subprocess.Popen] = {}

# browse_node_id -> task_id for analyses that have been scheduled or are actively running
_active_browse_node_tasks: dict[str, str] = {}

# task_id -> True — prevents concurrent chat requests on the same task
_active_chat_tasks: dict[str, bool] = {}

# task_id -> list of log lines collected so far (ring buffer, max 2000)
# These are the raw stdout lines (including stream-json JSON) kept for debug.
_task_logs: dict[str, list[str]] = {}

# task_id -> ordered list of item_ids (stream display order)
_task_stream_order: dict[str, list[str]] = {}

# task_id -> {item_id: item_dict}
# An "item" is one conversation-level entity (assistant text block, tool call,
# subagent panel, system note, or final result). Each carries a monotonic `v`
# (version) so SSE generators can diff and emit only changed items.
_task_stream_items: dict[str, dict[str, dict]] = {}

# task_id -> monotonic version counter
_task_stream_version: dict[str, int] = {}

# task_id -> parser state dict (current message / text block / tool block)
_task_parser_state: dict[str, dict] = {}


# ── 优雅停机 ─────────────────────────────────────────────────────────────────

def _cleanup_on_shutdown():
    """进程退出时清理所有运行中的子进程。"""
    LOGGER.info("正在清理 %d 个运行中的子进程...", len(_running_processes))
    for task_id, proc in list(_running_processes.items()):
        try:
            proc.kill()
            LOGGER.info("已终止子进程: task_id=%s, pid=%d", task_id, proc.pid)
        except Exception:
            LOGGER.warning("终止子进程失败: task_id=%s", task_id, exc_info=True)
    _running_processes.clear()


atexit.register(_cleanup_on_shutdown)

# 注册信号处理（仅在非 Windows 平台）
if sys.platform != "win32":
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda s, f: (_cleanup_on_shutdown(), sys.exit(0)))

# ── Helpers ────────────────────────────────────────────────────────────────────


def _extract_browse_node_id(url: str) -> str:
    """Extract the numeric Browse Node ID from an Amazon Bestsellers URL."""
    match = re.search(r"/gp/bestsellers/[^/]+/(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"/(\d{6,12})/?$", url.rstrip("/"))
    if match:
        return match.group(1)
    raise ValueError(f"Cannot extract Browse Node ID from URL: {url}")


def _normalize_error_message(value: object, fallback: str) -> str:
    text = str(value).strip()
    if text:
        return text
    if isinstance(value, BaseException):
        rep = repr(value).strip()
        if rep:
            return rep
        return value.__class__.__name__
    return fallback


# ── Canonical workspace ────────────────────────────────────────────────────────
# Single source of truth: every task for a given `browse_node_id` lives at
# `APP_DIR/workspace/{browse_node_id}`.
#
# This matches what the orchestrator agent derives on its own, because the
# claude subprocess is launched with `cwd=APP_DIR` and the agent's rule is
# `{CWD}/workspace/{id}`. Keeping both parties in lockstep on this single path
# avoids the "task says one place, agent writes another" bug.
#
# `_resolve_workspace_path` stays as a helper — it just returns the canonical
# path now, no scanning — so that any previous call sites keep working.

def _resolve_workspace_path(browse_node_id: str, current_path: Optional[str] = None) -> Path:
    """Canonical workspace path for a browse_node_id — always under APP_DIR.

    `current_path` is accepted for backwards compatibility with older callers
    but ignored. We never follow a stored path to a non-canonical location.
    """
    _ = current_path  # intentionally unused
    return WORKSPACE_BASE / browse_node_id


def _analysis_meta_path(workspace_path: str | Path) -> Path:
    return Path(workspace_path) / ANALYSIS_META_FILE


def _load_analysis_meta(workspace_path: str | Path) -> dict:
    meta_path = _analysis_meta_path(workspace_path)
    if not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        LOGGER.error("Failed to load analysis_meta from '%s'", meta_path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def _save_analysis_meta(workspace_path: str | Path, patch: dict) -> None:
    workspace = Path(workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    meta_path = _analysis_meta_path(workspace)
    current = _load_analysis_meta(workspace)
    merged = {**current, **patch}
    meta_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def _sync_task_analysis_meta(task: Task) -> None:
    _save_analysis_meta(task.workspace_path, {
        "browse_node_id": task.browse_node_id,
        "workspace_path": task.workspace_path,
        "last_task_id": task.id,
        "last_url": task.url,
        "session_id": task.session_id,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    })


def _extract_stream_session_id(raw_line: str) -> Optional[str]:
    stripped = raw_line.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        ev = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if ev.get("type") == "system" and ev.get("subtype") == "init":
        session_id = str(ev.get("session_id") or "").strip()
        return session_id or None
    return None


def _persist_task_session_id(task_id: str, session_id: str) -> None:
    session_id = session_id.strip()
    if not session_id:
        return
    tasks = _load_tasks()
    task = tasks.get(task_id)
    if task is None:
        return
    if task.session_id != session_id:
        task.session_id = session_id
        tasks[task_id] = task
        _save_tasks(tasks)
    _sync_task_analysis_meta(task)
    # T4: 同步写入 sessions 表
    _upsert_session(session_id, task_id)


def _upsert_session(session_id: str, task_id: str) -> None:
    """T4: 写入或更新 sessions 表。"""
    now = datetime.utcnow().isoformat()
    try:
        _db.execute(
            """INSERT INTO sessions (session_id, task_id, created_at, last_used_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                   last_used_at = excluded.last_used_at
            """,
            (session_id, task_id, now, now),
        )
        _db.commit()
    except Exception:
        LOGGER.error("Failed to upsert session '%s'", session_id, exc_info=True)


def _load_session_for_task(task_id: str) -> Optional[str]:
    """T4: 从 sessions 表恢复 task 的 session_id。"""
    try:
        row = _db.execute(
            "SELECT session_id FROM sessions WHERE task_id = ? ORDER BY last_used_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return row[0] if row else None
    except Exception:
        LOGGER.error("Failed to load session for task '%s'", task_id, exc_info=True)
        return None


def _delete_sessions_for_task(task_id: str) -> None:
    """删除 task 关联的 sessions 记录。"""
    try:
        _db.execute("DELETE FROM sessions WHERE task_id = ?", (task_id,))
        _db.commit()
    except Exception:
        LOGGER.error("Failed to delete sessions for task '%s'", task_id, exc_info=True)


def _find_running_task_for_browse_node(browse_node_id: str, exclude_task_id: Optional[str] = None) -> Optional[str]:
    active_task_id = _active_browse_node_tasks.get(browse_node_id)
    if active_task_id and active_task_id != exclude_task_id:
        return active_task_id
    tasks = _load_tasks()
    for running_task_id in list(_running_processes.keys()):
        if running_task_id == exclude_task_id:
            continue
        task = tasks.get(running_task_id)
        if task and task.browse_node_id == browse_node_id:
            return running_task_id
    return None


def _assert_browse_node_not_running(browse_node_id: str, exclude_task_id: Optional[str] = None) -> None:
    running_task_id = _find_running_task_for_browse_node(browse_node_id, exclude_task_id)
    if running_task_id:
        raise HTTPException(status_code=409, detail=f"该类目已有运行中的任务：{running_task_id}")


def _mark_browse_node_active(browse_node_id: str, task_id: str) -> None:
    _active_browse_node_tasks[browse_node_id] = task_id


def _clear_browse_node_active(browse_node_id: str, task_id: str) -> None:
    if _active_browse_node_tasks.get(browse_node_id) == task_id:
        _active_browse_node_tasks.pop(browse_node_id, None)


def _reconcile_task(task: Task) -> Task:
    # Always pin workspace_path to canonical; legacy values get rewritten.
    task.workspace_path = str(_resolve_workspace_path(task.browse_node_id))
    meta = _load_analysis_meta(task.workspace_path)
    meta_session_id = meta.get("session_id")
    if isinstance(meta_session_id, str) and meta_session_id.strip():
        task.session_id = meta_session_id.strip()
    elif task.session_id:
        _sync_task_analysis_meta(task)
    # T4: 若文件系统和 task 都无 session_id，尝试从 SQLite sessions 表恢复
    if not task.session_id:
        db_session = _load_session_for_task(task.id)
        if db_session:
            task.session_id = db_session
            _sync_task_analysis_meta(task)
    phases = _build_progress_from_workspace(task.workspace_path)
    done = [k for k, v in phases.items() if v]
    missing = [k for k, v in phases.items() if not v]

    # If summary exists, the pipeline is effectively complete regardless of
    # stored status — agent may have finished but crashed before updating.
    if phases["summary"]:
        task.status = TaskStatus.COMPLETED
        task.error = None
        return task

    # Summary missing — task is not complete.
    if task.status == TaskStatus.COMPLETED:
        # Was marked complete but summary gone (data loss / migration).
        # Flip to RUNNING so user can resume.
        task.status = TaskStatus.RUNNING
        task.error = f"Pipeline incomplete (summary missing). Done: {done}. Missing: {missing}. Click 'Continue' to resume."
    elif task.status == TaskStatus.FAILED:
        # Re-evaluate: if there's partial progress, make it resumable.
        if done:
            task.status = TaskStatus.RUNNING
            task.error = f"Pipeline incomplete. Done: {done}. Missing: {missing}. Click 'Continue' to resume."
        else:
            task.error = f"Pipeline incomplete. Done: {done}. Missing: {missing}"

    return task


def _load_tasks() -> dict[str, Task]:
    """从 SQLite 加载所有任务。首次运行时自动迁移 tasks.json 数据。"""
    try:
        # 首次迁移：如果 tasks.json 存在且 tasks 表为空，导入旧数据
        if TASKS_FILE.exists():
            row = _db.execute("SELECT COUNT(*) FROM tasks").fetchone()
            if row and row[0] == 0:
                _migrate_tasks_from_json()

        rows = _db.execute(
            "SELECT id, url, browse_node_id, model, session_id, status, "
            "created_at, updated_at, workspace_path, error, owner_id, is_public "
            "FROM tasks"
        ).fetchall()
        tasks = {}
        for r in rows:
            task = Task(
                id=r[0], url=r[1], browse_node_id=r[2], model=r[3],
                session_id=r[4], status=r[5], created_at=r[6],
                updated_at=r[7], workspace_path=r[8], error=r[9],
                owner_id=r[10], is_public=bool(r[11]),
            )
            task = _reconcile_task(task)
            tasks[task.id] = task
        return tasks
    except Exception:
        LOGGER.error("Failed to load tasks from SQLite", exc_info=True)
        return {}


def _migrate_tasks_from_json() -> None:
    """将 tasks.json 数据迁移到 SQLite。"""
    try:
        raw = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        for tid, data in raw.items():
            _db.execute(
                "INSERT OR IGNORE INTO tasks "
                "(id, url, browse_node_id, model, session_id, status, "
                "created_at, updated_at, workspace_path, error, owner_id, is_public) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data.get("id", tid), data.get("url", ""),
                    data.get("browse_node_id", ""), data.get("model"),
                    data.get("session_id"), data.get("status", "pending"),
                    data.get("created_at", ""), data.get("updated_at", ""),
                    data.get("workspace_path", ""), data.get("error"),
                    data.get("owner_id"), int(data.get("is_public", False)),
                ),
            )
        _db.commit()
        # 迁移完成后重命名旧文件
        backup = TASKS_FILE.with_suffix(".json.bak")
        TASKS_FILE.rename(backup)
        LOGGER.info("已将 tasks.json 迁移到 SQLite，旧文件备份为 %s", backup)
    except Exception:
        LOGGER.error("tasks.json 迁移失败", exc_info=True)


def _save_tasks(tasks: dict[str, Task]) -> None:
    """将任务保存到 SQLite。"""
    try:
        for tid, task in tasks.items():
            _db.execute(
                "INSERT OR REPLACE INTO tasks "
                "(id, url, browse_node_id, model, session_id, status, "
                "created_at, updated_at, workspace_path, error, owner_id, is_public) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task.id, task.url, task.browse_node_id, task.model,
                    task.session_id, task.status, task.created_at,
                    task.updated_at, task.workspace_path, task.error,
                    task.owner_id, int(task.is_public),
                ),
            )
        _db.commit()
    except Exception:
        LOGGER.error("Failed to save tasks to SQLite", exc_info=True)


def _update_task(task_id: str, **kwargs) -> Task:
    """更新单个任务的字段。"""
    tasks = _load_tasks()
    if task_id not in tasks:
        raise KeyError(task_id)
    t = tasks[task_id]
    for k, v in kwargs.items():
        setattr(t, k, v)
    t.updated_at = datetime.utcnow().isoformat()
    # 直接更新 SQLite 中的单条记录
    try:
        _db.execute(
            "UPDATE tasks SET status=?, updated_at=?, error=?, session_id=?, "
            "workspace_path=?, owner_id=?, is_public=? WHERE id=?",
            (t.status, t.updated_at, t.error, t.session_id,
             t.workspace_path, t.owner_id, int(t.is_public), task_id),
        )
        _db.commit()
    except Exception:
        LOGGER.error("Failed to update task '%s' in SQLite", task_id, exc_info=True)
    return t


def _append_log(task_id: str, line: str):
    if task_id not in _task_logs:
        _task_logs[task_id] = []
    buf = _task_logs[task_id]
    buf.append(line)
    if len(buf) > 2000:
        _task_logs[task_id] = buf[-2000:]


def _log_and_stream(task_id: str, line: str) -> None:
    """Append to both the raw log and the structured stream.

    Preferred for `[SYSTEM]`-prefixed backend-originated messages that the
    user should see as conversational items in the live stream.
    """
    _append_log(task_id, line)
    _feed_stream_line(task_id, line)


# ── Stream items (structured conversational log) ───────────────────────────────


def _next_item_version(task_id: str) -> int:
    v = _task_stream_version.get(task_id, 0) + 1
    _task_stream_version[task_id] = v
    return v


def _stream_upsert(task_id: str, item_id: str, patch: dict) -> dict:
    """Insert a new stream item or merge a patch into an existing one.

    Also persists the final state to SQLite so conversation history survives
    server restarts and task switching.
    """
    items = _task_stream_items.setdefault(task_id, {})
    order = _task_stream_order.setdefault(task_id, [])
    existing = items.get(item_id)
    if existing is None:
        item = {"id": item_id, "v": _next_item_version(task_id), **patch}
        items[item_id] = item
        order.append(item_id)
    else:
        # Merge-patch (shallow); for appending text, caller should pass {"append": "..."}
        if "append" in patch:
            existing["content"] = (existing.get("content") or "") + patch.pop("append")
        # Any `meta` merges dict-wise
        if "meta" in patch and isinstance(existing.get("meta"), dict):
            existing["meta"] = {**existing["meta"], **patch.pop("meta")}
        existing.update(patch)
        existing["v"] = _next_item_version(task_id)
        item = existing

    # ── Persist to SQLite ──────────────────────────────────────────────────
    try:
        _db.execute(
            """INSERT INTO stream_items (task_id, item_id, kind, role, content, meta_json, final, version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id, item_id) DO UPDATE SET
                   kind=excluded.kind, role=excluded.role, content=excluded.content,
                   meta_json=excluded.meta_json, final=excluded.final, version=excluded.version
            """,
            (
                task_id,
                item_id,
                item.get("kind", ""),
                item.get("role", ""),
                item.get("content", ""),
                json.dumps(item.get("meta"), ensure_ascii=False) if item.get("meta") else "{}",
                1 if item.get("final") else 0,
                item["v"],
                datetime.utcnow().isoformat(),
            ),
        )
        _db.commit()
    except Exception:
        LOGGER.error("SQLite stream_items upsert failed", exc_info=True)

    return item


def _tool_input_summary(tool_name: str, tool_input: dict) -> str:
    """Produce a short one-line summary of a tool invocation for display."""
    if not isinstance(tool_input, dict):
        return str(tool_input)[:200]
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:200]
    if tool_name in ("Read", "Glob", "Grep"):
        return str(tool_input.get("path") or tool_input.get("file_path") or tool_input.get("pattern") or "")[:200]
    if tool_name in ("Write", "Edit", "MultiEdit"):
        return str(tool_input.get("file_path") or tool_input.get("path") or "")[:200]
    if tool_name == "Task" or tool_name == "Agent":
        return str(tool_input.get("subagent_type") or tool_input.get("description") or "")[:200]
    # Generic: take first 200 chars of serialized input
    try:
        return json.dumps(tool_input, ensure_ascii=False)[:200]
    except Exception:
        return str(tool_input)[:200]


def _tool_result_summary(content) -> str:
    """Short preview of a tool result payload."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip().splitlines()[0][:300] if content.strip() else ""
    if isinstance(content, list):
        # Claude Code returns list of {type:"text", text:"..."} or tool_result blocks
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", ""))[:300])
                elif "content" in block:
                    parts.append(str(block["content"])[:300])
            else:
                parts.append(str(block)[:300])
        joined = " | ".join(p for p in parts if p)
        return joined[:300]
    return str(content)[:300]


def _feed_stream_line(task_id: str, raw_line: str) -> None:
    """
    Parse one stdout line from `claude --output-format stream-json` and
    update the structured stream items for the given task.

    Lines that don't parse as JSON are treated as plain text (e.g. uvicorn
    noise or `[SYSTEM]` markers the backend itself injects).
    """
    stripped = raw_line.strip()
    if not stripped:
        return

    # Injected by _run_analysis (not stream-json) — surface as system note.
    if stripped.startswith("[SYSTEM]"):
        item_id = f"sys-{_next_item_version(task_id)}"
        _stream_upsert(task_id, item_id, {
            "kind": "system_note",
            "role": "system",
            "content": stripped[len("[SYSTEM]"):].strip(),
            "final": True,
        })
        return

    if not (stripped.startswith("{") and stripped.endswith("}")):
        # Non-JSON line (extra stderr noise). Keep in raw log only.
        return

    try:
        ev = json.loads(stripped)
    except json.JSONDecodeError:
        return

    session_id = _extract_stream_session_id(stripped)
    if session_id:
        _persist_task_session_id(task_id, session_id)

    state = _task_parser_state.setdefault(task_id, {
        "current_msg_id": None,
        "current_text_id": None,
        "current_tool": None,  # {id, name, input_buf}
        "last_subagent_tool_id": None,
    })

    t = ev.get("type")
    sub = ev.get("subtype")

    if t == "system" and sub == "init":
        sid = ev.get("session_id", "")
        item_id = f"sys-init"
        _stream_upsert(task_id, item_id, {
            "kind": "system_note",
            "role": "system",
            "content": f"Session started — {sid[:12]}…",
            "final": True,
        })
        return

    if t == "stream_event":
        event = ev.get("event") or {}
        et = event.get("type")

        if et == "message_start":
            msg = event.get("message") or {}
            state["current_msg_id"] = msg.get("id")
            return

        if et == "content_block_start":
            block = event.get("content_block") or {}
            idx = event.get("index")
            bt = block.get("type")
            if bt == "text":
                item_id = f"{state.get('current_msg_id') or 'msg'}-text-{idx}"
                state["current_text_id"] = item_id
                _stream_upsert(task_id, item_id, {
                    "kind": "assistant_text",
                    "role": "assistant",
                    "content": "",
                    "final": False,
                })
            elif bt == "tool_use":
                tool_id = block.get("id") or f"tu-{idx}"
                tool_name = block.get("name") or "Tool"
                state["current_tool"] = {"id": tool_id, "name": tool_name, "input_buf": ""}
                _stream_upsert(task_id, tool_id, {
                    "kind": "tool_call",
                    "role": "tool",
                    "content": "",
                    "meta": {
                        "tool_name": tool_name,
                        "status": "starting",
                        "input_summary": "",
                        "subagent_activities": [],
                    },
                    "final": False,
                })
            elif bt == "thinking":
                item_id = f"{state.get('current_msg_id') or 'msg'}-think-{idx}"
                state["current_text_id"] = item_id
                _stream_upsert(task_id, item_id, {
                    "kind": "thinking",
                    "role": "assistant",
                    "content": "",
                    "final": False,
                })
            return

        if et == "content_block_delta":
            delta = event.get("delta") or {}
            dt = delta.get("type")
            if dt == "text_delta":
                tid = state.get("current_text_id")
                if tid:
                    _stream_upsert(task_id, tid, {"append": delta.get("text", "")})
            elif dt == "thinking_delta":
                tid = state.get("current_text_id")
                if tid:
                    _stream_upsert(task_id, tid, {"append": delta.get("thinking", "")})
            elif dt == "input_json_delta":
                tool = state.get("current_tool")
                if tool:
                    tool["input_buf"] += delta.get("partial_json", "")
            return

        if et == "content_block_stop":
            if state.get("current_text_id"):
                _stream_upsert(task_id, state["current_text_id"], {"final": True})
                state["current_text_id"] = None
            elif state.get("current_tool"):
                tool = state["current_tool"]
                parsed_input: dict = {}
                if tool["input_buf"]:
                    try:
                        parsed_input = json.loads(tool["input_buf"])
                    except json.JSONDecodeError:
                        parsed_input = {"_raw": tool["input_buf"][:500]}
                summary = _tool_input_summary(tool["name"], parsed_input)
                is_subagent = tool["name"] in ("Task", "Agent")
                if is_subagent:
                    state["last_subagent_tool_id"] = tool["id"]
                _stream_upsert(task_id, tool["id"], {
                    "meta": {
                        "status": "running",
                        "input_summary": summary,
                        "input_full": parsed_input if len(str(parsed_input)) < 2000 else {"_truncated": True},
                        "is_subagent": is_subagent,
                    },
                })
                state["current_tool"] = None
            return

        # message_delta / message_stop: no UI change needed beyond what we already track
        return

    if t == "user":
        # Tool results coming back to the assistant.
        msg = ev.get("message") or {}
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if not tool_use_id:
                    continue
                is_err = bool(block.get("is_error"))
                summary = _tool_result_summary(block.get("content"))
                items = _task_stream_items.get(task_id, {})
                if tool_use_id in items:
                    _stream_upsert(task_id, tool_use_id, {
                        "meta": {
                            "status": "error" if is_err else "done",
                            "result_summary": summary,
                        },
                        "final": True,
                    })
        return

    if t == "system" and sub == "task_progress":
        tool_use_id = ev.get("tool_use_id") or state.get("last_subagent_tool_id")
        desc = ev.get("description") or ""
        usage = ev.get("usage") or {}
        duration_ms = usage.get("duration_ms", 0)
        if tool_use_id:
            existing = _task_stream_items.get(task_id, {}).get(tool_use_id)
            if existing is not None:
                activities = list((existing.get("meta") or {}).get("subagent_activities") or [])
                activities.append({
                    "description": desc[:200],
                    "duration_ms": duration_ms,
                    "tool_uses": usage.get("tool_uses"),
                })
                if len(activities) > 50:
                    activities = activities[-50:]
                _stream_upsert(task_id, tool_use_id, {
                    "meta": {
                        "status": "running",
                        "subagent_activities": activities,
                        "last_activity": desc[:200],
                        "last_duration_ms": duration_ms,
                    },
                })
        return

    if t == "system" and sub in ("task_updated", "task_notification"):
        tool_use_id = ev.get("tool_use_id") or state.get("last_subagent_tool_id")
        patch = ev.get("patch") or {}
        new_status = patch.get("status") or ev.get("status")
        if tool_use_id and new_status:
            _stream_upsert(task_id, tool_use_id, {
                "meta": {"status": new_status},
            })
        return

    if t == "result":
        is_err = bool(ev.get("is_error"))
        result_text = ev.get("result") or ""
        # With --include-partial-messages on, the same text already arrived as
        # content_block_delta events and was rendered as assistant_text. Only
        # surface it again when the orchestrator failed (error payloads are
        # often only present on the result event).
        display_content = result_text if is_err else ""
        item_id = "final-result"
        _stream_upsert(task_id, item_id, {
            "kind": "final_result",
            "role": "assistant",
            "content": display_content,
            "meta": {
                "is_error": is_err,
                "duration_ms": ev.get("duration_ms"),
                "total_cost_usd": ev.get("total_cost_usd"),
                "num_turns": ev.get("num_turns"),
            },
            "final": True,
        })
        return


def _reset_task_stream(task_id: str) -> None:
    _task_logs[task_id] = []
    _task_stream_items[task_id] = {}
    _task_stream_order[task_id] = []
    _task_stream_version[task_id] = 0
    _task_parser_state[task_id] = {
        "current_msg_id": None,
        "current_text_id": None,
        "current_tool": None,
        "last_subagent_tool_id": None,
    }


def _spawn_process(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        cwd=str(SUBPROCESS_CWD),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


async def _read_process_lines(proc: subprocess.Popen, on_line) -> None:
    if proc.stdout is None:
        return
    while True:
        raw_line = await asyncio.to_thread(proc.stdout.readline)
        if raw_line == "":
            if proc.poll() is not None:
                break
            await asyncio.sleep(0.05)
            continue
        on_line(raw_line.rstrip())


async def _wait_process(proc: subprocess.Popen) -> int:
    return await asyncio.to_thread(proc.wait)


# ── Progress detection ─────────────────────────────────────────────────────────

_PHASES = [
    ("crawl", "阶段 1: 爬虫 — 爬取类目 & 商品详情"),
    ("chunk", "阶段 2: 分块 + 审计 — 数据分块提取 + 完整性审查"),
    ("analyze", "阶段 3: 分析 — 四维度并行分析"),
    ("summary", "阶段 4: 汇总 — 综合报告"),
    ("qa", "阶段 5: 追问 — 交互式问答（summary 完成后解锁）"),
]


def _build_progress_from_workspace(workspace_path: str) -> dict:
    """Infer progress from files already written to workspace.

    Returns 5 boolean flags — the "qa" stage is considered available once
    summary.md exists (it's an unlock state, not a pipeline phase).
    """
    ws = Path(workspace_path)
    phases = {
        "crawl": False,
        "chunk": False,
        "analyze": False,
        "summary": False,
        "qa": False,
    }
    if ws.exists():
        if (ws / "categories").exists():
            phases["crawl"] = True
        chunks_dir = ws / "chunks"
        if chunks_dir.exists() and any(chunks_dir.iterdir()):
            phases["chunk"] = True
        reports_dir = ws / "reports"
        if reports_dir.exists() and any(reports_dir.glob("*_dim.md")):
            phases["analyze"] = True
        if (ws / "summary.md").exists():
            phases["summary"] = True
            phases["qa"] = True
    return phases


def _get_report_files(workspace_path: str) -> dict[str, Optional[str]]:
    """Return content of all report files if they exist."""
    ws = Path(workspace_path)
    reports: dict[str, Optional[str]] = {
        "summary": None,
        "marketplace": None,
        "reviews": None,
        "aplus": None,
        "fine_grained": None,
    }
    if not ws.exists():
        return reports

    summary_path = ws / "summary.md"
    if summary_path.exists():
        reports["summary"] = summary_path.read_text(encoding="utf-8")

    reports_dir = ws / "reports"
    if reports_dir.exists():
        for md_file in reports_dir.glob("*_dim.md"):
            stem = md_file.stem.lower()
            for key in ("marketplace", "reviews", "aplus", "fine_grained"):
                if key in stem:
                    reports[key] = md_file.read_text(encoding="utf-8")
    return reports


# ── Background task runner ─────────────────────────────────────────────────────


def _load_model_settings(user_id: str) -> dict:
    """从数据库加载用户的模型配置。"""
    try:
        row = _db.execute(
            "SELECT api_key, base_url, model FROM model_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row:
            return {"api_key": row[0] or "", "base_url": row[1] or "", "model": row[2] or ""}
    except Exception:
        LOGGER.error("Failed to load model settings for user '%s'", user_id, exc_info=True)
    return {"api_key": "", "base_url": "", "model": ""}


def _build_analysis_prompt(task: Task, mode: str = "full") -> str:
    mode_intro = {
        "full": "请分析这个类目的 Amazon Bestsellers Top50。",
        "refresh": "这是一次增量更新：请刷新排名，仅处理新增/变化 ASIN，并重新生成报告。",
    }.get(mode, "请分析这个类目的 Amazon Bestsellers Top50。")
    return "\n".join([
        mode_intro,
        f"类目 URL：{task.url}",
        f"browse_node_id：{task.browse_node_id}",
        f"workspace 绝对路径：{task.workspace_path}",
        "重要：本次任务唯一合法的 workspace 就是上面这个绝对路径。",
        "禁止根据当前会话的 CWD、session context、系统上下文或任何其它目录重新推导 workspace。",
        "禁止把 workspace 改写到别的盘符、别的项目目录、或类似 D:\\Niuhui9\\workspace 这样的路径。",
        f"调用 scraper MCP 时，output_dir 必须严格传：{task.workspace_path}",
        "后续所有子 agent、文件读写、报告输出都必须严格使用这个 workspace 绝对路径。",
    ])


async def _run_analysis(task_id: str, task: Task, prompt_override: Optional[str] = None):
    """Launch `claude` CLI as async subprocess and stream its output to log buffer.

    If ``--resume <session_id>`` fails because the session no longer exists
    (``No conversation found with session ID``), the stale session_id is
    cleared and the analysis is retried once without ``--resume``.

    Args:
        task_id: Task identifier.
        task: Task object.
        prompt_override: If provided, use this prompt instead of the default
            "分析这个类目的 Bestsellers Top50：{url}". Used by the refresh
            endpoint to trigger incremental update mode.
    """
    prompt = prompt_override or _build_analysis_prompt(task)

    _reset_task_stream(task_id)
    _log_and_stream(task_id, f"[SYSTEM] 启动分析任务: {task.url}")
    _log_and_stream(task_id, f"[SYSTEM] Workspace: {task.workspace_path}")

    _retried = False
    _skip_cleanup = False

    # 从数据库加载用户模型配置
    model_settings = {"api_key": "", "base_url": "", "model": ""}
    if task.owner_id:
        model_settings = _load_model_settings(task.owner_id)

    try:
        while True:
            _skip_cleanup = False
            cmd = ["claude"]
            if task.session_id:
                cmd.extend(["--resume", task.session_id])

            # 应用用户自定义模型配置
            if model_settings.get("model"):
                cmd.extend(["--model", model_settings["model"]])
            elif task.model:
                cmd.extend(["--model", task.model])

            cmd.extend([
                "-p", prompt,
                "--plugin-dir", PLUGIN_DIR,
                "--agent", AGENT_ID,
                "--dangerously-skip-permissions",
                "--output-format", "stream-json",
                "--verbose",
                "--include-partial-messages",
            ])

            if task.session_id:
                _log_and_stream(task_id, f"[SYSTEM] Resume session: {task.session_id}")
            else:
                _log_and_stream(task_id, f"[SYSTEM] 新建对话（无已有 session）")

            def _on_line(line: str) -> None:
                _append_log(task_id, line)
                _feed_stream_line(task_id, line)

            proc: Optional[subprocess.Popen] = None
            _update_task(task_id, status=TaskStatus.RUNNING)
            proc = _spawn_process(cmd)
            _running_processes[task_id] = proc

            _ANALYSIS_TIMEOUT = 2 * 60 * 60  # 2 小时超时
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        _read_process_lines(proc, _on_line),
                        _wait_process(proc),
                    ),
                    timeout=_ANALYSIS_TIMEOUT,
                )
            except asyncio.TimeoutError:
                LOGGER.error("分析任务 '%s' 超时（%d 秒），强制终止子进程", task_id, _ANALYSIS_TIMEOUT)
                _log_and_stream(task_id, f"[SYSTEM] ❌ 分析超时（{_ANALYSIS_TIMEOUT // 3600} 小时），强制终止进程")
                try:
                    proc.kill()
                except Exception:
                    pass
                _running_processes.pop(task_id, None)
                _update_task(task_id, status=TaskStatus.FAILED, error=f"分析超时（{_ANALYSIS_TIMEOUT // 3600} 小时）")
                _clear_browse_node_active(task.browse_node_id, task_id)
                return
            _running_processes.pop(task_id, None)

            # ── Stale session fallback ──────────────────────────────────
            if proc.returncode != 0 and task.session_id and not _retried:
                logs = _task_logs.get(task_id, [])
                if any("No conversation found with session ID" in line for line in logs):
                    _log_and_stream(task_id, f"[SYSTEM] ⚠️ Session {task.session_id[:12]}… 已失效，将新建对话重试")
                    task.session_id = None
                    _update_task(task_id, session_id=None)
                    _save_analysis_meta(task.workspace_path, {"session_id": ""})
                    # R4: Do NOT call _reset_task_stream — preserve existing stream items
                    _log_and_stream(task_id, f"[SYSTEM] 启动分析任务（重试）: {task.url}")
                    _log_and_stream(task_id, f"[SYSTEM] Workspace: {task.workspace_path}")
                    _retried = True
                    _skip_cleanup = True
                    continue  # retry without --resume

            # Verify actual pipeline completion based on filesystem state, not exit code alone.
            latest_tasks = _load_tasks()
            latest = latest_tasks.get(task_id)
            ws_path = latest.workspace_path if latest else task.workspace_path
            phases = _build_progress_from_workspace(ws_path)
            fully_done = phases["summary"]
            done_phases = [k for k, v in phases.items() if v]

            if proc.returncode == 0 and fully_done:
                _update_task(task_id, status=TaskStatus.COMPLETED)
                _log_and_stream(task_id, "[SYSTEM] ✅ 分析完成！")
            elif proc.returncode == 0:
                missing = [p for p in ("crawl", "chunk", "analyze", "summary") if not phases[p]]
                err = f"Pipeline incomplete. Done: {done_phases}. Missing: {missing}"
                _update_task(task_id, status=TaskStatus.FAILED, error=err)
                _log_and_stream(task_id, f"[SYSTEM] ⚠️ Agent 提前结束但未生成 summary.md：{err}")
            else:
                _update_task(task_id, status=TaskStatus.FAILED, error=f"Exit code {proc.returncode}")
                _log_and_stream(task_id, f"[SYSTEM] ❌ 分析失败，退出码: {proc.returncode}")

            break  # normal exit (retry uses continue)

    except FileNotFoundError:
        msg = "'claude' 命令未找到，请先安装 Claude Code CLI"
        _update_task(task_id, status=TaskStatus.FAILED, error=msg)
        _log_and_stream(task_id, f"[SYSTEM] ❌ {msg}")
    except Exception as e:
        err = _normalize_error_message(e, "Unexpected internal error")
        _update_task(task_id, status=TaskStatus.FAILED, error=err)
        _log_and_stream(task_id, f"[SYSTEM] ❌ 意外错误: {err}")
        for line in traceback.format_exc().rstrip().splitlines():
            _append_log(task_id, line)
    finally:
        _running_processes.pop(task_id, None)
        if not _skip_cleanup:
            _clear_browse_node_active(task.browse_node_id, task_id)


# ── SSE progress generator ─────────────────────────────────────────────────────


async def _progress_generator(task_id: str) -> AsyncGenerator[dict, None]:
    """Yield SSE events:
      - `stream_item`: structured conversational item (add or patch), emits diff-only
      - `phases`:      workspace-inferred 5-stage flags
      - `status`:      task status + error
      - `log`:         kept for backward-compat / debug (raw line index)
      - `done`:        terminal event
    """
    sent_log_idx = 0
    last_phase_state: dict = {}
    # Track last version emitted per item id so we only stream diffs.
    last_item_versions: dict[str, int] = {}

    async def _flush_stream_items():
        items = _task_stream_items.get(task_id, {})
        order = _task_stream_order.get(task_id, [])
        payloads: list[dict] = []
        for iid in order:
            item = items.get(iid)
            if item is None:
                continue
            v = int(item.get("v", 0))
            if v > last_item_versions.get(iid, 0):
                last_item_versions[iid] = v
                payloads.append(item)
        return payloads

    # First cycle: emit a stage catalog so the frontend can render the rail
    # before any pipeline activity has happened.
    yield {
        "event": "stage_catalog",
        "data": json.dumps([{"key": k, "label": lbl} for k, lbl in _PHASES]),
    }

    while True:
        tasks = _load_tasks()
        task = tasks.get(task_id)
        if task is None:
            yield {"event": "error", "data": json.dumps({"message": "task not found"})}
            return

        # Raw log lines (for debug tab / fallback)
        logs = _task_logs.get(task_id, [])
        while sent_log_idx < len(logs):
            line = logs[sent_log_idx]
            yield {
                "event": "log",
                "data": json.dumps({"line": line, "index": sent_log_idx}),
            }
            sent_log_idx += 1

        # Structured stream items (diff only)
        payloads = await _flush_stream_items()
        for item in payloads:
            yield {"event": "stream_item", "data": json.dumps(item)}

        # Phase update if changed
        phases = _build_progress_from_workspace(task.workspace_path)
        if phases != last_phase_state:
            last_phase_state = phases.copy()
            yield {"event": "phases", "data": json.dumps(phases)}

        # Status
        yield {
            "event": "status",
            "data": json.dumps({"status": task.status, "task_id": task_id, "error": task.error}),
        }

        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            # Flush any pending structured items + raw logs one last time.
            logs = _task_logs.get(task_id, [])
            while sent_log_idx < len(logs):
                line = logs[sent_log_idx]
                yield {
                    "event": "log",
                    "data": json.dumps({"line": line, "index": sent_log_idx}),
                }
                sent_log_idx += 1
            final_payloads = await _flush_stream_items()
            for item in final_payloads:
                yield {"event": "stream_item", "data": json.dumps(item)}
            phases = _build_progress_from_workspace(task.workspace_path)
            yield {"event": "phases", "data": json.dumps(phases)}
            yield {"event": "done", "data": json.dumps({"status": task.status})}
            return

        await asyncio.sleep(1.0)


# ── App ─────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Amazon Bestsellers Summary API", version="1.0.0")

# 速率限制
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return StreamingResponse(
        iter([json.dumps({"detail": "请求过于频繁，请稍后重试"})]),
        status_code=429,
        media_type="application/json",
    )

_cors_origins_str = os.environ.get("CORS_ORIGINS", "http://localhost:5173")
_cors_origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── JWT 认证中间件 ────────────────────────────────────────────────────────────

@app.middleware("http")
async def jwt_auth_middleware(request: Request, call_next):
    """对 /api 下非豁免路径强制 JWT 认证。支持 Authorization header 和 ?token= 查询参数。"""
    path = request.url.path
    if path.startswith("/api") and path not in _AUTH_EXEMPT_PATHS:
        # 优先从 header 获取，其次从 query 参数获取（SSE 场景）
        auth = request.headers.get("Authorization", "")
        token = None
        if auth.startswith("Bearer "):
            token = auth[7:]
        else:
            token = request.query_params.get("token")
        if not token:
            return StreamingResponse(
                iter([json.dumps({"detail": "未登录，请先登录"})]),
                status_code=401,
                media_type="application/json",
            )
        payload = _decode_jwt(token)
        if payload is None:
            return StreamingResponse(
                iter([json.dumps({"detail": "Token 无效或已过期"})]),
                status_code=401,
                media_type="application/json",
            )
    return await call_next(request)


# ── 认证接口 ──────────────────────────────────────────────────────────────────

@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    if len(req.username) < 2 or len(req.username) > 50:
        raise HTTPException(status_code=400, detail="用户名长度需在 2-50 之间")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少 6 位")
    user_id = str(uuid.uuid4())[:12]
    now = datetime.utcnow().isoformat()
    password_hash = _hash_password(req.password)
    try:
        _db.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user_id, req.username, password_hash, now),
        )
        _db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="用户名已存在")
    token = _create_jwt(user_id, req.username)
    return {"user_id": user_id, "username": req.username, "token": token}


@app.post("/api/auth/login")
@limiter.limit("5/minute")
async def login(req: LoginRequest, request: Request):
    row = _db.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (req.username,),
    ).fetchone()
    if row is None or not _verify_password(req.password, row[2]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = _create_jwt(row[0], row[1])
    return {"user_id": row[0], "username": row[1], "token": token}


@app.get("/api/auth/me")
async def auth_me(request: Request):
    user_id = _require_user(request)
    row = _db.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"user_id": row[0], "username": row[1], "created_at": row[2]}


# ── 模型配置接口 ────────────────────────────────────────────────────────────

@app.get("/api/settings/model")
async def get_model_settings(request: Request):
    """获取当前用户的模型配置。"""
    user_id = _require_user(request)
    row = _db.execute(
        "SELECT api_key, base_url, model, created_at, updated_at FROM model_settings WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return {"api_key": "", "base_url": "", "model": "", "created_at": "", "updated_at": ""}
    return {
        "api_key": row[0],
        "base_url": row[1],
        "model": row[2],
        "created_at": row[3],
        "updated_at": row[4],
    }


@app.put("/api/settings/model")
async def update_model_settings(req: ModelSettingsRequest, request: Request):
    """更新当前用户的模型配置。"""
    user_id = _require_user(request)
    now = datetime.utcnow().isoformat()

    # 读取现有配置
    existing = _db.execute(
        "SELECT api_key, base_url, model FROM model_settings WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    api_key = req.api_key if req.api_key is not None else (existing[0] if existing else "")
    base_url = req.base_url if req.base_url is not None else (existing[1] if existing else "")
    model = req.model if req.model is not None else (existing[2] if existing else "")

    _db.execute(
        """INSERT INTO model_settings (user_id, api_key, base_url, model, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               api_key = excluded.api_key,
               base_url = excluded.base_url,
               model = excluded.model,
               updated_at = excluded.updated_at
        """,
        (user_id, api_key, base_url, model, now if not existing else existing[0], now),
    )
    _db.commit()
    return {"api_key": api_key, "base_url": base_url, "model": model, "updated_at": now}


# ── Credits 接口 ─────────────────────────────────────────────────────────────

@app.post("/api/credits/record")
async def record_credits(req: CreditsRecordRequest, request: Request):
    """记录一次 API 使用量。"""
    user_id = _require_user(request)
    now = datetime.utcnow().isoformat()

    # 读取现有 credits
    existing = _db.execute(
        "SELECT cache_hit_input, cache_miss_input, output, total_used FROM credits WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    cache_hit = (existing[0] if existing else 0) + req.cache_hit_input
    cache_miss = (existing[1] if existing else 0) + req.cache_miss_input
    output = (existing[2] if existing else 0) + req.output_tokens
    total = cache_hit + cache_miss + output

    _db.execute(
        """INSERT INTO credits (user_id, cache_hit_input, cache_miss_input, output, total_used, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               cache_hit_input = excluded.cache_hit_input,
               cache_miss_input = excluded.cache_miss_input,
               output = excluded.output,
               total_used = excluded.total_used,
               updated_at = excluded.updated_at
        """,
        (user_id, cache_hit, cache_miss, output, total, now),
    )
    _db.commit()
    return {
        "cache_hit_input": cache_hit,
        "cache_miss_input": cache_miss,
        "output": output,
        "total_used": total,
    }


@app.get("/api/credits")
async def get_credits(request: Request):
    """查询用户 Credits 余额。"""
    user_id = _require_user(request)
    row = _db.execute(
        "SELECT cache_hit_input, cache_miss_input, output, total_used, updated_at FROM credits WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return {
            "cache_hit_input": 0,
            "cache_miss_input": 0,
            "output": 0,
            "total_used": 0,
            "updated_at": "",
        }
    return {
        "cache_hit_input": row[0],
        "cache_miss_input": row[1],
        "output": row[2],
        "total_used": row[3],
        "updated_at": row[4],
    }


@app.post("/api/tasks", response_model=Task, response_model_exclude={"workspace_path"})
@limiter.limit("10/minute")
async def create_task(req: CreateTaskRequest, request: Request):
    user_id = _require_user(request)
    try:
        browse_node_id = _extract_browse_node_id(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _assert_browse_node_not_running(browse_node_id)

    task_id = str(uuid.uuid4())[:12]
    now = datetime.utcnow().isoformat()
    workspace_path = str(_resolve_workspace_path(browse_node_id))
    meta = _load_analysis_meta(workspace_path)
    saved_session_id = meta.get("session_id") if isinstance(meta.get("session_id"), str) else None

    task = Task(
        id=task_id,
        url=req.url,
        browse_node_id=browse_node_id,
        model=req.model,
        session_id=saved_session_id.strip() if saved_session_id and saved_session_id.strip() else None,
        status=TaskStatus.PENDING,
        created_at=now,
        updated_at=now,
        workspace_path=workspace_path,
        owner_id=user_id,
    )

    tasks = _load_tasks()
    tasks[task_id] = task
    _save_tasks(tasks)
    _sync_task_analysis_meta(task)
    _mark_browse_node_active(browse_node_id, task_id)

    # Launch async background analysis
    asyncio.create_task(_run_analysis(task_id, task))

    return task


@app.post("/api/tasks/{task_id}/resume", response_model=Task, response_model_exclude={"workspace_path"})
async def resume_task(task_id: str):
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    if task.status == TaskStatus.RUNNING and task_id in _running_processes:
        raise HTTPException(status_code=409, detail="Task is already running")
    _assert_browse_node_not_running(task.browse_node_id, exclude_task_id=task_id)

    task.workspace_path = str(_resolve_workspace_path(task.browse_node_id, task.workspace_path))
    meta = _load_analysis_meta(task.workspace_path)
    saved_session_id = meta.get("session_id") if isinstance(meta.get("session_id"), str) else None
    if saved_session_id and saved_session_id.strip():
        task.session_id = saved_session_id.strip()
    task.status = TaskStatus.PENDING
    task.error = None
    task.updated_at = datetime.utcnow().isoformat()
    tasks[task_id] = task
    _save_tasks(tasks)
    _sync_task_analysis_meta(task)
    _mark_browse_node_active(task.browse_node_id, task_id)

    _log_and_stream(task_id, "[SYSTEM] ♻️ 收到继续分析请求，将复用现有 workspace 进行断点续跑。")
    asyncio.create_task(_run_analysis(task_id, task))
    return task


@app.post("/api/tasks/{task_id}/refresh", response_model=Task, response_model_exclude={"workspace_path"})
async def refresh_task(task_id: str):
    """Incremental update: re-crawl category list for latest ranks, then only
    process new/changed ASINs and re-run analysts + summary.

    Requires the task to have a completed (or previously run) workspace with
    existing category data. Uses a different prompt to trigger the orchestrator's
    incremental update mode.
    """
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    if task.status == TaskStatus.RUNNING and task_id in _running_processes:
        raise HTTPException(status_code=409, detail="Task is already running")
    _assert_browse_node_not_running(task.browse_node_id, exclude_task_id=task_id)

    # Verify workspace has existing category data (prerequisite for incremental)
    task.workspace_path = str(_resolve_workspace_path(task.browse_node_id, task.workspace_path))
    ws = Path(task.workspace_path)
    cat_dir = ws / "categories" / task.browse_node_id
    if not cat_dir.exists() or not (cat_dir / "rankings.jsonl").exists():
        raise HTTPException(
            status_code=400,
            detail="该类目尚未完成过分析，无法增量更新。请先创建新的分析任务。",
        )

    meta = _load_analysis_meta(task.workspace_path)
    saved_session_id = meta.get("session_id") if isinstance(meta.get("session_id"), str) else None
    if saved_session_id and saved_session_id.strip():
        task.session_id = saved_session_id.strip()
    task.status = TaskStatus.PENDING
    task.error = None
    task.updated_at = datetime.utcnow().isoformat()
    tasks[task_id] = task
    _save_tasks(tasks)
    _sync_task_analysis_meta(task)
    _mark_browse_node_active(task.browse_node_id, task_id)

    refresh_prompt = _build_analysis_prompt(task, mode="refresh")
    _log_and_stream(task_id, "[SYSTEM] 🔄 收到增量更新请求，将重新爬取列表页获取最新排名，仅处理新增/变化 ASIN。")
    asyncio.create_task(_run_analysis(task_id, task, prompt_override=refresh_prompt))
    return task


@app.post("/api/tasks/{task_id}/reanalyze", response_model=Task, response_model_exclude={"workspace_path"})
async def reanalyze_task(task_id: str, request: Request):
    """Full re-analysis: wipe the workspace clean and start from scratch.

    Clears all crawled data, chunks, reports, and the Claude session so the
    next run is a completely fresh analysis — no residual state from the
    previous run.
    """
    user_id = _require_user(request)
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    # T2: 权限校验 — 只有任务创建者可以全量重新分析
    if task.owner_id and task.owner_id != user_id:
        raise HTTPException(status_code=403, detail="无权操作他人的任务")
    if task.status == TaskStatus.RUNNING and task_id in _running_processes:
        raise HTTPException(status_code=409, detail="Task is already running")
    _assert_browse_node_not_running(task.browse_node_id, exclude_task_id=task_id)

    task.workspace_path = str(_resolve_workspace_path(task.browse_node_id, task.workspace_path))
    ws = Path(task.workspace_path)

    # Wipe workspace contents (but keep the directory itself)
    if ws.exists():
        for child in ws.iterdir():
            if child.name == ANALYSIS_META_FILE:
                continue  # handled separately below
            if child.is_dir():
                try:
                    shutil.rmtree(child)
                except Exception as e:
                    LOGGER.error("Failed to remove workspace subdir '%s': %s", child, e)
                    _update_task(task_id, status=TaskStatus.FAILED, error=f"Workspace cleanup failed: {e}. Please manually delete the workspace directory and retry.")
                    return task
            else:
                try:
                    child.unlink()
                except Exception as e:
                    LOGGER.error("Failed to remove workspace file '%s': %s", child, e)

    # Clear session_id from both task record and workspace meta
    task.session_id = None
    _save_analysis_meta(task.workspace_path, {"session_id": ""})
    _delete_task_history(task_id)

    task.status = TaskStatus.PENDING
    task.error = None
    task.updated_at = datetime.utcnow().isoformat()
    tasks[task_id] = task
    _save_tasks(tasks)
    _sync_task_analysis_meta(task)
    _mark_browse_node_active(task.browse_node_id, task_id)

    _log_and_stream(task_id, "[SYSTEM] 🔥 收到全量重新分析请求，已清除所有历史数据，将从头开始全新分析。")
    asyncio.create_task(_run_analysis(task_id, task))
    return task


@app.get("/api/tasks", response_model=list[Task], response_model_exclude={"workspace_path"})
async def list_tasks(
    request: Request,
    all: bool = False,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
):
    """T15: 支持按状态和关键词筛选任务。

    - status: 按任务状态筛选（running/completed/failed/cancelled）
    - keyword: 按 browse_node_id 或 URL 关键词搜索
    - all: 是否显示公开任务
    """
    user_id = _require_user(request)
    tasks = _load_tasks()
    if all:
        filtered = [t for t in tasks.values() if t.owner_id == user_id or t.is_public]
    else:
        filtered = [t for t in tasks.values() if t.owner_id == user_id]
    if status:
        filtered = [t for t in filtered if t.status == status]
    if keyword:
        kw = keyword.lower()
        filtered = [t for t in filtered if kw in t.browse_node_id.lower() or kw in t.url.lower()]
    return sorted(filtered, key=lambda t: t.created_at, reverse=True)


@app.get("/api/tasks/{task_id}", response_model=Task, response_model_exclude={"workspace_path"})
async def get_task(task_id: str):
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]


@app.get("/api/tasks/{task_id}/progress")
async def task_progress(task_id: str):
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return EventSourceResponse(_progress_generator(task_id))


@app.get("/api/tasks/{task_id}/reports")
async def get_reports(task_id: str):
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    reports = _get_report_files(task.workspace_path)
    phases = _build_progress_from_workspace(task.workspace_path)
    return {
        "task_id": task_id,
        "browse_node_id": task.browse_node_id,
        "reports": reports,
        "phases": phases,
    }


_REPORT_FILE_MAP = {
    "summary": "summary.md",
    "marketplace": "reports/marketplace_dim.md",
    "reviews": "reports/reviews_dim.md",
    "aplus": "reports/aplus_dim.md",
    "fine_grained": "reports/fine_grained_dim.md",
}


@app.get("/api/tasks/{task_id}/download/{dim}")
async def download_report(task_id: str, dim: str):
    """Serve a dimension report as a downloadable .md file."""
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]

    rel = _REPORT_FILE_MAP.get(dim)
    if not rel:
        raise HTTPException(status_code=400, detail=f"Unknown dimension: {dim}")

    ws = Path(task.workspace_path)
    candidate = ws / rel

    # For dimension reports, filename might not match exactly (e.g., "marketplace_dim.md"
    # vs. "xxx_marketplace_dim.md"). Fall back to glob search.
    if not candidate.exists() and dim != "summary":
        reports_dir = ws / "reports"
        if reports_dir.exists():
            for md in reports_dir.glob("*_dim.md"):
                if dim in md.stem.lower():
                    candidate = md
                    break

    if not candidate.exists():
        raise HTTPException(status_code=404, detail=f"Report file not found: {rel}")

    download_name = f"{task.browse_node_id}_{dim}.md"
    return FileResponse(
        str(candidate),
        media_type="text/markdown",
        filename=download_name,
    )


@app.post("/api/tasks/{task_id}/chat")
async def chat_with_task(task_id: str, req: ChatRequest):
    """
    Proxy a follow-up question through claude CLI with conversation continuation.
    Streams the response back as SSE.
    """
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    _assert_browse_node_not_running(task.browse_node_id)

    # R12: prevent concurrent chat requests on the same task
    if task_id in _active_chat_tasks:
        raise HTTPException(status_code=409, detail="A chat request is already in progress for this task")
    _active_chat_tasks[task_id] = True

    # Inject workspace context into the prompt so Claude knows what we're discussing
    context = (
        f"[上下文：当前分析的 Amazon Bestsellers 类目 URL 为 {task.url}，"
        f"分析报告存储在 {task.workspace_path}]\n\n"
        f"{req.message}"
    )

    def _build_chat_cmd(session_id: Optional[str]) -> list[str]:
        cmd = ["claude"]
        if session_id:
            cmd.extend(["--resume", session_id])
        cmd.extend([
            "-p", context,
            "--plugin-dir", PLUGIN_DIR,
            "--agent", AGENT_ID,
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
        ])
        return cmd

    def _extract_text_chunks(raw_line: str) -> list[str]:
        """Pull user-visible assistant text out of a single stream-json line."""
        stripped = raw_line.strip()
        if not stripped:
            return []
        if not (stripped.startswith("{") and stripped.endswith("}")):
            return [raw_line]
        try:
            ev = json.loads(stripped)
        except json.JSONDecodeError:
            return []

        if ev.get("type") == "stream_event":
            event = ev.get("event") or {}
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    return [delta.get("text", "")]
            return []

        return []

    async def _stream() -> AsyncGenerator[str, None]:
        _chat_retried = False
        # Persist user message
        _save_chat_message(task_id, "user", req.message)
        _assistant_text_buf: list[str] = []
        try:
            while True:
                cmd = _build_chat_cmd(task.session_id)
                try:
                    proc = _spawn_process(cmd)
                    _session_not_found = False
                    if proc.stdout is not None:
                        while True:
                            raw = await asyncio.to_thread(proc.stdout.readline)
                            if raw == "":
                                if proc.poll() is not None:
                                    break
                                await asyncio.sleep(0.05)
                                continue
                            # Detect stale session early, before streaming any content
                            if "No conversation found with session ID" in raw and task.session_id and not _chat_retried:
                                _session_not_found = True
                                break
                            session_id = _extract_stream_session_id(raw)
                            if session_id:
                                _persist_task_session_id(task_id, session_id)
                            for chunk in _extract_text_chunks(raw):
                                if chunk:
                                    _assistant_text_buf.append(chunk)
                                    yield f"data: {json.dumps({'text': chunk})}\n\n"
                    # ── Stale session fallback ──
                    if _session_not_found and task.session_id and not _chat_retried:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        task.session_id = None
                        _update_task(task_id, session_id=None)
                        _save_analysis_meta(task.workspace_path, {"session_id": ""})
                        _chat_retried = True
                        continue  # retry without --resume
                    await _wait_process(proc)
                    # Persist assistant response
                    if _assistant_text_buf:
                        _save_chat_message(task_id, "assistant", "".join(_assistant_text_buf))
                    yield f"data: {json.dumps({'done': True})}\n\n"
                except FileNotFoundError:
                    yield f"data: {json.dumps({'error': 'claude CLI not found'})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
                break  # done (retry uses continue)
        finally:
            _active_chat_tasks.pop(task_id, None)

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, request: Request):
    user_id = _require_user(request)
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    # T2: 权限校验 — 只有任务创建者可以删除
    if task.owner_id and task.owner_id != user_id:
        raise HTTPException(status_code=403, detail="无权删除他人的任务")
    # Kill running process if any
    proc = _running_processes.pop(task_id, None)
    if proc:
        try:
            proc.kill()
        except Exception:
            pass
    _clear_browse_node_active(task.browse_node_id, task_id)
    _delete_task_history(task_id)
    _delete_sessions_for_task(task_id)
    # Clear session_id from workspace meta so future tasks for the same
    # browse_node_id don't try to resume a stale session.
    _save_analysis_meta(task.workspace_path, {"session_id": ""})
    # 从 SQLite 删除任务记录
    try:
        _db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        _db.commit()
    except Exception:
        LOGGER.error("Failed to delete task '%s' from SQLite", task_id, exc_info=True)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/cancel", response_model=Task, response_model_exclude={"workspace_path"})
async def cancel_task(task_id: str, request: Request):
    """取消运行中的任务。保留 workspace 中间数据。"""
    user_id = _require_user(request)
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    # 权限校验
    if task.owner_id and task.owner_id != user_id:
        raise HTTPException(status_code=403, detail="无权取消他人的任务")
    if task.status not in (TaskStatus.RUNNING, TaskStatus.PENDING):
        raise HTTPException(status_code=409, detail=f"任务状态为 {task.status}，无法取消")
    # 终止子进程
    proc = _running_processes.pop(task_id, None)
    if proc:
        try:
            proc.terminate()
            # 等待 5 秒，如果还没退出就 kill
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            LOGGER.warning("终止子进程失败: task_id=%s", task_id, exc_info=True)
    _clear_browse_node_active(task.browse_node_id, task_id)
    _update_task(task_id, status=TaskStatus.CANCELLED, error="用户取消")
    _log_and_stream(task_id, "[SYSTEM] ❌ 任务已被用户取消")
    return _load_tasks()[task_id]


@app.get("/api/tasks/{task_id}/history")
async def get_task_history(task_id: str):
    """Return persisted conversation history (stream items + chat messages).

    Used by the frontend to restore conversation when switching tasks or
    after a page refresh — no need to keep SSE open for completed tasks.
    """
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    stream_items, stream_order = _load_stream_history(task_id)
    chat_messages = _load_chat_history(task_id)

    return {
        "task_id": task_id,
        "stream_items": stream_items,
        "stream_order": stream_order,
        "chat_messages": chat_messages,
    }


# ── 健康检查缓存 ──────────────────────────────────────────────────────────────
_health_cache: dict = {}
_health_cache_ts: float = 0.0
_HEALTH_CACHE_TTL = 10  # 秒
_claude_available_cache: dict = {}
_claude_cache_ts: float = 0.0
_CLAUDE_CACHE_TTL = 300  # 5 分钟


def _check_database() -> str:
    """检测 SQLite 数据库连接是否正常。"""
    try:
        _db.execute("SELECT 1")
        return "ok"
    except Exception as e:
        LOGGER.error("Health check: database error: %s", e)
        return "error"


def _check_disk() -> str:
    """检测磁盘空间。"""
    try:
        import psutil
        usage = psutil.disk_usage(str(APP_DIR))
        pct = usage.percent
        if pct >= 95:
            return "critical"
        if pct >= 85:
            return "warning"
        return "ok"
    except ImportError:
        # psutil 未安装时跳过磁盘检测
        return "unknown"
    except Exception as e:
        LOGGER.error("Health check: disk error: %s", e)
        return "error"


def _check_claude() -> bool:
    """检测 Claude CLI 是否可用（结果缓存 5 分钟）。"""
    global _claude_available_cache, _claude_cache_ts
    now = datetime.utcnow().timestamp()
    if _claude_cache_ts and (now - _claude_cache_ts) < _CLAUDE_CACHE_TTL:
        return _claude_available_cache.get("available", False)
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        available = result.returncode == 0
    except Exception:
        available = False
    _claude_available_cache = {"available": available}
    _claude_cache_ts = now
    return available


@app.get("/api/health")
async def health():
    """系统健康检查接口，结果缓存 10 秒。"""
    global _health_cache, _health_cache_ts
    now = datetime.utcnow().timestamp()
    if _health_cache_ts and (now - _health_cache_ts) < _HEALTH_CACHE_TTL:
        return _health_cache

    db_status = _check_database()
    disk_status = _check_disk()
    claude_ok = _check_claude()
    active_count = len(_running_processes)

    # 综合状态判定
    if db_status == "error" or disk_status == "critical":
        overall = "unhealthy"
    elif disk_status == "warning" or not claude_ok:
        overall = "degraded"
    else:
        overall = "healthy"

    result = {
        "status": overall,
        "components": {
            "database": db_status,
            "disk_space": disk_status,
            "claude_available": claude_ok,
            "active_tasks": active_count,
        },
        "version": "1.0.0",
        "workspace_base": str(WORKSPACE_BASE),
    }
    _health_cache = result
    _health_cache_ts = now
    return result
