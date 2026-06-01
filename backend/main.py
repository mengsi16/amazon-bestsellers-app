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
import hashlib
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
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

import aiofiles
import bcrypt
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse
from streaming import StreamManager, extract_stream_session_id

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
DEFAULT_SONNET_MODEL = "claude-3-5-sonnet-20241022"
DEFAULT_OPUS_MODEL = "claude-opus-4-7"
DEFAULT_MODEL_FAMILY = "sonnet"

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
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
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
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "email" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    if "email_verified" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
    if "display_name" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
    if "avatar_url" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT NOT NULL DEFAULT ''")
    if "updated_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
    if "last_login_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "UPDATE users SET email = lower(username), email_verified = 1 "
        "WHERE (email IS NULL OR email = '') AND instr(username, '@') > 1"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
        "ON users(email) WHERE email <> ''"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_identities (
            id               TEXT PRIMARY KEY,
            user_id          TEXT NOT NULL,
            provider         TEXT NOT NULL,
            provider_user_id TEXT NOT NULL,
            provider_email   TEXT NOT NULL DEFAULT '',
            created_at       TEXT NOT NULL DEFAULT '',
            last_login_at    TEXT NOT NULL DEFAULT '',
            UNIQUE(provider, provider_user_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_identities_user ON auth_identities(user_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_verification_codes (
            id          TEXT PRIMARY KEY,
            email       TEXT NOT NULL,
            purpose     TEXT NOT NULL,
            code_hash   TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            consumed_at TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_codes_email_purpose "
        "ON email_verification_codes(email, purpose, created_at)"
    )
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
    model_config_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(model_configs)").fetchall()
    }
    if "sonnet_model" not in model_config_columns:
        conn.execute(
            "ALTER TABLE model_configs "
            "ADD COLUMN sonnet_model TEXT NOT NULL DEFAULT 'claude-3-5-sonnet-20241022'"
        )
    if "opus_model" not in model_config_columns:
        conn.execute("ALTER TABLE model_configs ADD COLUMN opus_model TEXT NOT NULL DEFAULT 'opus'")
    if "default_model_family" not in model_config_columns:
        conn.execute(
            "ALTER TABLE model_configs "
            "ADD COLUMN default_model_family TEXT NOT NULL DEFAULT 'sonnet'"
        )
    conn.execute(
        "UPDATE model_configs SET sonnet_model = model "
        "WHERE sonnet_model IS NULL OR sonnet_model = '' OR sonnet_model = 'claude-3-5-sonnet-20241022'"
    )
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
    conn.execute("""
        DELETE FROM users
        WHERE instr(username, '@') = 0
          AND id NOT IN (SELECT owner_id FROM tasks WHERE owner_id IS NOT NULL AND owner_id <> '')
          AND id NOT IN (SELECT user_id FROM model_configs WHERE user_id IS NOT NULL AND user_id <> '')
          AND id NOT IN (SELECT user_id FROM credits_log WHERE user_id IS NOT NULL AND user_id <> '')
    """)
    conn.commit()
    return conn


_db = _init_db()
_stream_manager = StreamManager(
    _db,
    LOGGER,
    persist_session_id=lambda task_id, session_id: _persist_task_session_id(task_id, session_id),
    record_credits=lambda task_id, ev: _record_credits_from_result(task_id, ev),
)


def _stream() -> StreamManager:
    _stream_manager.db = _db
    return _stream_manager

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
    except Exception as exc:
        LOGGER.error(
            "API key 加密失败，exception_type=%s，api_key_len=%d",
            type(exc).__name__,
            len(api_key),
            exc_info=True,
        )
        raise


def _decrypt_api_key(encrypted: str) -> str:
    """解密 API key。失败时返回空字符串而非抛异常——避免 create_task 因 key 解密失败返回纯文本 500。"""
    try:
        f = _get_fernet()
        return f.decrypt(encrypted.encode()).decode()
    except Exception as exc:
        LOGGER.error(
            "API key 解密失败，exception_type=%s，encrypted_len=%d — 返回空字符串降级",
            type(exc).__name__,
            len(encrypted),
            exc_info=True,
        )
        return ""


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
    "/api/auth/email/send-code",
    "/api/auth/email/register",
    "/api/auth/email/login",
    "/api/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}
_OAUTH_PROVIDERS = {"google", "github"}
_OAUTH_CONFIG = {
    "google": {
        "client_id_env": "GOOGLE_OAUTH_CLIENT_ID",
        "client_secret_env": "GOOGLE_OAUTH_CLIENT_SECRET",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scope": "openid email profile",
    },
    "github": {
        "client_id_env": "GITHUB_OAUTH_CLIENT_ID",
        "client_secret_env": "GITHUB_OAUTH_CLIENT_SECRET",
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "scope": "read:user user:email",
    },
}


def _is_auth_exempt_path(path: str) -> bool:
    return path in _AUTH_EXEMPT_PATHS or path.startswith("/api/auth/oauth/")


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def _create_jwt(user_id: str, username: str) -> str:
    from datetime import timedelta
    expire = datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)
    payload = {"sub": user_id, "username": username, "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY_CURRENT, algorithm=JWT_ALGORITHM)


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="请输入有效的邮箱地址")
    return email


def _normalize_code_purpose(value: str) -> str:
    purpose = value.strip().lower()
    if purpose not in {"register", "login"}:
        raise HTTPException(status_code=400, detail="验证码用途无效")
    return purpose


def _hash_verification_code(email: str, purpose: str, code: str) -> str:
    raw = f"{email}:{purpose}:{code}:{JWT_SECRET_KEY_CURRENT}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _create_email_verification_code(email: str, purpose: str) -> dict:
    from datetime import timedelta

    code = "123456"
    now = datetime.utcnow()
    expires_at = (now + timedelta(minutes=10)).isoformat()
    _db.execute(
        "UPDATE email_verification_codes SET consumed_at = ? "
        "WHERE email = ? AND purpose = ? AND consumed_at = ''",
        (now.isoformat(), email, purpose),
    )
    _db.execute(
        "INSERT INTO email_verification_codes "
        "(id, email, purpose, code_hash, expires_at, consumed_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, '', ?)",
        (
            str(uuid.uuid4())[:12],
            email,
            purpose,
            _hash_verification_code(email, purpose, code),
            expires_at,
            now.isoformat(),
        ),
    )
    _db.commit()
    payload = {"ok": True, "expires_at": expires_at}
    if email.endswith("@xxx.com") or os.environ.get("ENV") != "production":
        payload["dev_code"] = code
    return payload


def _consume_email_verification_code(email: str, purpose: str, code: str) -> None:
    now = datetime.utcnow()
    row = _db.execute(
        "SELECT id, code_hash, expires_at FROM email_verification_codes "
        "WHERE email = ? AND purpose = ? AND consumed_at = '' "
        "ORDER BY created_at DESC LIMIT 1",
        (email, purpose),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="验证码无效或已过期")
    expires_at = datetime.fromisoformat(row[2])
    if expires_at < now:
        raise HTTPException(status_code=400, detail="验证码无效或已过期")
    if row[1] != _hash_verification_code(email, purpose, code.strip()):
        raise HTTPException(status_code=400, detail="验证码错误")
    _db.execute(
        "UPDATE email_verification_codes SET consumed_at = ? WHERE id = ?",
        (now.isoformat(), row[0]),
    )
    _db.commit()


def _ensure_email_identity(user_id: str, email: str) -> None:
    now = datetime.utcnow().isoformat()
    _db.execute(
        "INSERT OR IGNORE INTO auth_identities "
        "(id, user_id, provider, provider_user_id, provider_email, created_at, last_login_at) "
        "VALUES (?, ?, 'email', ?, ?, ?, ?)",
        (str(uuid.uuid4())[:12], user_id, email, email, now, now),
    )
    _db.execute(
        "UPDATE auth_identities SET last_login_at = ? "
        "WHERE provider = 'email' AND provider_user_id = ?",
        (now, email),
    )
    _db.commit()


def _auth_response(user_id: str, email: str, created_at: str = "") -> dict:
    token = _create_jwt(user_id, email)
    return {
        "user_id": user_id,
        "username": email,
        "email": email,
        "email_verified": True,
        "created_at": created_at,
        "token": token,
    }


def _normalize_oauth_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in _OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail="OAuth provider not supported")
    return normalized


