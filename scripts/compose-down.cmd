@echo off
REM Stop the whole stack (data volumes are kept)
cd /d "%~dp0.." || exit /b 1
docker compose -f deploy/docker-compose.yml down
