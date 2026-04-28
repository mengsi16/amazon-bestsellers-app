#!/usr/bin/env bash
# Amazon Bestsellers Summary Agent — Unix/macOS Setup
set -e

echo "============================================================"
echo "  Amazon Bestsellers Summary Agent - Setup (Unix/macOS)"
echo "============================================================"
echo

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 not found. Please install Python 3.10+ from https://www.python.org/"
    exit 1
fi

# Check claude CLI
if ! command -v claude &>/dev/null; then
    echo "[ERROR] Claude Code CLI not found."
    echo "        Install from: https://code.claude.com/cli"
    exit 1
fi

echo "[1/2] Installing Python dependencies..."
pip3 install -r scraper/requirements.txt

echo
echo "[2/2] Installing Playwright Chromium browser..."
playwright install chromium

echo
echo "============================================================"
echo "  Setup complete!"
echo
echo "  Usage:"
echo "    python3 run.py https://www.amazon.com/gp/bestsellers/beauty/11058221/"
echo "============================================================"
