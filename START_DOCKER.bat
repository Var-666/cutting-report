@echo off
setlocal
cd /d "%~dp0"
where docker >nul 2>nul
if errorlevel 1 (
    echo Docker was not found. Start Docker Desktop in Linux containers mode.
    goto failed
)
docker info >nul 2>nul
if errorlevel 1 (
    echo Docker is not running. Start Docker Desktop and try again.
    goto failed
)
docker compose version >nul 2>nul
if errorlevel 1 (
    echo Docker Compose v2 is required.
    goto failed
)
if not exist ".env" (
    copy /-Y ".env.example" ".env" >nul
    if errorlevel 1 goto failed
    echo Created .env. Configure your database and energy API before starting.
    echo See README-DOCKER.md. Save the file, then run START_DOCKER.bat again.
    notepad ".env"
    pause
    exit /b 0
)
echo Building and starting cutting-report. The initial build needs Internet access.
docker compose up -d --build --wait --wait-timeout 180
if errorlevel 1 goto failed
docker compose ps
echo Open http://127.0.0.1:8000 or use the DOCKER_PORT configured in .env.
echo You can close this window. Docker keeps the application running.
pause
exit /b 0

:failed
echo Deployment did not complete. Check the error above.
echo For application logs: docker compose logs --tail 100 app
echo Do not delete .env or Docker volumes when troubleshooting.
pause
exit /b 1
