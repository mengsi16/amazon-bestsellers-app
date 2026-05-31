#!/usr/bin/env python3
"""
Batch pipeline for Amazon product chunking & extraction.

Reads MCP scraper output layout:
    {products_dir}/{ASIN}/product.html
    {rankings_jsonl}  (append-only, last line = latest snapshot)

Output layout:
    {out_dir}/{rank}_{ASIN}/
    ├── manifest.json
    ├── ppd/raw/ppd.html + extract/ppd_extracted.md
    ├── customer_reviews/raw/... + extract/...
    ├── product_details/raw/... + extract/...
    └── aplus/raw/... + extract/...
    {out_dir}/global_manifest.json

Pipeline stages:
1) Chunk: split product.html → 4 block HTML files (static_chunker.py)
2) Extract: each block HTML → structured markdown (4 extractors)
3) Manifest: per-product + global manifest

CLI:
    python -m chunker.batch_run \\
        --products-dir {workspace}/products \\
        --rankings-jsonl {workspace}/categories/{browse_node_id}/rankings.jsonl \\
        --out-dir {workspace}/chunks \\
        [--limit N] [--skip-extracted]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BLOCKS = ("ppd", "customer_reviews", "product_details", "aplus")
PRODUCT_DIR_RE = re.compile(r"^(\d{3})_([A-Z0-9]{10})$")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# Rankings helpers
# ---------------------------------------------------------------------------

def load_latest_rankings(rankings_jsonl: Path) -> dict[str, int]:
    """Read the last line of rankings.jsonl and return {ASIN: rank} mapping."""
    if not rankings_jsonl.exists():
        return {}
    lines = rankings_jsonl.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        return {}
    last_line = lines[-1]
    snapshot = json.loads(last_line)
    return {asin: rank for asin, rank in snapshot.get("ranks", {}).items()}


def load_current_ranking(current_ranking_path: Path) -> dict[str, int]:
    """Read current_ranking.json and return {ASIN: rank} mapping.

    ASINs with rank = -1 have dropped out of Top50 and should be excluded
    from processing.
    """
    if not current_ranking_path.exists():
        return {}
    try:
        data = json.loads(current_ranking_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {asin: rank for asin, rank in data.get("asins", {}).items()}


def rank_to_dir_name(rank: int, asin: str) -> str:
    """Format as {zero_padded_3digit_rank}_{ASIN}, e.g. 001_B0XXXXX."""
    return f"{rank:03d}_{asin}"


# ---------------------------------------------------------------------------
# Discover ASINs from products_dir
# ---------------------------------------------------------------------------

def discover_asins(products_dir: Path) -> list[str]:
    """Find all ASIN directories that contain a valid product.html."""
    asins: list[str] = []
    for child in sorted(products_dir.iterdir()):
        if child.is_dir() and (child / "product.html").exists():
            asins.append(child.name)
    return asins


# ---------------------------------------------------------------------------
# Stage 1: Chunk (delegates to static_chunker.chunk_product_html)
# ---------------------------------------------------------------------------

def _chunk_product(asin: str, products_dir: Path, product_out_dir: Path) -> dict:
    """Run static chunker on one ASIN. Returns blocks status dict."""
    try:
        from chunker.static_chunker import chunk_product_html, write_product_manifest
    except ImportError as e:
        return {"chunk_status": "FAILED", "reason": f"chunker_module_not_available: {e}"}

    html_path = products_dir / asin / "product.html"
    result = chunk_product_html(html_path, product_out_dir)

    if result["status"] == "SKIPPED":
        return {"chunk_status": "SKIPPED", "reason": result.get("reason", "")}

    blocks_status = result.get("blocks", {})
    write_product_manifest(product_out_dir, blocks_status)
    return {"chunk_status": "SUCCESS", "blocks": blocks_status}


# ---------------------------------------------------------------------------
# Stage 2: Extract (delegates to 4 extractor modules)
# ---------------------------------------------------------------------------

def _extract_block(product_dir: Path, block: str, skip_extracted: bool) -> tuple[str, str]:
    """Run the appropriate extractor for one block. Returns (status, path)."""
    extract_dir = product_dir / block / "extract"
    out_path = extract_dir / f"{block}_extracted.md"

    if skip_extracted and out_path.exists() and out_path.stat().st_size > 0:
        return "SUCCESS", str(out_path)

    raw_html = product_dir / block / "raw" / f"{block}.html"
    if not raw_html.exists():
        return "SKIPPED", "N/A"

    extract_dir.mkdir(parents=True, exist_ok=True)

    # Try to import and run the dedicated extractor; fall back to generic
    try:
        if block == "ppd":
            from chunker.ppd_extract import extract_ppd_markdown
            extract_ppd_markdown(raw_html, out_path)
        elif block == "customer_reviews":
            from chunker.customer_reviews_extract import extract_customer_reviews_markdown
            extract_customer_reviews_markdown(raw_html, out_path)
        elif block == "product_details":
            from chunker.product_details_extract import extract_product_details_markdown
            extract_product_details_markdown(raw_html, out_path)
        elif block == "aplus":
            from chunker.aplus_extract import extract_aplus_markdown
            extract_aplus_markdown(raw_html, out_path)
        else:
            return "SKIPPED", "N/A"
        return "SUCCESS", str(out_path)
    except ImportError:
        # Extractor not yet written — leave placeholder
        return "PENDING", "N/A"


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _build_product_manifest(product_dir: Path, skip_extracted: bool) -> dict:
    manifest_path = product_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(_read_text(manifest_path))
        except (OSError, json.JSONDecodeError):
            manifest = {"product_dir": product_dir.name, "blocks": {}}
    else:
        manifest = {"product_dir": product_dir.name, "blocks": {}}

    for block in BLOCKS:
        block_state = dict(manifest.get("blocks", {}).get(block, {}))
        chunk_status = block_state.get("chunk", "NOT_FOUND")

        if chunk_status != "SUCCESS":
            block_state["extract"] = "SKIPPED"
            block_state["extract_path"] = "N/A"
        else:
            extract_status, extract_path = _extract_block(product_dir, block, skip_extracted)
            block_state["extract"] = extract_status
            block_state["extract_path"] = extract_path

        manifest.setdefault("blocks", {})[block] = block_state

    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def _to_global_row(product_manifest: dict) -> dict:
    name = product_manifest.get("product_dir", "")
    match = PRODUCT_DIR_RE.match(name)
    rank = match.group(1) if match else "N/A"
    asin = match.group(2) if match else "N/A"

    blocks = {}
    for block in BLOCKS:
        data = product_manifest.get("blocks", {}).get(block, {})
        blocks[block] = {
            "chunk": data.get("chunk", "NOT_FOUND"),
            "extract": data.get("extract", "SKIPPED"),
        }

    extract_failed = any(info["extract"] == "FAILED" for info in blocks.values())
    status = "FAILED" if extract_failed else "SUCCESS"

    return {
        "rank": rank,
        "asin": asin,
        "dir": name,
        "status": status,
        "blocks": blocks,
    }


def _write_global_manifest(chunks_dir: Path, rows: list[dict]) -> Path:
    global_manifest = {
        "total": len(rows),
        "success": sum(1 for row in rows if row["status"] == "SUCCESS"),
        "failed": sum(1 for row in rows if row["status"] == "FAILED"),
        "products": rows,
    }
    path = chunks_dir / "global_manifest.json"
    path.write_text(
        json.dumps(global_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    products_dir: Path,
    rankings_jsonl: Path,
    out_dir: Path,
    limit: int | None = None,
    skip_extracted: bool = False,
    current_ranking: Path | None = None,
) -> Path:
    """Run the full chunk → extract → manifest pipeline.

    Args:
        products_dir: Directory with MCP scraper output ({ASIN}/product.html).
        rankings_jsonl: Path to rankings.jsonl (last line = latest snapshot).
        out_dir: Output root for chunks.
        limit: Process at most N products (by rank).
        skip_extracted: Skip blocks that already have extracted markdown.
        current_ranking: Path to current_ranking.json. If provided, ASINs with
            rank = -1 (dropped out of Top50) are excluded from processing.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load rankings to determine rank per ASIN
    rankings = load_latest_rankings(rankings_jsonl)

    # 1b. If current_ranking.json is provided, filter out rank=-1 ASINs
    excluded_asins: set[str] = set()
    if current_ranking is not None:
        cr = load_current_ranking(current_ranking)
        excluded_asins = {asin for asin, rank in cr.items() if rank == -1}
        # Also overlay current_ranking ranks (more authoritative than jsonl last line)
        for asin, rank in cr.items():
            if rank >= 1:
                rankings[asin] = rank

    asins = discover_asins(products_dir)

    # Filter out excluded ASINs
    if excluded_asins:
        asins = [a for a in asins if a not in excluded_asins]

    # Sort by rank (ASINs not in rankings get rank 999 → end of list)
    asins_sorted = sorted(
        asins,
        key=lambda a: rankings.get(a, 999),
    )
    if limit is not None:
        asins_sorted = asins_sorted[:limit]

    # 2. Chunk + Extract each product
    rows: list[dict] = []
    for asin in asins_sorted:
        rank = rankings.get(asin, 999)
        dir_name = rank_to_dir_name(rank, asin)
        product_out_dir = out_dir / dir_name
        product_out_dir.mkdir(parents=True, exist_ok=True)

        # Stage 1: Chunk
        chunk_result = _chunk_product(asin, products_dir, product_out_dir)
        if chunk_result.get("chunk_status") == "SKIPPED":
            rows.append({
                "rank": f"{rank:03d}",
                "asin": asin,
                "dir": dir_name,
                "status": "SKIPPED",
                "reason": chunk_result.get("reason", ""),
                "blocks": {},
            })
            continue

        # Stage 2: Extract
        product_manifest = _build_product_manifest(product_out_dir, skip_extracted)
        rows.append(_to_global_row(product_manifest))

    # 3. Global manifest
    return _write_global_manifest(out_dir, rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run chunk/extract pipeline on MCP scraper output"
    )
    parser.add_argument(
        "--products-dir",
        type=Path,
        required=True,
        help="Directory containing {ASIN}/product.html from MCP scraper",
    )
    parser.add_argument(
        "--rankings-jsonl",
        type=Path,
        required=True,
        help="Path to categories/{browse_node_id}/rankings.jsonl",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output chunks directory",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process first N products (by rank)",
    )
    parser.add_argument(
        "--skip-extracted",
        action="store_true",
        help="Skip re-generating extracted markdown when output already exists",
    )
    parser.add_argument(
        "--current-ranking",
        type=Path,
        default=None,
        help="Path to current_ranking.json. ASINs with rank=-1 are excluded.",
    )
    args = parser.parse_args()

    manifest_path = run_pipeline(
        args.products_dir,
        args.rankings_jsonl,
        args.out_dir,
        args.limit,
        args.skip_extracted,
        args.current_ranking,
    )
    print(f"Batch pipeline complete: {manifest_path}")


if __name__ == "__main__":
    main()
