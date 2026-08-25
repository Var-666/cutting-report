@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python launcher not found.
  echo Please install 64-bit Python 3.11 or 3.12 and enable Add Python to PATH.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)

echo Installing application dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt -r backend\requirements-sqlserver.txt
if errorlevel 1 goto :failed

if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo Created .env. Edit it before starting the application.
) else (
  echo Existing .env was preserved.
)

echo.
echo Installation completed.
echo Edit .env, then run start_windows.bat.
pause
exit /b 0

:failed
echo.
echo [ERROR] Installation failed. Review the output above.
pause
exit /b 1
