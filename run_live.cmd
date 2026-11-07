@echo off
rem Nasiya Pro live runner for Windows. Uses uv's Python (works even when "python"
rem is not installed or is only the Microsoft Store alias).
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if %errorlevel%==0 (
  uv run --no-project python run_live.py %*
  exit /b %errorlevel%
)
python run_live.py %*
exit /b %errorlevel%
