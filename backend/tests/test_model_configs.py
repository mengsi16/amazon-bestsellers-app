"""模型配置接口测试。"""

import sys
from pathlib import Path
import sqlite3
import os

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """每个测试使用独立数据库。"""
    db_path = tmp_path / "test_model_configs.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-for-model-configs")
    monkeypatch.setenv("CREDITS_ENCRYPTION_KEY", "test-credits-encryption-key-123456")

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
    email = f"model_user_{id(client)}@xxx.com"
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
    import main

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
    assert data["opus_model"] == main.DEFAULT_OPUS_MODEL
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


def test_missing_encryption_key_fails_fast(monkeypatch):
    """缺少 API key 加密密钥时不能随机生成临时密钥。"""
    import main

    monkeypatch.delenv("CREDITS_ENCRYPTION_KEY", raising=False)

    with pytest.raises(RuntimeError, match="CREDITS_ENCRYPTION_KEY"):
        main._get_fernet()


def test_ensure_encryption_key_generates_env_and_backup(tmp_path, monkeypatch):
    """启动自愈应在空 .env 中生成稳定密钥并写备份。"""
    import main

    monkeypatch.delenv("CREDITS_ENCRYPTION_KEY", raising=False)
    env_path = tmp_path / ".env"
    backup_path = tmp_path / ".secrets" / "CREDITS_ENCRYPTION_KEY.bak"
    env_path.write_text("ENV=development\nCREDITS_ENCRYPTION_KEY=\nPORT=8000\n", encoding="utf-8")

    generated = main._ensure_credits_encryption_key(env_path, backup_path)

    assert generated
    assert f"CREDITS_ENCRYPTION_KEY={generated}" in env_path.read_text(encoding="utf-8")
    assert backup_path.read_text(encoding="utf-8").strip() == generated
    assert os.environ["CREDITS_ENCRYPTION_KEY"] == generated


def test_ensure_encryption_key_keeps_existing_value(tmp_path, monkeypatch):
    """已有密钥时必须沿用旧值，不得重新生成覆盖。"""
    import main

    monkeypatch.delenv("CREDITS_ENCRYPTION_KEY", raising=False)
    env_path = tmp_path / ".env"
    backup_path = tmp_path / ".secrets" / "CREDITS_ENCRYPTION_KEY.bak"
    env_path.write_text("CREDITS_ENCRYPTION_KEY=existing-secret-value\n", encoding="utf-8")

    value = main._ensure_credits_encryption_key(env_path, backup_path)

    assert value == "existing-secret-value"
    assert env_path.read_text(encoding="utf-8") == "CREDITS_ENCRYPTION_KEY=existing-secret-value\n"
    assert backup_path.read_text(encoding="utf-8").strip() == "existing-secret-value"


def test_ensure_encryption_key_syncs_stale_backup(tmp_path, monkeypatch):
    """备份存在但不是当前值时，应同步为当前密钥。"""
    import main

    monkeypatch.delenv("CREDITS_ENCRYPTION_KEY", raising=False)
    env_path = tmp_path / ".env"
    backup_path = tmp_path / ".secrets" / "CREDITS_ENCRYPTION_KEY.bak"
    backup_path.parent.mkdir(parents=True)
    env_path.write_text("CREDITS_ENCRYPTION_KEY=current-secret-value\n", encoding="utf-8")
    backup_path.write_text("stale-secret-value\n", encoding="utf-8")

    value = main._ensure_credits_encryption_key(env_path, backup_path)

    assert value == "current-secret-value"
    assert backup_path.read_text(encoding="utf-8").strip() == "current-secret-value"
