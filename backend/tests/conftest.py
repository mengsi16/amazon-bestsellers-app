"""测试配置和 fixtures。"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# 确保 backend 目录在 Python 路径中
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parents[2] / "agent" / "chunker"))


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    """设置测试环境变量，使用临时目录。"""
    monkeypatch.setenv("ENV", "testing")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-for-testing-only")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    # 使用临时目录作为 workspace
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path / "workspace"))
    yield


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """创建临时测试数据库。"""
    db_path = tmp_path / "test_conversations.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    yield db_path


@pytest.fixture
def sample_task_data():
    """示例任务数据。"""
    return {
        "id": "test-task-001",
        "url": "https://www.amazon.com/Bestsellers/zgbs/home-garden",
        "browse_node_id": "home-garden",
        "model": None,
        "session_id": None,
        "status": "pending",
        "created_at": "2026-05-29T00:00:00",
        "updated_at": "2026-05-29T00:00:00",
        "workspace_path": "/tmp/test/workspace/home-garden",
        "error": None,
        "owner_id": "test-user-001",
        "is_public": False,
    }
