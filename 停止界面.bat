@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".server.pid" (
  echo No .server.pid found. The server may not be running.
  pause
  goto :eof
)
set /p PID=<.server.pid
echo Stopping PID %PID%
taskkill /F /PID %PID% >nul 2>&1
del /q ".server.pid" >nul 2>&1
timeout /t 1 >nul
echo Done.
pause
