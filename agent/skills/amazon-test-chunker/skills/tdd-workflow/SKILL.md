---
name: tdd-workflow
description: Amazon 商品 HTML 提取器的 TDD 工作流。先写测试后写代码，9 步执行顺序（每步带 CHECKPOINT），Smoke Fixture 选取策略，测试代码风格模板。当 agent 需要按 TDD 流程开发提取器时调度此技能。
type: skill
---

# TDD 工作流

## 原则

- **先写测试，后写代码**
- **冒烟测试优先**：每个提取器先选取 3–4 个固定 Raw HTML 样本作为 smoke fixture；测试限定在这 3–4 个文件内通过后才进入下一步
- 每个提取器至少用 3–4 个样本测试
- 测试断言关键字段存在且值正确
- 测试断言不含垃圾内容（`<script>`, `<br>`, 导航文本等）
- **Golden Fixture 比对**：测试必须额外运行 `test_*_golden_comparison` 用例（详见 `skills/golden-fixture/SKILL.md`）

## 执行顺序（带强制检查点）

每个步骤必须 **实际运行 pytest** 并确认通过后才能进入下一步。

```
1. Smoke Fixture 选取：
   a. 从 `products/{ASIN}/product.html` 中手动指定 3–4 个代表性 ASIN（优先 Top1/Top25）
   b. 运行 `python -m chunker.batch_run --products-dir <products_dir> --rankings-jsonl <rankings_jsonl> --out-dir chunks/ --limit 4`
      确认能正常分块生成 chunks
   c. 这 3–4 个产品的 chunk 目录就是后续所有提取器测试的固定 fixture

2. DOM 探测：
   读取上述 3–4 个样本 → 用 Python 脚本探测 DOM 结构（临时脚本，写到 tests/ 下，用完删除）

3. 分块器：
   a. 写测试 tests/test_static_chunker.py
   b. 实现 chunker/static_chunker.py
   c. ✅ CHECKPOINT: `python -m pytest tests/test_static_chunker.py -v`

4. Reviews 提取器：
   a. 复制 skills/amazon-extractor/skills/customer-reviews/customer_reviews_extract.py 到 chunker/customer_reviews_extract.py
   b. 写测试 tests/test_customer_reviews_extract.py（结构断言 + Golden 比对）
   c. ✅ CHECKPOINT: `python -m pytest tests/test_customer_reviews_extract.py -v`
   d. 失败只微调模版，不得重写

5. Details 提取器：
   a. 写测试 tests/test_product_details_extract.py（结构断言 + Golden 比对）
   b. 实现 chunker/product_details_extract.py
   c. ✅ CHECKPOINT: `python -m pytest tests/test_product_details_extract.py -v`

6. PPD 提取器：
   a. 写测试 tests/test_ppd_extract.py（Core/Buybox/Twister/Overview/Bullets/Images + Golden 比对）
   b. 逐子阶段实现
   c. ✅ CHECKPOINT: `python -m pytest tests/test_ppd_extract.py -v`

7. A+ 提取器：
   a. 写测试 tests/test_aplus_extract.py（Comparison/Assets/Story + 两个 Golden 比对）
   b. 逐子阶段实现
   c. ✅ CHECKPOINT: `python -m pytest tests/test_aplus_extract.py -v`

8. 编排脚本：
   a. 写测试 tests/test_batch_run.py
   b. 实现 chunker/batch_run.py
   c. ✅ CHECKPOINT: `python -m pytest tests/test_batch_run.py -v`

9. ✅ FINAL GATE: `python -m pytest tests/ -v` 全部通过
```

## 测试代码风格

```python
# 文件位置：tests/test_ppd_extract.py
import tempfile
import unittest
from pathlib import Path

# Smoke Fixture：只针对 3–4 个样本，不面向全量数据
CHUNKS_DIR = Path(__file__).resolve().parents[1] / "chunks"
SMOKE_SAMPLE_COUNT = 4
SMOKE_DIRS = sorted(
    [d for d in CHUNKS_DIR.iterdir() if d.is_dir() and (d / "ppd.html").exists()]
)[:SMOKE_SAMPLE_COUNT]

class TestPpdExtractor(unittest.TestCase):
    def test_product_001_core_fields(self):
        self.assertTrue(len(SMOKE_DIRS) >= 1, "No smoke fixture chunks found")
        html_path = SMOKE_DIRS[0] / "ppd.html"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ppd.md"
            result = extract_ppd_markdown(html_path, out)
            content = result.read_text(encoding="utf-8")
            self.assertIn("# PPD Extracted", content)
            self.assertIn("## Core", content)
            self.assertIn("- Title:", content)
            self.assertIn("- Current price: $", content)
            if "| Field | Value |" in content:
                self.assertIn("| --- | --- |", content)
            self.assertNotIn("<br", content.lower())
            self.assertNotIn("<script", content.lower())

    def test_product_002_also_works(self):
        if len(SMOKE_DIRS) < 2:
            self.skipTest("Only 1 smoke fixture available")
        html_path = SMOKE_DIRS[1] / "ppd.html"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ppd.md"
            result = extract_ppd_markdown(html_path, out)
            content = result.read_text(encoding="utf-8")
            self.assertIn("# PPD Extracted", content)
            self.assertIn("## Core", content)

    def test_golden_comparison(self):
        """Compare output against Golden fixture; retry 3x; escalate to human review if unstable."""
        self.assertTrue(len(SMOKE_DIRS) >= 1, "No smoke fixture chunks found")
        html_path = SMOKE_DIRS[0] / "ppd.html"
        golden_path = TEMPLATES_DIR / "ppd_extracted.md"
        if not html_path.exists():
            self.skipTest("Reference sample chunk not found")
        if not golden_path.exists():
            self.skipTest("Golden fixture not found")
        _run_with_golden_retry(self, extract_ppd_markdown, html_path, golden_path)
```

## Smoke Fixture 路径约定

- `CHUNKS_DIR = Path(__file__).resolve().parents[1] / "chunks"`
- 动态发现：`sorted([d for d in CHUNKS_DIR.iterdir() if d.is_dir() and (d / "<block>.html").exists()])[:SMOKE_SAMPLE_COUNT]`
- **不得硬编码 ASIN 路径**，必须用 glob 动态发现
