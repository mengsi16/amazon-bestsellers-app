"""API 端点测试。"""

import pytest
import sys
import os
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """每个测试使用独立的数据库。"""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-for-api-tests")
    # 重新加载模块以使用新的数据库路径
    import main
    main.DB_PATH = db_path
    main._db = main._init_db()
    async def noop_run_analysis(task_id, task, prompt_override=None):
        main._active_browse_node_tasks.pop(task.browse_node_id, None)
    monkeypatch.setattr(main, "_run_analysis", noop_run_analysis)
    yield


@pytest.fixture
def client():
    """创建测试客户端。"""
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """创建测试用户并返回认证 headers。"""
    resp = client.post("/api/auth/register", json={
        "username": f"testuser_{id(client)}",
        "password": "testpassword123"
    })
    assert resp.status_code == 200
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


class TestHealthEndpoint:
    """测试健康检查端点。"""

    def test_health_check(self, client):
        """健康检查应返回 200。"""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data


class TestAuthEndpoints:
    """测试认证端点。"""

    def test_register_success(self, client):
        """注册应返回用户信息和 token。"""
        resp = client.post("/api/auth/register", json={
            "username": f"newuser_{id(client)}",
            "password": "password123"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "user_id" in data
        assert "token" in data

    def test_register_short_username(self, client):
        """用户名太短应返回 400。"""
        resp = client.post("/api/auth/register", json={
            "username": "a",
            "password": "password123"
        })
        assert resp.status_code == 400

    def test_register_short_password(self, client):
        """密码太短应返回 400。"""
        resp = client.post("/api/auth/register", json={
            "username": f"testuser2_{id(client)}",
            "password": "12345"
        })
        assert resp.status_code == 400

    def test_login_success(self, client):
        """登录应返回 token。"""
        username = f"loginuser_{id(client)}"
        client.post("/api/auth/register", json={
            "username": username,
            "password": "password123"
        })
        resp = client.post("/api/auth/login", json={
            "username": username,
            "password": "password123"
        })
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_login_wrong_password(self, client):
        """错误密码应返回 401。"""
        username = f"wrongpw_{id(client)}"
        client.post("/api/auth/register", json={
            "username": username,
            "password": "password123"
        })
        resp = client.post("/api/auth/login", json={
            "username": username,
            "password": "wrongpassword"
        })
        assert resp.status_code == 401

    def test_me_endpoint(self, client, auth_headers):
        """获取当前用户信息。"""
        resp = client.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        assert "user_id" in resp.json()

    def test_me_unauthorized(self, client):
        """未登录应返回 401。"""
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401


class TestTaskEndpoints:
    """测试任务端点。"""

    def test_create_task(self, client, auth_headers):
        """创建任务应返回任务信息。"""
        resp = client.post("/api/tasks", json={
            "url": "https://www.amazon.com/Bestsellers/zgbs/1234567890"
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["browse_node_id"] == "1234567890"

    def test_create_task_invalid_url(self, client, auth_headers):
        """无效 URL 应返回 400。"""
        resp = client.post("/api/tasks", json={
            "url": "https://www.google.com"
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_list_tasks(self, client, auth_headers):
        """列出任务应返回列表。"""
        client.post("/api/tasks", json={
            "url": "https://www.amazon.com/Bestsellers/zgbs/1111111111"
        }, headers=auth_headers)
        resp = client.get("/api/tasks", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_task(self, client, auth_headers):
        """获取单个任务。"""
        create_resp = client.post("/api/tasks", json={
            "url": "https://www.amazon.com/Bestsellers/zgbs/2222222222"
        }, headers=auth_headers)
        task_id = create_resp.json()["id"]
        resp = client.get(f"/api/tasks/{task_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == task_id

    def test_get_task_not_found(self, client, auth_headers):
        """获取不存在的任务应返回 404。"""
        resp = client.get("/api/tasks/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_task(self, client, auth_headers):
        """删除任务。"""
        create_resp = client.post("/api/tasks", json={
            "url": "https://www.amazon.com/Bestsellers/zgbs/3333333333"
        }, headers=auth_headers)
        task_id = create_resp.json()["id"]
        resp = client.delete(f"/api/tasks/{task_id}", headers=auth_headers)
        assert resp.status_code == 200
        get_resp = client.get(f"/api/tasks/{task_id}", headers=auth_headers)
        assert get_resp.status_code == 404

    def test_unauthorized_access(self, client):
        """未认证访问应返回 401。"""
        resp = client.get("/api/tasks")
        assert resp.status_code == 401
