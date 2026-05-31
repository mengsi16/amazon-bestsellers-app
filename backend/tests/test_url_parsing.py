"""URL 解析测试。"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestExtractBrowseNodeId:
    """测试 _extract_browse_node_id 函数。"""

    def test_standard_bestsellers_url(self):
        """标准 Bestsellers URL（数字 ID）。"""
        from main import _extract_browse_node_id
        url = "https://www.amazon.com/Bestsellers/zgbs/1234567890"
        result = _extract_browse_node_id(url)
        assert result == "1234567890"

    def test_url_with_ref(self):
        """带 ref 参数的 URL（gp/bestsellers 格式）。"""
        from main import _extract_browse_node_id
        url = "https://www.amazon.com/gp/bestsellers/home-garden/1234567890/ref=zg_bs_nav_0"
        result = _extract_browse_node_id(url)
        assert result == "1234567890"

    def test_gp_bestsellers_url(self):
        """gp/bestsellers 格式的 URL。"""
        from main import _extract_browse_node_id
        url = "https://www.amazon.com/gp/bestsellers/home-garden/9876543210"
        result = _extract_browse_node_id(url)
        assert result == "9876543210"

    def test_numeric_browse_node_id_at_end(self):
        """URL 末尾的数字型 browse_node_id。"""
        from main import _extract_browse_node_id
        url = "https://www.amazon.com/Bestsellers/1234567890"
        result = _extract_browse_node_id(url)
        assert result == "1234567890"

    def test_invalid_url_no_bestsellers(self):
        """非 Bestsellers URL 应抛出 ValueError。"""
        from main import _extract_browse_node_id
        with pytest.raises(ValueError):
            _extract_browse_node_id("https://www.amazon.com/dp/B08N5WRWNW")

    def test_invalid_url_empty(self):
        """空 URL 应抛出 ValueError。"""
        from main import _extract_browse_node_id
        with pytest.raises(ValueError):
            _extract_browse_node_id("")

    def test_invalid_url_no_amazon(self):
        """非 Amazon URL 应抛出 ValueError。"""
        from main import _extract_browse_node_id
        with pytest.raises(ValueError):
            _extract_browse_node_id("https://www.google.com/search?q=test")

    def test_invalid_url_no_numeric_id(self):
        """无数值 ID 的 URL 应抛出 ValueError。"""
        from main import _extract_browse_node_id
        with pytest.raises(ValueError):
            _extract_browse_node_id("https://www.amazon.com/Bestsellers/zgbs/home-garden")
