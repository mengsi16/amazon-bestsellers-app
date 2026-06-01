"""API 端点测试。"""

import pytest
import sys
import os
import json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def isolate_db(monkeypatch):
    """每个测试使用独立的数据库。"""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-for-api-tests")
    # 重新加载模块以使用新的数据库路径
    import main
    main.DB_PATH = ":memory:"
    main._db = main._init_db()
    async def noop_run_analysis(task_id, task, prompt_override=None, startup_system_lines=None):
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
    email = f"test_{id(client)}@xxx.com"
    code_resp = client.post("/api/auth/email/send-code", json={
        "email": email,
        "purpose": "register",
    })
    assert code_resp.status_code == 200
    resp = client.post("/api/auth/email/register", json={
        "email": email,
        "password": "testpassword123",
        "code": code_resp.json()["dev_code"],
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
        """邮箱验证码注册应返回用户信息和 token。"""
        email = f"newuser_{id(client)}@xxx.com"
        code_resp = client.post("/api/auth/email/send-code", json={
            "email": email,
            "purpose": "register",
        })
        resp = client.post("/api/auth/email/register", json={
            "email": email,
            "password": "password123",
            "code": code_resp.json()["dev_code"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "user_id" in data
        assert "token" in data
        assert data["email"] == email

    def test_register_short_username(self, client):
        """非邮箱注册应返回 400。"""
        resp = client.post("/api/auth/register", json={
            "username": "a",
            "password": "password123"
        })
        assert resp.status_code == 400

    def test_register_short_password(self, client):
        """密码太短应返回 400。"""
        email = f"testuser2_{id(client)}@xxx.com"
        code_resp = client.post("/api/auth/email/send-code", json={
            "email": email,
            "purpose": "register",
        })
        resp = client.post("/api/auth/email/register", json={
            "email": email,
            "password": "12345",
            "code": code_resp.json()["dev_code"],
        })
        assert resp.status_code == 400

    def test_login_success(self, client):
        """邮箱登录应返回 token。"""
        email = f"loginuser_{id(client)}@xxx.com"
        code_resp = client.post("/api/auth/email/send-code", json={
            "email": email,
            "purpose": "register",
        })
        client.post("/api/auth/email/register", json={
            "email": email,
            "password": "password123",
            "code": code_resp.json()["dev_code"],
        })
        resp = client.post("/api/auth/login", json={
            "username": email,
            "password": "password123"
        })
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_login_wrong_password(self, client):
        """错误密码应返回 401。"""
        email = f"wrongpw_{id(client)}@xxx.com"
        code_resp = client.post("/api/auth/email/send-code", json={
            "email": email,
            "purpose": "register",
        })
        client.post("/api/auth/email/register", json={
            "email": email,
            "password": "password123",
            "code": code_resp.json()["dev_code"],
        })
        resp = client.post("/api/auth/login", json={
            "username": email,
            "password": "wrongpassword"
        })
        assert resp.status_code == 401

    def test_me_endpoint(self, client, auth_headers):
        """获取当前用户信息。"""
        resp = client.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        assert "user_id" in resp.json()
        assert resp.json()["email_verified"] is True

    def test_me_unauthorized(self, client):
        """未登录应返回 401。"""
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_email_send_code_for_test_domain_returns_dev_code(self, client):
        """测试邮箱请求验证码应返回开发验证码，避免真实发信。"""
        resp = client.post("/api/auth/email/send-code", json={
            "email": "test@xxx.com",
            "purpose": "register",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["dev_code"] == "123456"
        assert "expires_at" in data

    def test_email_register_requires_code_and_login_works(self, client):
        """邮箱注册必须校验验证码，注册后可用邮箱登录。"""
        code_resp = client.post("/api/auth/email/send-code", json={
            "email": "test@xxx.com",
            "purpose": "register",
        })
        code = code_resp.json()["dev_code"]

        wrong_resp = client.post("/api/auth/email/register", json={
            "email": "test@xxx.com",
            "password": "password123",
            "code": "000000",
        })
        assert wrong_resp.status_code == 400

        register_resp = client.post("/api/auth/email/register", json={
            "email": "test@xxx.com",
            "password": "password123",
            "code": code,
        })
        assert register_resp.status_code == 200
        register_data = register_resp.json()
        assert register_data["email"] == "test@xxx.com"
        assert register_data["email_verified"] is True
        assert "token" in register_data

        login_resp = client.post("/api/auth/email/login", json={
            "email": "test@xxx.com",
            "password": "password123",
        })
        assert login_resp.status_code == 200
        assert login_resp.json()["email"] == "test@xxx.com"

    def test_non_email_register_and_login_are_rejected(self, client):
        """旧用户名入口不再接受非邮箱账号。"""
        register_resp = client.post("/api/auth/register", json={
            "username": "legacyuser",
            "password": "password123",
        })
        assert register_resp.status_code == 400

        login_resp = client.post("/api/auth/login", json={
            "username": "legacyuser",
            "password": "password123",
        })
        assert login_resp.status_code == 400


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


class TestStreamHistory:
    """测试同一任务多轮执行的流式历史记录。"""

    def test_stream_items_from_multiple_runs_keep_separate_history_rows(self):
        """同一 task 的新执行轮次应追加历史记录，不应覆盖上一轮同名 item。"""
        import main

        task_id = "task-stream-runs"
        first_init = json.dumps({
            "type": "system",
            "subtype": "init",
            "session_id": "first-session",
        })
        second_init = json.dumps({
            "type": "system",
            "subtype": "init",
            "session_id": "second-session",
        })
        result = json.dumps({
            "type": "result",
            "is_error": False,
            "result": "",
            "duration_ms": 10,
            "total_cost_usd": 0.01,
            "num_turns": 1,
        })

        main._reset_task_stream(task_id)
        main._feed_stream_line(task_id, "[SYSTEM] 第一轮启动")
        main._feed_stream_line(task_id, first_init)
        main._feed_stream_line(task_id, result)

        main._reset_task_stream(task_id)
        main._feed_stream_line(task_id, "[SYSTEM] 第二轮启动")
        main._feed_stream_line(task_id, second_init)
        main._feed_stream_line(task_id, result)

        items, order = main._load_stream_history(task_id)

        assert len(items) == 6
        assert len(order) == 6
        assert [item["content"] for item in items if item["kind"] == "system_note"] == [
            "第一轮启动",
            "Session started — first-sessio…",
            "第二轮启动",
            "Session started — second-sessi…",
        ]
        assert len([item for item in items if item["kind"] == "final_result"]) == 2

    def test_refresh_startup_system_line_survives_stream_reset(self):
        """刷新入口传入的启动提示应在 stream reset 后写入，避免前端只能看到旧提示。"""
        import main

        task_id = "task-refresh-startup-line"
        task = main.Task(
            id=task_id,
            url="https://www.amazon.com/gp/bestsellers/fashion/1040658/",
            browse_node_id="1040658",
            created_at="2026-06-01T00:00:00",
            updated_at="2026-06-01T00:00:00",
            workspace_path="E:\\PostGraduate\\Project\\amazon-bestsellers\\workspace\\1040658",
        )

        main._log_and_stream(task_id, "[SYSTEM] 旧提示")
        main._initialize_analysis_stream(task_id, task, [
            "[SYSTEM] 🔄 收到增量更新请求，将重新爬取列表页获取最新排名，仅处理新增/变化 ASIN。",
        ])

        items, order = main._load_stream_history(task_id)
        current_run_items = [item for item in items if item["id"] in order[-3:]]

        assert [item["content"] for item in current_run_items] == [
            "启动分析任务: https://www.amazon.com/gp/bestsellers/fashion/1040658/",
            "Workspace: E:\\PostGraduate\\Project\\amazon-bestsellers\\workspace\\1040658",
            "🔄 收到增量更新请求，将重新爬取列表页获取最新排名，仅处理新增/变化 ASIN。",
        ]

    def test_reconcile_keeps_active_refresh_pending_when_old_summary_exists(self, tmp_path, monkeypatch):
        """活跃刷新任务不能被旧 summary.md 提前收敛为 completed。"""
        import main

        browse_node_id = "1040658"
        task_id = "task-active-refresh"
        workspace_base = tmp_path / "workspace"
        workspace = workspace_base / browse_node_id
        workspace.mkdir(parents=True)
        (workspace / "summary.md").write_text("# old summary", encoding="utf-8")
        monkeypatch.setattr(main, "WORKSPACE_BASE", workspace_base)
        monkeypatch.setitem(main._active_browse_node_tasks, browse_node_id, task_id)

        task = main.Task(
            id=task_id,
            url="https://www.amazon.com/gp/bestsellers/fashion/1040658/",
            browse_node_id=browse_node_id,
            status=main.TaskStatus.PENDING,
            created_at="2026-06-01T00:00:00",
            updated_at="2026-06-01T00:00:00",
            workspace_path=str(workspace),
        )

        reconciled = main._reconcile_task(task)

        assert reconciled.status == main.TaskStatus.PENDING
        assert reconciled.error is None
