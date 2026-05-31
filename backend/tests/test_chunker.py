"""Chunker 模块测试。"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agent" / "chunker"))


class TestIsValidProductPage:
    """测试产品页面验证函数。"""

    def test_valid_product_page(self):
        """有效产品页面应返回 True。"""
        from static_chunker import _is_valid_product_page
        html = '''
        <html>
        <head><title>Test Product</title></head>
        <body>
            <div id="dp-container">
                <h1 id="productTitle">Test Product</h1>
                <div id="ppd">Product details</div>
            </div>
        </body>
        </html>
        '''
        assert _is_valid_product_page(html) is True

    def test_homepage_redirect(self):
        """Amazon 首页应返回 False。"""
        from static_chunker import _is_valid_product_page
        html = '''
        <html>
        <head><title>Amazon.com. Spend less. Smile more.</title></head>
        <body>
            <div>Homepage content</div>
        </body>
        </html>
        '''
        assert _is_valid_product_page(html) is False

    def test_captcha_page(self):
        """验证码页面应返回 False。"""
        from static_chunker import _is_valid_product_page
        html = '''
        <html>
        <head><title>Robot Check</title></head>
        <body>
            <div>Type the characters you see in this image</div>
        </body>
        </html>
        '''
        assert _is_valid_product_page(html) is False

    def test_service_error_page(self):
        """503 错误页面应返回 False。"""
        from static_chunker import _is_valid_product_page
        html = '''
        <html>
        <head><title>503 - Service Unavailable Error</title></head>
        <body>
            <div>Service Unavailable</div>
        </body>
        </html>
        '''
        assert _is_valid_product_page(html) is False

    def test_empty_html(self):
        """空 HTML 应返回 False。"""
        from static_chunker import _is_valid_product_page
        assert _is_valid_product_page("") is False


class TestFindAplusContainers:
    """测试 A+ 容器查找函数。"""

    def test_single_aplus_container(self):
        """单个 A+ 容器。"""
        from static_chunker import _find_aplus_containers
        from bs4 import BeautifulSoup
        html = '''
        <html><body>
            <div id="aplus">Content 1</div>
        </body></html>
        '''
        soup = BeautifulSoup(html, "html.parser")
        containers = _find_aplus_containers(soup)
        assert len(containers) == 1

    def test_dual_aplus_containers(self):
        """双 A+ 容器（Brand Story + Premium A+）。"""
        from static_chunker import _find_aplus_containers
        from bs4 import BeautifulSoup
        html = '''
        <html><body>
            <div id="aplus">Brand Story Content</div>
            <div id="aplus">Premium A+ Content</div>
        </body></html>
        '''
        soup = BeautifulSoup(html, "html.parser")
        containers = _find_aplus_containers(soup)
        assert len(containers) == 2

    def test_no_aplus_container(self):
        """无 A+ 容器。"""
        from static_chunker import _find_aplus_containers
        from bs4 import BeautifulSoup
        html = '''
        <html><body>
            <div id="ppd">Product details</div>
        </body></html>
        '''
        soup = BeautifulSoup(html, "html.parser")
        containers = _find_aplus_containers(soup)
        assert len(containers) == 0


class TestChunkProductHtml:
    """测试产品 HTML 分块函数。"""

    def test_chunk_valid_product(self, tmp_path):
        """有效产品页面应成功分块。"""
        from static_chunker import chunk_product_html
        # 创建测试 HTML
        html = '''
        <html><body>
            <div id="ppd">Product details</div>
            <div id="customerReviews">Reviews</div>
            <div id="productDetails_feature_div">Details</div>
            <div id="aplus">A+ Content</div>
        </body></html>
        '''
        html_path = tmp_path / "product.html"
        html_path.write_text(html, encoding="utf-8")
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        result = chunk_product_html(html_path, out_dir)
        assert result["status"] == "SUCCESS"
        assert "blocks" in result

    def test_chunk_missing_html(self, tmp_path):
        """HTML 文件不存在应返回 SKIPPED。"""
        from static_chunker import chunk_product_html
        html_path = tmp_path / "nonexistent.html"
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        result = chunk_product_html(html_path, out_dir)
        assert result["status"] == "SKIPPED"
        assert result["reason"] == "product_html_missing"

    def test_chunk_invalid_page(self, tmp_path):
        """无效页面应返回 SKIPPED。"""
        from static_chunker import chunk_product_html
        html = '''
        <html><head><title>Amazon.com. Spend less. Smile more.</title></head>
        <body>Homepage</body></html>
        '''
        html_path = tmp_path / "product.html"
        html_path.write_text(html, encoding="utf-8")
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        result = chunk_product_html(html_path, out_dir)
        assert result["status"] == "SKIPPED"
        assert result["reason"] == "invalid_product_page"
