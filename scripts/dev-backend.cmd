@echo off
REM Start backend dev server (reload on). Port comes from backend/.env, default 8000.
cd /d "%~dp0..\backend" || exit /b 1
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
