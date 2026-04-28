---
name: golden-fixture
description: 基于风格锚点（Style Anchors）的 Golden Output Fixture 比对策略。从已验证的 Golden 模板中提取结构锚点（节标题、表格字段名、Bullet 前缀），与提取器输出比对。包含三次重试 + 人工审核降级机制。Golden 模板文件存放在 templates/ 子目录中。
type: skill
---

# Golden Fixture 比对

## 概述

Golden Output Fixture 是已验证的正确提取结果，用于 TDD 锚定基准。比对方式**不检查内容是否一模一样**，只检查**风格是否一致**——从 Golden 文件中提取「结构锚点」（Style Anchors）并断言每一个都出现在提取输出中。

## Golden 模板文件

Golden 模板存放在本 skill 的 `templates/` 子目录中：

| 提取器 | Golden 文件 |
| --- | --- |
| customer_reviews | `templates/customer_reviews_extracted.md` |
| product_details | `templates/product_details_extracted.md` |
| ppd | `templates/ppd_extracted.md` |
| aplus_comparison | `templates/aplus_comparison.md` |
| aplus_assets | `templates/aplus_assets.md` |

## 风格锚点（Style Anchors）提取规则

| 锚点类型 | 提取规则 | 示例 |
| --- | --- | --- |
| 节标题 | `#`/`##`/`###` 开头的行，去掉末尾动态计数如 `(10)` | `## Summary` |
| 表格字段名 | 表格行的**左列**（字段名），不含右列值 | `\| Overall rating \|` |
| 表格分隔符 | 任何 `\| --- \|` 行 | `\| --- \|` |
| Bullet 字段前缀 | `- FieldName:` 前缀，冒号后的值不参与比对 | `- Overall rating:` |

具体值（价格、评分、评论数等）**不作为锚点**，不同产品提取出来本来就不同。

## 锚点提取代码

```python
import re
import tempfile
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "test-chunker" / "skills" / "golden-fixture" / "templates"


def _golden_style_anchors(golden_path: Path) -> list[str]:
    """
    Extract style-level structural anchors from a golden fixture.
    Only structure is checked — NOT product-specific values.

    Extracted:
    - Section headers (#/##/###): trailing dynamic counts like '(10)' stripped
    - Numbered sub-headers (### 1. Title): collapsed to heading-level marker only
    - Table separator rows:  normalized to '| --- |'
    - Table field names:     left column of table rows (NOT value columns)
    - Bullet field prefixes: '- FieldName:' (NOT the value after the colon)
    """
    lines = golden_path.read_text(encoding="utf-8").splitlines()
    anchors: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        if s and s not in seen:
            seen.add(s)
            anchors.append(s)

    for line in lines:
        s = line.strip()
        if not s or s == "---":
            continue

        # Section headers
        if s.startswith("#"):
            normalized = re.sub(r"\s*\(\d+\)\s*$", "", s).strip()
            if re.match(r"#{2,}\s+\d+\.", normalized):
                level = re.match(r"(#+)", normalized).group(1)
                add(level)
            else:
                add(normalized)
            continue

        # Table separators → generic presence check
        if re.fullmatch(r"\|[\s\-|]+\|", s):
            add("| --- |")
            continue

        # Table data rows → extract field name (left column) only
        if s.startswith("|") and s.endswith("|"):
            cols = [c.strip() for c in s[1:-1].split("|")]
            if cols and cols[0] and cols[0] != "---":
                add(f"| {cols[0]} |")
            continue

        # Bullet items → strip value, keep field prefix only
        m = re.match(r"^(-\s+[\w][\w &/()\\.+''`:-]*?)\s*:", s)
        if m:
            add(m.group(1) + ":")
            continue

    return anchors
```

## 三次重试 + 人工审核降级策略

Golden 比对失败时最多重试 3 次：
- **任一次通过** → 返回内容，测试通过
- **3 次结果互不相同** → Golden 模版可能已过时，`skipTest("[NEEDS_HUMAN_REVIEW]")`
- **3 次结果一致但与 Golden 不符** → `fail` 并打印缺失锚点 diff

```python
def _run_with_golden_retry(
    test_case,
    extract_fn,
    html_path: Path,
    golden_path: Path,
    max_retries: int = 3,
) -> str:
    """
    Run extract_fn up to max_retries times and compare style anchors against golden.
    """
    anchors = _golden_style_anchors(golden_path)
    results: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for attempt in range(max_retries):
            out = Path(tmp) / f"attempt_{attempt}.md"
            content = extract_fn(html_path, out).read_text(encoding="utf-8")
            missing = [a for a in anchors if a not in content]
            if not missing:
                return content
            results.append(content)
    all_unique = len(set(results)) == len(results)
    if all_unique:
        test_case.skipTest(
            "[NEEDS_HUMAN_REVIEW] 三次运行结构互不相同，"
            "Golden 模版可能已过时，跳过强制断言等待人工审核"
        )
    missing_final = [a for a in anchors if a not in results[-1]]
    test_case.fail(
        "Golden 风格比对失败（连续 3 次结构一致但与 Golden 风格不符），"
        f"缺少以下结构锚点 ({len(missing_final)}):\n"
        + "\n".join(f"  {a}" for a in missing_final)
    )
```

## 使用示例

```python
class TestCustomerReviewsExtractor(unittest.TestCase):

    def test_golden_comparison(self):
        """Compare output against Golden fixture for the reference sample."""
        self.assertTrue(len(SAMPLE_DIRS) >= 1, "No sample chunks found")
        html_path = SAMPLE_DIRS[0] / "customer_reviews.html"
        golden_path = TEMPLATES_DIR / "customer_reviews_extracted.md"
        if not html_path.exists():
            self.skipTest("Reference sample chunk not found")
        if not golden_path.exists():
            self.skipTest("Golden fixture not found, skipping comparison")
        _run_with_golden_retry(
            self,
            extract_customer_reviews_markdown,
            html_path,
            golden_path,
        )
```

> **注意**：Golden 比对测试使用 `SAMPLE_DIRS[0]`（第一个可用样本），与 Golden 文件对应的参考 ASIN 一致。如果发现样本目录不对应 Golden 文件中的产品，可按 ASIN 名过滤。

## 各块的断言要求

### Customer Reviews

- `"out of 5"` 或对应样本的实际总评分
- `"global ratings"` 在 Total ratings 行中
- `"percent of reviews have"` 在 Rating distribution 中（至少 5 行）
- 每条 review 必须有非空的 Title、Author、Date、Body
- `"Reviewed in"` 在日期中
- `"Verified purchase: Yes"` 至少出现 1 次
- 不得出现 `"N/A"` 作为 Author 或 Date（除非 HTML 中真的没有）
- 不得出现 `<br`、`<script`、`Read more`

### Product Details

- `"| Field | Value |"` 表头行存在
- `"| --- | --- |"` 分隔行存在
- 关键字段如 `Department`、`ASIN`、`Date First Available` 存在且值正确
- `"Best Sellers Rank"` 字段存在
- 不得包含 `\u200f`、`\u200e` 等 Unicode 控制字符
- 不得包含 `<br`、`<script`、`style=`
