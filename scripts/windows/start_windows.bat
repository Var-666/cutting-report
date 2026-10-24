@echo off
setlocal
cd /d "%~dp0\..\.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Application is not installed. Run scripts\windows\install_windows.bat first.
  pause
  exit /b 1
)

if not exist ".env" (
  echo [ERROR] .env does not exist. Run scripts\windows\install_windows.bat and configure .env first.
  pause
  exit /b 1
)

if /I "%~1"=="worker" (
  set "PYTHONPATH=%CD%\backend"
  ".venv\Scripts\python.exe" -m app.worker
  goto finished
)

echo Start background tasks in another window: scripts\windows\start_windows.bat worker
echo Starting Cutting Report at http://0.0.0.0:8000
echo Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 --workers 1

:finished
if errorlevel 1 (
  echo.
  echo [ERROR] Application stopped because of an error.
  pause
)
