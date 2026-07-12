@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python -m mini_claude.main %*
