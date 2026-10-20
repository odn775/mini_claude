@echo off
REM Start frontend dev server (Vite 5173, proxies /api to 127.0.0.1:8000)
cd /d "%~dp0..\frontend" || exit /b 1
npm run dev
