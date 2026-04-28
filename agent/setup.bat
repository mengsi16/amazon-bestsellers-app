@echo off
REM Amazon Bestsellers Summary Agent — Windows Setup
setlocal

echo ============================================================
echo   Amazon Bestsellers Summary Agent - Setup (Windows)
echo ============================================================
echo.

REM Check Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found. Please install Python 3.10+ from https://www.python.org/
    exit /b 1
)

REM Check claude CLI
claude --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Claude Code CLI not found.
    echo         Install from: https://code.claude.com/cli
    exit /b 1
)

echo [1/2] Installing Python dependencies...
pip install -r scraper\requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] pip install failed. Check that Python 3.10+ and pip are available.
    exit /b 1
)

echo.
echo [2/2] Installing Playwright Chromium browser...
playwright install chromium
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] playwright install chromium failed.
    exit /b 1
)

echo.
echo ============================================================
echo   Setup complete!
echo.
echo   Usage:
echo     python run.py https://www.amazon.com/gp/bestsellers/beauty/11058221/
echo ============================================================
endlocal
