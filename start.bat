@echo off
echo ============================================================
echo   Amazon Bestsellers Summary — Web App
echo ============================================================
echo.

REM Start backend in a new terminal window
echo [1/2] Starting backend (FastAPI @ http://localhost:8000) ...
start "Backend" cmd /k "cd /d %~dp0backend && C:\Python314\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

REM Give backend a moment to start
timeout /t 2 /nobreak >nul

REM Start frontend in a new terminal window
echo [2/2] Starting frontend (Vite @ http://localhost:5173) ...
start "Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo Both services starting in separate windows.
echo Open http://localhost:5173 in your browser.
echo.
pause
