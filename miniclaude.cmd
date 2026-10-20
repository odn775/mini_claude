@echo off
REM Mini Claude CLI launcher (entry point: backend/app/entries/cli.py)
cd /d "%~dp0backend"
set PYTHONIOENCODING=utf-8
python -m app.entries.cli %*
