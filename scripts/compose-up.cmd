@echo off
REM Start the whole stack (mysql + backend + nginx frontend), then open http://localhost:8080
cd /d "%~dp0.." || exit /b 1
docker compose -f deploy/docker-compose.yml up -d --build
