@echo off
REM Mini Claude CLI launcher (entry point: backend/app/entries/cli.py)
cd /d "%~dp0backend"
set PYTHONIOENCODING=utf-8
REM Share the repo data dir with the web container: same index, same docs
set MINI_CLAUDE_DATA_DIR=%~dp0data
set MINI_CLAUDE_KNOWLEDGE_DIR=%~dp0docs\departments
python -m app.entries.cli %*
