#!/usr/bin/env python3
"""
Amazon Bestsellers Summary Agent — CLI entry point

Wraps the `claude -p` non-interactive mode so users can run the full
analysis pipeline with a single command, without touching Claude Code's
interactive shell directly.

Usage:
    python run.py <bestsellers_url> [options]

Examples:
    python run.py https://www.amazon.com/gp/bestsellers/beauty/11058221/
    python run.py https://www.amazon.com/gp/bestsellers/fashion/1040658/
    python run.py https://www.amazon.com/gp/bestsellers/home-garden/3744541/ --model claude-opus-4-5
"""

import argparse
import os
import subprocess
import sys

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT = "amazon-bestsellers-summary:amazon-bestsellers-orchestrator"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Amazon Bestsellers Analysis — fully automated pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py https://www.amazon.com/gp/bestsellers/beauty/11058221/
  python run.py https://www.amazon.com/gp/bestsellers/fashion/1040658/
  python run.py https://www.amazon.com/gp/bestsellers/home-garden/3744541/ --model claude-opus-4-5
        """,
    )
    parser.add_argument(
        "url",
        help="Full Amazon Bestsellers URL (must include category slug + Browse Node ID, e.g. /gp/bestsellers/beauty/11058221/)",
    )
    parser.add_argument(
        "--plugin-dir",
        default=PLUGIN_DIR,
        metavar="DIR",
        help=f"Plugin root directory containing .claude-plugin/plugin.json (default: auto-detected as {PLUGIN_DIR})",
    )
    parser.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="Override Claude model (e.g. claude-sonnet-4-5, claude-opus-4-5). Defaults to model set in orchestrator agent.",
    )
    args = parser.parse_args()

    prompt = f"分析这个类目的 Bestsellers Top50：{args.url}"

    cmd = [
        "claude",
        "-p", prompt,
        "--plugin-dir", args.plugin_dir,
        "--agent", AGENT,
        "--dangerously-skip-permissions",
    ]
    if args.model:
        cmd.extend(["--model", args.model])

    _print_banner(args)

    try:
        result = subprocess.run(cmd, cwd=os.getcwd())
        sys.exit(result.returncode)
    except FileNotFoundError:
        print(
            "\n[ERROR] 'claude' command not found.\n"
            "Please install Claude Code CLI first: https://code.claude.com/cli\n",
            file=sys.stderr,
        )
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Analysis cancelled by user.", file=sys.stderr)
        sys.exit(130)


def _print_banner(args: argparse.Namespace) -> None:
    sep = "=" * 62
    print(sep, file=sys.stderr)
    print("  Amazon Bestsellers Summary Agent", file=sys.stderr)
    print(sep, file=sys.stderr)
    print(f"  URL        : {args.url}", file=sys.stderr)
    print(f"  Plugin Dir : {args.plugin_dir}", file=sys.stderr)
    if args.model:
        print(f"  Model      : {args.model}", file=sys.stderr)
    print(f"  Estimated  : 30–90 min  (crawl + chunk + analyze)", file=sys.stderr)
    print(sep, file=sys.stderr)
    print(file=sys.stderr)


if __name__ == "__main__":
    main()
