@echo off
REM DB migration.
REM   scripts\migrate.cmd upgrade head
REM   scripts\migrate.cmd revision --autogenerate -m "message"
cd /d "%~dp0..\backend" || exit /b 1
python -m alembic %*
