#!/usr/bin/env python3
"""
Image Downloader — 通用图片下载工具。

支持两种输入模式：
    1) 下载计划 JSON（download plan）
    2) 命令行直接传入 dir_name + URLs

Usage:
    python downloader.py --download-plan plan.json
    python downloader.py --output-dir out_dir \\
        --product "001_B0XXXXX" "https://..." "https://..." \\
        --product "002_B0YYYYY" "https://..."

plan.json 格式：
{
    "output_dir": "/absolute/path/to/images",
    "products": [
        {
            "dir_name": "001_B0XXXXX",
            "urls": ["https://m.media-amazon.com/images/...jpg"]
        }
    ]
}
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def url_to_filename(url: str, index: int, prefix: str = "img") -> str:
    """从 URL 生成文件名。"""
    path_part = url.split("?")[0]
    if "." in path_part.split("/")[-1]:
        ext = "." + path_part.split("/")[-1].rsplit(".", 1)[-1]
        ext = re.sub(r"\._[A-Z]+[\d,]+_", "", ext)
        if not ext or ext == ".":
            ext = ".jpg"
    else:
        ext = ".jpg"
    return f"{prefix}_{index:03d}{ext}"


# ---------------------------------------------------------------------------
# Single image download
# ---------------------------------------------------------------------------

def download_image(url: str, save_path: Path, timeout: int = 30, retries: int = 2) -> bool:
    """下载单张图片，返回是否成功。"""
    for attempt in range(retries + 1):
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://www.amazon.com/",
            })

            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                data = resp.read()
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_bytes(data)
                return True

        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ssl.SSLError) as e:
            if attempt < retries:
                time.sleep(1.0)
                continue
            print(f"  [FAIL] {url} -> {e}", file=sys.stderr)
            return False


# ---------------------------------------------------------------------------
# Plan loading & validation
# ---------------------------------------------------------------------------

def load_download_plan(plan_path: Path) -> dict:
    """读取并校验下载计划 JSON。"""
    if not plan_path.exists():
        raise FileNotFoundError(f"Plan file not found: {plan_path}")

    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    if "output_dir" not in plan or not plan["output_dir"]:
        raise ValueError("Plan JSON missing required field: 'output_dir'")
    if "products" not in plan or not plan["products"]:
        raise ValueError("Plan JSON missing or empty required field: 'products'")

    for i, prod in enumerate(plan["products"]):
        if "dir_name" not in prod or not prod["dir_name"]:
            raise ValueError(f"Product #{i} missing required field: 'dir_name'")
        if "urls" not in prod:
            raise ValueError(f"Product #{i} missing required field: 'urls'")

    return plan


def build_plan_from_cli(output_dir: str, product_args: list[list[str]]) -> dict:
    """从 CLI 参数构造下载计划。"""
    if not output_dir:
        raise ValueError("CLI mode requires --output-dir")
    if not product_args:
        raise ValueError("CLI mode requires at least one --product")

    products: list[dict] = []
    for i, row in enumerate(product_args):
        if len(row) < 2:
            raise ValueError(
                f"--product entry #{i} must include dir_name and at least one url"
            )
        products.append({
            "dir_name": row[0],
            "urls": row[1:],
        })

    return {"output_dir": output_dir, "products": products}


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------

def execute_download_plan(
    plan: dict,
    file_prefix: str = "img",
    delay_between: float = 0.3,
) -> dict:
    """执行下载计划，返回并持久化 download_manifest。

    Args:
        plan: 已校验的下载计划 dict。
        file_prefix: 图片文件名前缀（如 "listing_img" / "aplus_img"）。
        delay_between: 同一产品内图片下载间隔（秒）。

    Returns:
        download_manifest dict。
    """
    output_dir = Path(plan["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    products_results: list[dict] = []

    for prod in plan["products"]:
        dir_name = prod["dir_name"]
        urls = prod["urls"]

        if not urls:
            products_results.append({
                "dir_name": dir_name,
                "status": "NO_IMAGES",
                "image_count": 0,
                "success_count": 0,
                "images": [],
            })
            continue

        print(f"  [{dir_name}] Downloading {len(urls)} image(s)...")

        product_output = output_dir / dir_name
        product_output.mkdir(parents=True, exist_ok=True)

        images_info: list[dict] = []
        success_count = 0

        for img_idx, url in enumerate(urls, start=1):
            filename = url_to_filename(url, img_idx, prefix=file_prefix)
            save_path = product_output / filename
            print(f"    [{img_idx}/{len(urls)}] {filename}")

            ok = download_image(url, save_path)
            images_info.append({
                "index": img_idx,
                "url": url,
                "filename": filename,
                "local_path": str(save_path),
                "status": "OK" if ok else "FAILED",
            })
            if ok:
                success_count += 1

            if img_idx < len(urls):
                time.sleep(delay_between)

        if success_count == len(urls):
            status = "SUCCESS"
        elif success_count > 0:
            status = "PARTIAL"
        else:
            status = "ALL_FAILED"

        products_results.append({
            "dir_name": dir_name,
            "status": status,
            "image_count": len(urls),
            "success_count": success_count,
            "images": images_info,
        })

    manifest = {
        "output_dir": str(output_dir),
        "total_images": sum(r["image_count"] for r in products_results),
        "total_success": sum(r.get("success_count", 0) for r in products_results),
        "products": products_results,
    }

    manifest_path = output_dir / "download_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    total = manifest["total_images"]
    ok = manifest["total_success"]
    print(f"\n{'='*50}")
    print(f"Done! {ok}/{total} images downloaded.")
    print(f"Output: {output_dir}")
    print(f"Manifest: {manifest_path}")

    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Image Downloader — 通用图片下载工具",
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--download-plan",
        type=Path,
        help="Path to the download plan JSON file",
    )
    input_group.add_argument(
        "--output-dir",
        type=str,
        help="Output dir for direct CLI mode",
    )
    parser.add_argument(
        "--product",
        action="append",
        nargs="+",
        metavar="ITEM",
        help="Direct mode: --product <dir_name> <url1> [url2 ...]. Repeatable.",
    )
    parser.add_argument(
        "--file-prefix",
        default="img",
        help="Filename prefix for downloaded images (default: img)",
    )
    args = parser.parse_args()

    if args.download_plan:
        try:
            plan = load_download_plan(args.download_plan)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Error: Invalid plan file — {e}", file=sys.stderr)
            sys.exit(2)
    else:
        try:
            plan = build_plan_from_cli(
                output_dir=args.output_dir,
                product_args=args.product,
            )
        except ValueError as e:
            print(f"Error: Invalid CLI input — {e}", file=sys.stderr)
            sys.exit(3)

    execute_download_plan(plan, file_prefix=args.file_prefix)


if __name__ == "__main__":
    main()