def _oauth_client_config(provider: str) -> tuple[str, str]:
    config = _OAUTH_CONFIG[provider]
    client_id = os.environ.get(config["client_id_env"], "").strip()
    client_secret = os.environ.get(config["client_secret_env"], "").strip()
    if not client_id or not client_secret:
        label = "Google" if provider == "google" else "GitHub"
        raise HTTPException(status_code=503, detail=f"{label} OAuth 未配置，请设置 client id 和 client secret")
    return client_id, client_secret


def _oauth_redirect_uri(request: Request, provider: str) -> str:
    base = os.environ.get("OAUTH_REDIRECT_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/api/auth/oauth/{provider}/callback"
    return str(request.url_for("oauth_callback", provider=provider))


def _create_oauth_state(provider: str) -> str:
    from datetime import timedelta

    expire = datetime.utcnow() + timedelta(minutes=10)
    payload = {
        "provider": provider,
        "nonce": secrets.token_urlsafe(16),
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET_KEY_CURRENT, algorithm=JWT_ALGORITHM)


def _verify_oauth_state(provider: str, state: str) -> None:
    payload = _decode_jwt(state)
    if payload is None or payload.get("provider") != provider:
        raise HTTPException(status_code=400, detail="OAuth state 无效或已过期")


def _oauth_authorization_url(provider: str, request: Request) -> str:
    client_id, _client_secret = _oauth_client_config(provider)
    config = _OAUTH_CONFIG[provider]
    params = {
        "client_id": client_id,
        "redirect_uri": _oauth_redirect_uri(request, provider),
        "response_type": "code",
        "scope": config["scope"],
        "state": _create_oauth_state(provider),
    }
    if provider == "google":
        params["access_type"] = "online"
        params["prompt"] = "select_account"
    return f"{config['authorize_url']}?{urlencode(params)}"


def _http_post_form_json(url: str, data: dict[str, str], headers: dict[str, str] | None = None) -> dict:
    request = UrlRequest(
        url,
        data=urlencode(data).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            **(headers or {}),
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_get_json(url: str, access_token: str) -> dict | list:
    request = UrlRequest(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        method="GET",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _exchange_oauth_code(provider: str, code: str, redirect_uri: str) -> dict:
    client_id, client_secret = _oauth_client_config(provider)
    config = _OAUTH_CONFIG[provider]
    return _http_post_form_json(config["token_url"], {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })


def _fetch_oauth_profile(provider: str, access_token: str) -> dict:
    if provider == "google":
        profile = _http_get_json("https://openidconnect.googleapis.com/v1/userinfo", access_token)
        if not isinstance(profile, dict):
            raise HTTPException(status_code=502, detail="Google 用户信息格式异常")
        return {
            "provider_user_id": str(profile.get("sub", "")),
            "email": str(profile.get("email", "")),
            "email_verified": bool(profile.get("email_verified")),
            "display_name": str(profile.get("name", "")),
            "avatar_url": str(profile.get("picture", "")),
        }

    user = _http_get_json("https://api.github.com/user", access_token)
    if not isinstance(user, dict):
        raise HTTPException(status_code=502, detail="GitHub 用户信息格式异常")
    email = str(user.get("email") or "")
    email_verified = bool(email)
    if not email:
        emails = _http_get_json("https://api.github.com/user/emails", access_token)
        if not isinstance(emails, list):
            raise HTTPException(status_code=502, detail="GitHub 邮箱信息格式异常")
        primary = next(
            (
                item for item in emails
                if isinstance(item, dict) and item.get("primary") and item.get("verified") and item.get("email")
            ),
            None,
        )
        if primary:
            email = str(primary["email"])
            email_verified = True
    return {
        "provider_user_id": str(user.get("id", "")),
        "email": email,
        "email_verified": email_verified,
        "display_name": str(user.get("name") or user.get("login") or ""),
        "avatar_url": str(user.get("avatar_url") or ""),
    }


def _login_oauth_user(provider: str, profile: dict) -> dict:
    provider_user_id = str(profile.get("provider_user_id", "")).strip()
    email = _normalize_email(str(profile.get("email", "")))
    if not provider_user_id:
        raise HTTPException(status_code=400, detail="OAuth 用户 ID 缺失")
    if not bool(profile.get("email_verified")):
        raise HTTPException(status_code=400, detail="OAuth 邮箱未验证")

    now = datetime.utcnow().isoformat()
    identity = _db.execute(
        "SELECT user_id FROM auth_identities WHERE provider = ? AND provider_user_id = ?",
        (provider, provider_user_id),
    ).fetchone()
    user_row = None
    if identity is not None:
        user_row = _db.execute(
            "SELECT id, created_at FROM users WHERE id = ?",
            (identity[0],),
        ).fetchone()
    if user_row is None:
        user_row = _db.execute(
            "SELECT id, created_at FROM users WHERE email = ? OR username = ?",
            (email, email),
        ).fetchone()
    if user_row is None:
        user_id = str(uuid.uuid4())[:12]
        _db.execute(
            "INSERT INTO users "
            "(id, username, email, email_verified, password_hash, display_name, avatar_url, created_at, updated_at, last_login_at) "
            "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                email,
                email,
                _hash_password(secrets.token_urlsafe(32)),
                str(profile.get("display_name") or email),
                str(profile.get("avatar_url") or ""),
                now,
                now,
                now,
            ),
        )
        created_at = now
    else:
        user_id = user_row[0]
        created_at = user_row[1]
        _db.execute(
            "UPDATE users SET email = ?, email_verified = 1, display_name = ?, avatar_url = ?, updated_at = ?, last_login_at = ? "
            "WHERE id = ?",
            (
                email,
                str(profile.get("display_name") or email),
                str(profile.get("avatar_url") or ""),
                now,
                now,
                user_id,
            ),
        )

    _db.execute(
        "INSERT OR IGNORE INTO auth_identities "
        "(id, user_id, provider, provider_user_id, provider_email, created_at, last_login_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4())[:12], user_id, provider, provider_user_id, email, now, now),
    )
    _db.execute(
        "UPDATE auth_identities SET provider_email = ?, last_login_at = ? "
        "WHERE provider = ? AND provider_user_id = ?",
        (email, now, provider, provider_user_id),
    )
    _db.commit()
    return _auth_response(user_id, email, created_at)


def _oauth_success_html(auth: dict) -> str:
    payload = json.dumps(auth, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>登录成功</title></head>
<body>
<script>
const auth = {payload};
localStorage.setItem('token', auth.token);
location.replace('/');
</script>
</body>
</html>"""


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


class EmailCodeRequest(BaseModel):
    email: str
    purpose: str = "register"


class EmailRegisterRequest(BaseModel):
    email: str
    password: str
    code: str


class EmailLoginRequest(BaseModel):
    email: str
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
        return _stream().load_history(task_id)
    except Exception:
        LOGGER.error("Failed to load stream history for task '%s'", task_id, exc_info=True)
        return [], []


def _delete_task_history(task_id: str) -> None:
    """Delete all SQLite records for a task."""
    try:
        _stream().delete_history(task_id)
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
    model_family: Optional[str] = None


class ChatRequest(BaseModel):
    message: str


# ── Model Configs API ──────────────────────────────────────────────────────────

class ModelConfigCreate(BaseModel):
    name: str
    model: Optional[str] = None
    sonnet_model: Optional[str] = None
    opus_model: Optional[str] = None
    default_model_family: Optional[str] = None
    api_key: str  # 明文传入，内部加密存储
    base_url: Optional[str] = None
    is_default: bool = False


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    sonnet_model: Optional[str] = None
    opus_model: Optional[str] = None
    default_model_family: Optional[str] = None
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

# task_id -> task context (owner_id, model, etc.) 用于 credits 记录
_task_context: dict[str, dict] = {}

# task_id -> credits recorded flag (prevent double recording)
_task_credits_recorded: dict[str, bool] = {}


# ── 优雅停机 ─────────────────────────────────────────────────────────────────

def _cleanup_on_shutdown():
    """进程退出时清理所有运行中的子进程。"""
    if not _running_processes:
        return
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


# ── Credits 记录 ──────────────────────────────────────────────────────────────

def _record_credits_from_result(task_id: str, ev: dict) -> None:
    """从 stream-json result 事件中提取 usage 信息并写入 credits_log。"""
    if _task_credits_recorded.get(task_id):
        return  # 防止重复记录

    ctx = _task_context.get(task_id, {})
    owner_id = ctx.get("owner_id")
    if not owner_id:
        return

    usage = ev.get("usage", {})
    cache_hit = usage.get("cache_hit_input", 0)
    cache_miss = usage.get("cache_miss_input", 0)
    output_tokens = usage.get("output_tokens", 0)
    total_cost = ev.get("total_cost_usd", 0.0)
    model = ctx.get("model", "")

    # 如果 usage 中没有 cache 字段，全部计入 cache_miss_input
    if not usage.get("cache_hit_input") and not usage.get("cache_miss_input"):
        total_input = usage.get("input_tokens", 0)
        if total_input > 0:
            cache_miss = total_input
            cache_hit = 0

    if cache_hit == 0 and cache_miss == 0 and output_tokens == 0:
        return  # 无使用量，跳过

    now = datetime.utcnow().isoformat()
    try:
        _db.execute(
            """INSERT INTO credits_log (task_id, user_id, cache_hit_input, cache_miss_input, output_tokens, cost_usd, model, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, owner_id, cache_hit, cache_miss, output_tokens, total_cost, model or "", now),
        )
        _db.commit()
        _task_credits_recorded[task_id] = True
        LOGGER.info("记录 credits: task_id=%s, cache_hit=%d, cache_miss=%d, output=%d, cost=%.4f",
                    task_id, cache_hit, cache_miss, output_tokens, total_cost)
    except Exception:
        LOGGER.error("Failed to record credits for task '%s'", task_id, exc_info=True)


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
    """写 analysis_meta.json，失败仅 log 不抛异常——元数据写入不应阻塞任务创建。"""
    try:
        workspace = Path(workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        meta_path = _analysis_meta_path(workspace)
        current = _load_analysis_meta(workspace)
        merged = {**current, **patch}
        meta_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        LOGGER.error(
            "保存 analysis_meta 失败，workspace=%s，exception_type=%s",
            workspace_path, type(exc).__name__,
            exc_info=True,
        )


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
    return extract_stream_session_id(raw_line)


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


def _is_task_execution_active(task: Task) -> bool:
    return (
        _active_browse_node_tasks.get(task.browse_node_id) == task.id
        or task.id in _running_processes
    )


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

    if _is_task_execution_active(task):
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.status = TaskStatus.RUNNING
        task.error = None
        return task

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


def _append_log(task_id: str, line: str) -> None:
    _stream().append_log(task_id, line)


def _log_and_stream(task_id: str, line: str) -> None:
    _stream().log_and_stream(task_id, line)


# ── Stream items (structured conversational log) ───────────────────────────────


def _feed_stream_line(task_id: str, raw_line: str) -> None:
    _stream().feed_line(task_id, raw_line)


def _reset_task_stream(task_id: str) -> None:
    _stream().reset_task_stream(task_id)

def _spawn_process(cmd: list[str], env_extra: Optional[dict] = None) -> subprocess.Popen:
    """启动子进程，可选注入额外环境变量。"""
    return subprocess.Popen(
        cmd,
        cwd=str(SUBPROCESS_CWD),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=(dict(os.environ, **env_extra) if env_extra else None),
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


def _normalize_model_family(model_family: Optional[str]) -> str:
    family = (model_family or DEFAULT_MODEL_FAMILY).strip().lower()
    if family not in {"sonnet", "opus"}:
        raise HTTPException(status_code=400, detail="model_family 只能是 sonnet 或 opus")
    return family


def _resolve_model_config_fields(
    model: Optional[str],
    sonnet_model: Optional[str],
    opus_model: Optional[str],
    default_model_family: Optional[str],
) -> dict:
    resolved_sonnet = (sonnet_model or model or DEFAULT_SONNET_MODEL).strip()
    resolved_opus = (opus_model or DEFAULT_OPUS_MODEL).strip()
    resolved_family = _normalize_model_family(default_model_family)
    return {
        "model": resolved_sonnet,
        "sonnet_model": resolved_sonnet,
        "opus_model": resolved_opus,
        "default_model_family": resolved_family,
    }


def _model_config_response(row) -> dict:
    return {
        "id": row[0],
        "name": row[1],
        "model": row[3] or row[2] or DEFAULT_SONNET_MODEL,
        "sonnet_model": row[3] or row[2] or DEFAULT_SONNET_MODEL,
        "opus_model": row[4] or DEFAULT_OPUS_MODEL,
        "default_model_family": row[5] or DEFAULT_MODEL_FAMILY,
        "base_url": row[6] or "",
        "has_api_key": bool(row[7]),
        "is_default": bool(row[8]),
        "created_at": row[9],
    }


def _load_default_model_config(user_id: Optional[str]) -> Optional[dict]:
    if not user_id:
        return None
    row = _db.execute(
        """SELECT id, name, model, sonnet_model, opus_model, default_model_family,
                  base_url, api_key_encrypted, is_default, created_at
           FROM model_configs
           WHERE user_id = ? AND is_default = 1
           ORDER BY created_at DESC
           LIMIT 1""",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "model": row[3] or row[2] or DEFAULT_SONNET_MODEL,
        "sonnet_model": row[3] or row[2] or DEFAULT_SONNET_MODEL,
        "opus_model": row[4] or DEFAULT_OPUS_MODEL,
        "default_model_family": row[5] or DEFAULT_MODEL_FAMILY,
        "base_url": row[6] or "",
        "api_key": _decrypt_api_key(row[7]) if row[7] else "",
        "is_default": bool(row[8]),
        "created_at": row[9],
    }


def _select_config_model(config: Optional[dict], model_family: Optional[str]) -> str:
    if config is None:
        return ""
    family = _normalize_model_family(model_family or config["default_model_family"])
    if family == "opus":
        return config["opus_model"]
    return config["sonnet_model"]


def _build_claude_cli_config(
    user_id: Optional[str],
    explicit_model: Optional[str] = None,
    model_family: Optional[str] = None,
) -> dict:
    default_config = _load_default_model_config(user_id)
    model = (explicit_model or "").strip()
    if not model:
        model = _select_config_model(default_config, model_family)
    env_to_set = {}
    if default_config:
        if default_config["api_key"]:
            env_to_set["ANTHROPIC_API_KEY"] = default_config["api_key"]
        if default_config["base_url"]:
            env_to_set["ANTHROPIC_BASE_URL"] = default_config["base_url"]
    return {"model": model, "env": env_to_set, "default_config": default_config}


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


def _initialize_analysis_stream(
    task_id: str,
    task: Task,
    startup_system_lines: Optional[list[str]] = None,
) -> None:
    _reset_task_stream(task_id)
    _log_and_stream(task_id, f"[SYSTEM] 启动分析任务: {task.url}")
    _log_and_stream(task_id, f"[SYSTEM] Workspace: {task.workspace_path}")
    for line in startup_system_lines or []:
        _log_and_stream(task_id, line)


async def _run_analysis(
    task_id: str,
    task: Task,
    prompt_override: Optional[str] = None,
    startup_system_lines: Optional[list[str]] = None,
):
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
        startup_system_lines: 新执行轮次初始化后写入的系统提示，避免被 stream reset 清掉。
    """
    prompt = prompt_override or _build_analysis_prompt(task)

    _initialize_analysis_stream(task_id, task, startup_system_lines)

    _retried = False
    _skip_cleanup = False

    try:
        cli_config = _build_claude_cli_config(task.owner_id, explicit_model=task.model)

        # 设置任务上下文，用于 credits 记录
        _task_context[task_id] = {
            "owner_id": task.owner_id,
            "model": cli_config["model"] or task.model,
        }
        _task_credits_recorded[task_id] = False

        while True:
            _skip_cleanup = False
            cmd = ["claude"]
            if task.session_id:
                cmd.extend(["--resume", task.session_id])

            if cli_config["model"]:
                cmd.extend(["--model", cli_config["model"]])

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
            proc = _spawn_process(cmd, env_extra=cli_config["env"] or None)
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
                except Exception as exc:
                    LOGGER.error(
                        "分析超时后终止进程失败，task_id=%s，exception_type=%s",
                        task_id,
                        type(exc).__name__,
                        exc_info=True,
                    )
                _running_processes.pop(task_id, None)
                _update_task(task_id, status=TaskStatus.FAILED, error=f"分析超时（{_ANALYSIS_TIMEOUT // 3600} 小时）")
                _clear_browse_node_active(task.browse_node_id, task_id)
                return
            _running_processes.pop(task_id, None)

            # ── Stale session fallback ──────────────────────────────────
            if proc.returncode != 0 and task.session_id and not _retried:
                logs = _stream().logs(task_id)
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
                # 尝试从日志中提取真实错误原因
                logs = _stream().logs(task_id)
                error_reason = None
                for line in logs:
                    line_lower = line.lower()
                    if any(kw in line_lower for kw in ("error", "exception", "traceback", "failed", "cannot", "unable", "invalid")):
                        # 取第一个包含错误关键词的行
                        error_reason = line.strip()
                        if len(error_reason) > 10:
                            break
                if error_reason and len(error_reason) < 200:
                    err = f"Exit code {proc.returncode}: {error_reason}"
                else:
                    err = f"Exit code {proc.returncode}"
                _update_task(task_id, status=TaskStatus.FAILED, error=err)
                _log_and_stream(task_id, f"[SYSTEM] ❌ 分析失败，退出码: {proc.returncode}")

            break  # normal exit (retry uses continue)

    except FileNotFoundError as exc:
        LOGGER.error(
            "claude CLI 不存在，task_id=%s，exception_type=%s",
            task_id,
            type(exc).__name__,
            exc_info=True,
        )
        msg = "'claude' 命令未找到，请先安装 Claude Code CLI"
        _update_task(task_id, status=TaskStatus.FAILED, error=msg)
        _log_and_stream(task_id, f"[SYSTEM] ❌ {msg}")
    except Exception as e:
        LOGGER.error(
            "分析任务失败，task_id=%s，exception_type=%s",
            task_id,
            type(e).__name__,
            exc_info=True,
        )
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
        logs = _stream().logs(task_id)
        while sent_log_idx < len(logs):
            line = logs[sent_log_idx]
            yield {
                "event": "log",
                "data": json.dumps({"line": line, "index": sent_log_idx}),
            }
            sent_log_idx += 1

        # Structured stream items (diff only)
        payloads = _stream().flush_items(task_id, last_item_versions)
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
            logs = _stream().logs(task_id)
            while sent_log_idx < len(logs):
                line = logs[sent_log_idx]
                yield {
                    "event": "log",
                    "data": json.dumps({"line": line, "index": sent_log_idx}),
                }
                sent_log_idx += 1
            final_payloads = _stream().flush_items(task_id, last_item_versions)
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


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器：确保 500 永远返回 JSON 而非纯文本 'Internal Server Error'。"""
    LOGGER.error("未捕获的异常，path=%s，exception_type=%s", request.url.path, type(exc).__name__, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


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
    if path.startswith("/api") and not _is_auth_exempt_path(path):
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

@app.post("/api/auth/email/send-code")
async def send_email_code(req: EmailCodeRequest):
    email = _normalize_email(req.email)
    purpose = _normalize_code_purpose(req.purpose)
    return _create_email_verification_code(email, purpose)


@app.post("/api/auth/email/register")
async def email_register(req: EmailRegisterRequest):
    email = _normalize_email(req.email)
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少 6 位")
    existing = _db.execute(
        "SELECT id FROM users WHERE email = ? OR username = ?",
        (email, email),
    ).fetchone()
    if existing is not None:
        raise HTTPException(status_code=409, detail="邮箱已注册")
    _consume_email_verification_code(email, "register", req.code)
    user_id = str(uuid.uuid4())[:12]
    now = datetime.utcnow().isoformat()
    password_hash = _hash_password(req.password)
    _db.execute(
        "INSERT INTO users "
        "(id, username, email, email_verified, password_hash, display_name, created_at, updated_at, last_login_at) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)",
        (user_id, email, email, password_hash, email, now, now, now),
    )
    _db.commit()
    _ensure_email_identity(user_id, email)
    return _auth_response(user_id, email, now)


def _login_email(email: str, password: str) -> dict:
    row = _db.execute(
        "SELECT id, username, email, email_verified, password_hash, created_at "
        "FROM users WHERE email = ? OR username = ?",
        (email, email),
    ).fetchone()
    if row is None or not _verify_password(password, row[4]):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    if not bool(row[3]):
        raise HTTPException(status_code=403, detail="邮箱尚未验证")
    now = datetime.utcnow().isoformat()
    _db.execute(
        "UPDATE users SET last_login_at = ?, email = ?, email_verified = 1 WHERE id = ?",
        (now, email, row[0]),
    )
    _db.commit()
    _ensure_email_identity(row[0], email)
    return _auth_response(row[0], email, row[5])


@app.post("/api/auth/email/login")
@limiter.limit("5/minute")
async def email_login(req: EmailLoginRequest, request: Request):
    email = _normalize_email(req.email)
    return _login_email(email, req.password)


@app.get("/api/auth/oauth/{provider}/start-url")
async def oauth_start_url(provider: str, request: Request):
    normalized_provider = _normalize_oauth_provider(provider)
    return {"authorization_url": _oauth_authorization_url(normalized_provider, request)}


@app.get("/api/auth/oauth/{provider}/start")
async def oauth_start(provider: str, request: Request):
    normalized_provider = _normalize_oauth_provider(provider)
    return RedirectResponse(_oauth_authorization_url(normalized_provider, request))


@app.get("/api/auth/oauth/{provider}/callback")
async def oauth_callback(provider: str, request: Request, code: str, state: str):
    normalized_provider = _normalize_oauth_provider(provider)
    _verify_oauth_state(normalized_provider, state)
    token_data = _exchange_oauth_code(
        normalized_provider,
        code,
        _oauth_redirect_uri(request, normalized_provider),
    )
    access_token = str(token_data.get("access_token") or "")
    if not access_token:
        raise HTTPException(status_code=400, detail="OAuth token 响应缺少 access_token")
    profile = _fetch_oauth_profile(normalized_provider, access_token)
    auth = _login_oauth_user(normalized_provider, profile)
    return HTMLResponse(_oauth_success_html(auth))


@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    _normalize_email(req.username)
    raise HTTPException(status_code=400, detail="邮箱注册需要验证码，请先获取验证码")


@app.post("/api/auth/login")
@limiter.limit("5/minute")
async def login(req: LoginRequest, request: Request):
    email = _normalize_email(req.username)
    return _login_email(email, req.password)


@app.get("/api/auth/me")
async def auth_me(request: Request):
    user_id = _require_user(request)
    row = _db.execute(
        "SELECT id, username, email, email_verified, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    email = row[2] or row[1]
    providers = [
        provider_row[0]
        for provider_row in _db.execute(
            "SELECT provider FROM auth_identities WHERE user_id = ? ORDER BY provider",
            (user_id,),
        ).fetchall()
    ]
    return {
        "user_id": row[0],
        "username": email,
        "email": email,
        "email_verified": bool(row[3]),
        "providers": providers,
        "created_at": row[4],
    }


# ── 模型配置接口（新版）───────────────────────────────────────────────────

@app.get("/api/model-configs")
async def list_model_configs(request: Request):
    """列出当前用户的所有模型配置（不返回 api_key）。"""
    user_id = _require_user(request)
    rows = _db.execute(
        """SELECT id, name, model, sonnet_model, opus_model, default_model_family,
                  base_url, api_key_encrypted, is_default, created_at
           FROM model_configs
           WHERE user_id = ?
           ORDER BY created_at DESC""",
        (user_id,),
    ).fetchall()
    return [_model_config_response(r) for r in rows]


@app.post("/api/model-configs")
async def create_model_config(req: ModelConfigCreate, request: Request):
    """创建新的模型配置。api_key 会加密存储。"""
    user_id = _require_user(request)
    config_id = str(uuid.uuid4())[:12]
    now = datetime.utcnow().isoformat()
    encrypted_key = _encrypt_api_key(req.api_key)
    model_fields = _resolve_model_config_fields(
        req.model,
        req.sonnet_model,
        req.opus_model,
        req.default_model_family,
    )

    # 如果设为默认，先取消其他默认
    if req.is_default:
        _db.execute("UPDATE model_configs SET is_default = 0 WHERE user_id = ?", (user_id,))

    _db.execute(
        """INSERT INTO model_configs (
               id, user_id, name, model, sonnet_model, opus_model, default_model_family,
               api_key_encrypted, base_url, is_default, created_at
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            config_id,
            user_id,
            req.name,
            model_fields["model"],
            model_fields["sonnet_model"],
            model_fields["opus_model"],
            model_fields["default_model_family"],
            encrypted_key,
            req.base_url or "",
            int(req.is_default),
            now,
        ),
    )
    _db.commit()
    return {
        "id": config_id,
        "name": req.name,
        "model": model_fields["model"],
        "sonnet_model": model_fields["sonnet_model"],
        "opus_model": model_fields["opus_model"],
        "default_model_family": model_fields["default_model_family"],
        "base_url": req.base_url or "",
        "has_api_key": True,
        "is_default": req.is_default,
        "created_at": now,
    }


@app.put("/api/model-configs/{config_id}")
async def update_model_config(config_id: str, req: ModelConfigUpdate, request: Request):
    """更新指定模型配置。api_key 仅在传入时覆盖。"""
    user_id = _require_user(request)
    row = _db.execute(
        """SELECT id, user_id, name, model, sonnet_model, opus_model, default_model_family,
                  base_url, api_key_encrypted, is_default, created_at
           FROM model_configs
           WHERE id = ?""",
        (config_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="配置不存在")
    if row[1] != user_id:
        raise HTTPException(status_code=403, detail="无权修改此配置")

    model_fields = _resolve_model_config_fields(
        req.model if req.model is not None else row[3],
        req.sonnet_model if req.sonnet_model is not None else row[4],
        req.opus_model if req.opus_model is not None else row[5],
        req.default_model_family if req.default_model_family is not None else row[6],
    )
    encrypted_key = _encrypt_api_key(req.api_key) if req.api_key is not None else row[8]
    is_default = bool(req.is_default) if req.is_default is not None else bool(row[9])
    if is_default:
        _db.execute("UPDATE model_configs SET is_default = 0 WHERE user_id = ?", (user_id,))

    _db.execute(
        """UPDATE model_configs
           SET name = ?, model = ?, sonnet_model = ?, opus_model = ?,
               default_model_family = ?, api_key_encrypted = ?, base_url = ?, is_default = ?
           WHERE id = ?""",
        (
            req.name if req.name is not None else row[2],
            model_fields["model"],
            model_fields["sonnet_model"],
            model_fields["opus_model"],
            model_fields["default_model_family"],
            encrypted_key,
            req.base_url if req.base_url is not None else row[7],
            int(is_default),
            config_id,
        ),
    )
    _db.commit()

    updated = _db.execute(
        """SELECT id, name, model, sonnet_model, opus_model, default_model_family,
                  base_url, api_key_encrypted, is_default, created_at
           FROM model_configs
           WHERE id = ?""",
        (config_id,),
    ).fetchone()
    return _model_config_response(updated)


@app.delete("/api/model-configs/{config_id}")
async def delete_model_config(config_id: str, request: Request):
    """删除指定的模型配置。"""
    user_id = _require_user(request)
    row = _db.execute("SELECT user_id FROM model_configs WHERE id = ?", (config_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="配置不存在")
    if row[0] != user_id:
        raise HTTPException(status_code=403, detail="无权删除此配置")
    _db.execute("DELETE FROM model_configs WHERE id = ?", (config_id,))
    _db.commit()
    return {"ok": True}


@app.put("/api/model-configs/{config_id}/default")
async def set_default_config(config_id: str, request: Request):
    """将指定配置设为默认。"""
    user_id = _require_user(request)
    row = _db.execute("SELECT user_id FROM model_configs WHERE id = ?", (config_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="配置不存在")
    if row[0] != user_id:
        raise HTTPException(status_code=403, detail="无权修改此配置")

    _db.execute("UPDATE model_configs SET is_default = 0 WHERE user_id = ?", (user_id,))
    _db.execute("UPDATE model_configs SET is_default = 1 WHERE id = ?", (config_id,))
    _db.commit()
    return {"ok": True}


# ── Credits 接口 ─────────────────────────────────────────────────────────────

@app.get("/api/credits")
async def get_credits(request: Request):
    """获取当前用户总消耗（聚合查询）。"""
    user_id = _require_user(request)
    row = _db.execute(
        """SELECT COALESCE(SUM(cache_hit_input), 0), COALESCE(SUM(cache_miss_input), 0),
                  COALESCE(SUM(output_tokens), 0), COALESCE(SUM(cost_usd), 0)
           FROM credits_log WHERE user_id = ?""",
        (user_id,),
    ).fetchone()
    return {
        "cache_hit_input": row[0] or 0,
        "cache_miss_input": row[1] or 0,
        "output": row[2] or 0,
        "total_cost_usd": row[3] or 0.0,
    }


@app.get("/api/credits/logs")
async def get_credits_logs(request: Request, limit: int = 50, offset: int = 0):
    """获取消耗明细。"""
    user_id = _require_user(request)
    rows = _db.execute(
        """SELECT id, task_id, cache_hit_input, cache_miss_input, output_tokens, cost_usd, model, created_at
           FROM credits_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        (user_id, limit, offset),
    ).fetchall()
    return [
        {
            "id": r[0],
            "task_id": r[1],
            "cache_hit_input": r[2],
            "cache_miss_input": r[3],
            "output_tokens": r[4],
            "cost_usd": r[5],
            "model": r[6] or "",
            "created_at": r[7],
        }
        for r in rows
    ]


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
    task_model = (req.model or "").strip() or None
    if task_model is None and req.model_family:
        _normalize_model_family(req.model_family)
        task_model = _select_config_model(_load_default_model_config(user_id), req.model_family) or None

    task = Task(
        id=task_id,
        url=req.url,
        browse_node_id=browse_node_id,
        model=task_model,
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
    asyncio.create_task(_run_analysis(
        task_id,
        task,
        prompt_override=refresh_prompt,
        startup_system_lines=["[SYSTEM] 🔄 收到增量更新请求，将重新爬取列表页获取最新排名，仅处理新增/变化 ASIN。"],
    ))
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
    cli_config = _build_claude_cli_config(task.owner_id, explicit_model=task.model)

    def _build_chat_cmd(session_id: Optional[str]) -> list[str]:
        cmd = ["claude"]
        if session_id:
            cmd.extend(["--resume", session_id])
        elif cli_config["model"]:
            cmd.extend(["--model", cli_config["model"]])
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
                    proc = _spawn_process(cmd, env_extra=cli_config["env"] or None)
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
                        except Exception as exc:
                            LOGGER.error(
                                "聊天进程终止失败，task_id=%s，exception_type=%s",
                                task_id,
                                type(exc).__name__,
                                exc_info=True,
                            )
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
                except FileNotFoundError as exc:
                    LOGGER.error(
                        "claude CLI 不存在，task_id=%s，exception_type=%s",
                        task_id,
                        type(exc).__name__,
                        exc_info=True,
                    )
                    yield f"data: {json.dumps({'error': 'claude CLI not found'})}\n\n"
                except Exception as e:
                    LOGGER.error(
                        "聊天请求失败，task_id=%s，exception_type=%s",
                        task_id,
                        type(e).__name__,
                        exc_info=True,
                    )
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
