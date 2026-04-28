---
name: amazon-test-chunker
description: Amazon 商品 HTML 提取器的测试技能集，覆盖 TDD 工作流（先测试后代码、9 步 CHECKPOINT 流程）和 Golden Fixture 比对（风格锚点 + 三次重试降级）两大测试策略。当 agent 需要为提取器编写测试、运行 pytest、或进行 Golden Output 比对时调度此技能。
type: orchestration
---

# Test-Chunker

## 子技能

| 子技能 | 技能名 | 职责 |
| --- | --- | --- |
| TDD 工作流 | `tdd-workflow` | 先写测试后写代码的完整执行流程、检查点、Smoke Fixture 选取、测试代码风格 |
| Golden Fixture 比对 | `golden-fixture` | 基于风格锚点的 Golden Output 比对策略、三次重试降级机制、Golden 模板文件 |

## 通用测试规则

### 测试文件位置与清理

- **所有测试文件必须写入 `tests/` 目录**（项目根目录下）
- 命名：`tests/test_<block>_extract.py`
- **严禁在根目录或 `chunker/` 目录下创建测试文件**
- 临时输出必须用 `tempfile.TemporaryDirectory()`，不得残留
- 运行：`python -m pytest tests/ -v`
- 测试通过后**不删除测试文件**（回归测试的一部分）
- 临时探测脚本用完即删

### 硬性规则

1. **不得在没有运行 pytest 的情况下完成任何提取器**
2. **写完测试和实现必须立即执行**
3. **测试失败不得跳过**，必须修复后重跑
4. **FINAL GATE 必须执行**：`python -m pytest tests/ -v` 全绿
5. 无法运行测试时必须报告用户，不得静默退出
6. 最终回复必须贴出 pytest 执行结果摘要

### 目录结构验证测试（必须包含）

**batch_run 产出的目录结构是强制的，必须编写测试来验证。** 如果目录结构不正确，整个流水线的产出无法被下游 agent 消费。

在 `tests/test_batch_structure.py` 中编写以下验证：

```python
"""Test that batch_run output conforms to the mandatory directory structure."""
import re
from pathlib import Path

BLOCKS = ["ppd", "customer_reviews", "product_details", "aplus"]
DIR_PATTERN = re.compile(r"^\d{3,}_[A-Z0-9]{10}$")  # e.g. 001_B0XXXXX


def test_product_dirs_have_rank_prefix(out_dir: Path):
    """每个商品目录名必须是 {rank}_{ASIN} 格式，禁止纯 ASIN。"""
    for d in out_dir.iterdir():
        if d.is_dir():
            assert DIR_PATTERN.match(d.name), (
                f"目录名不合规: {d.name}，必须是 {{rank}}_{{ASIN}} 格式（如 001_B0XXXXX）"
            )


def test_block_subdirs_exist(product_dir: Path):
    """每个 block 必须有独立子目录，不允许平铺。"""
    for block in BLOCKS:
        block_dir = product_dir / block
        assert block_dir.is_dir(), f"缺少 block 子目录: {block}/"


def test_raw_files_in_block_raw(product_dir: Path):
    """分块产出必须在 block/raw/ 下，不允许在商品根目录。"""
    for block in BLOCKS:
        raw_dir = product_dir / block / "raw"
        if (product_dir / block).exists():
            assert raw_dir.is_dir(), f"缺少 raw 子目录: {block}/raw/"
            assert (raw_dir / f"{block}.html").exists(), f"缺少: {block}/raw/{block}.html"
    # 禁止：根目录下不应有 .html 文件
    root_htmls = list(product_dir.glob("*.html"))
    assert len(root_htmls) == 0, f"商品根目录不应有 .html 文件: {root_htmls}"


def test_extract_files_in_block_extract(product_dir: Path):
    """提取产出必须在 block/extract/ 下，不允许共享 extract/ 目录。"""
    for block in BLOCKS:
        extract_dir = product_dir / block / "extract"
        if (product_dir / block / "raw" / f"{block}.html").exists():
            assert extract_dir.is_dir(), f"缺少 extract 子目录: {block}/extract/"
            assert (extract_dir / f"{block}_extracted.md").exists(), (
                f"缺少: {block}/extract/{block}_extracted.md"
            )
    # 禁止：根目录下不应有共享的 extract/ 目录
    assert not (product_dir / "extract").exists(), "禁止使用共享 extract/ 目录"


def test_global_manifest_exists(out_dir: Path):
    """out_dir 根目录必须有 global_manifest.json。"""
    assert (out_dir / "global_manifest.json").exists(), "缺少 global_manifest.json"


def test_product_manifest_exists(product_dir: Path):
    """每个商品目录内必须有 manifest.json。"""
    assert (product_dir / "manifest.json").exists(), f"缺少: {product_dir.name}/manifest.json"
```

> **上述测试是模板**，实际编写时需要用 pytest fixture 动态发现 `out_dir` 和 `product_dir`。
> 这些测试必须在 `python -m pytest tests/ -v` 中**全绿**才能通过 FINAL GATE。

### 被测代码风格

```python
"""Extract structured markdown from <block_name> HTML chunk."""
import re
from pathlib import Path
from bs4 import BeautifulSoup, Tag

def _normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def extract_<block>_markdown(html_path: Path, out_path: Path | None = None) -> Path:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")
    # ... 提取逻辑 ...
    output_path = out_path if out_path else html_path.with_name("<block>_extracted.md")
    output_path.write_text(markdown, encoding="utf-8")
    return output_path
```
