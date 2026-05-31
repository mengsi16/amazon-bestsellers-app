"""认证相关测试。"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPasswordHashing:
    """测试密码哈希和验证。"""

    def test_hash_password(self):
        """密码哈希应返回字符串。"""
        from main import _hash_password
        result = _hash_password("testpassword123")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_verify_correct_password(self):
        """正确密码应验证通过。"""
        from main import _hash_password, _verify_password
        password = "mypassword"
        hashed = _hash_password(password)
        assert _verify_password(password, hashed) is True

    def test_verify_wrong_password(self):
        """错误密码应验证失败。"""
        from main import _hash_password, _verify_password
        hashed = _hash_password("correctpassword")
        assert _verify_password("wrongpassword", hashed) is False


class TestJWTToken:
    """测试 JWT token 创建和解码。"""

    def test_create_jwt(self):
        """创建 JWT 应返回有效字符串。"""
        from main import _create_jwt
        token = _create_jwt("user-001", "testuser")
        assert isinstance(token, str)
        assert len(token) > 0
        # JWT 格式：header.payload.signature
        parts = token.split(".")
        assert len(parts) == 3

    def test_decode_jwt_valid(self):
        """有效 token 应能正确解码。"""
        from main import _create_jwt, _decode_jwt
        user_id = "user-001"
        username = "testuser"
        token = _create_jwt(user_id, username)
        payload = _decode_jwt(token)
        assert payload is not None
        assert payload["sub"] == user_id
        assert payload["username"] == username

    def test_decode_jwt_invalid(self):
        """无效 token 应返回 None。"""
        from main import _decode_jwt
        payload = _decode_jwt("invalid.token.here")
        assert payload is None

    def test_decode_jwt_expired(self):
        """过期 token 应返回 None。"""
        from main import JWT_SECRET_KEY_CURRENT, JWT_ALGORITHM
        from jose import jwt
        from datetime import datetime, timedelta
        # 创建一个已过期的 token
        expire = datetime.utcnow() - timedelta(days=1)
        payload = {"sub": "user-001", "username": "test", "exp": expire}
        token = jwt.encode(payload, JWT_SECRET_KEY_CURRENT, algorithm=JWT_ALGORITHM)
        from main import _decode_jwt
        result = _decode_jwt(token)
        assert result is None
