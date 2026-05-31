"""模型配置接口测试。"""

import sys
from pathlib import Path
import sqlite3

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """每个测试使用独立数据库。"""
    db_path = tmp_path / "test_model_configs.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-for-model-configs")

    real_connect = sqlite3.connect

    def connect_for_test(*args, **kwargs):
        kwargs["check_same_thread"] = False
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect_for_test)

    import main
    main.DB_PATH = db_path
    main._db = main._init_db()
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
        "username": f"model_user_{id(client)}",
        "password": "testpassword123",
    })
    assert resp.status_code == 200
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_model_config_returns_family_fields_without_api_key(client, auth_headers):
    """创建模型配置后应返回 Sonnet/Opus 字段且不回显 API key。"""
    resp = client.post("/api/model-configs", json={
        "name": "公司网关",
        "api_key": "sk-test-secret",
        "base_url": "https://gateway.example.com",
        "sonnet_model": "sonnet",
        "opus_model": "opus",
        "default_model_family": "opus",
        "is_default": True,
    }, headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["sonnet_model"] == "sonnet"
    assert data["opus_model"] == "opus"
    assert data["default_model_family"] == "opus"
    assert data["has_api_key"] is True
    assert "api_key" not in data

    listed = client.get("/api/model-configs", headers=auth_headers).json()
    assert listed[0]["sonnet_model"] == "sonnet"
    assert listed[0]["opus_model"] == "opus"
    assert listed[0]["default_model_family"] == "opus"
    assert listed[0]["has_api_key"] is True
    assert "api_key" not in listed[0]


def test_create_model_config_accepts_legacy_model_field(client, auth_headers):
    """旧版 model 字段应兼容为 Sonnet 模型。"""
    resp = client.post("/api/model-configs", json={
        "name": "旧配置",
        "api_key": "sk-test-secret",
        "model": "claude-sonnet-legacy",
        "is_default": True,
    }, headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "claude-sonnet-legacy"
    assert data["sonnet_model"] == "claude-sonnet-legacy"
    assert data["opus_model"] == "opus"
    assert data["default_model_family"] == "sonnet"


def test_decrypt_api_key_fail_fast(monkeypatch):
    """解密失败不能把密文当 API key 返回。"""
    import main

    class BrokenFernet:
        def decrypt(self, value):
            raise ValueError("bad token")

    monkeypatch.setattr(main, "_get_fernet", lambda: BrokenFernet())

    with pytest.raises(ValueError, match="bad token"):
        main._decrypt_api_key("not-a-valid-token")
