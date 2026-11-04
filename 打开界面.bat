@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"

set "RUN="
python -c "import numpy,yaml" >nul 2>nul && set "RUN=python"
if not defined RUN py -3 -c "import numpy,yaml" >nul 2>nul && set "RUN=py -3"
if not defined RUN python3 -c "import numpy,yaml" >nul 2>nul && set "RUN=python3"
if defined RUN goto run

set "HASPIP="
python -c "import sys" >nul 2>nul && set "HASPIP=python"
if not defined HASPIP py -3 -c "import sys" >nul 2>nul && set "HASPIP=py -3"
if not defined HASPIP goto nopython

echo.
echo [chainlab] Python found, but numpy / pyyaml is missing.
choice /c YN /m "Install them now (pip install numpy pyyaml)"
if errorlevel 2 goto nodeps
echo Installing numpy pyyaml ...
%HASPIP% -m pip install numpy pyyaml
if errorlevel 1 goto nodeps
python -c "import numpy,yaml" >nul 2>nul && set "RUN=python"
if not defined RUN py -3 -c "import numpy,yaml" >nul 2>nul && set "RUN=py -3"
if defined RUN goto run
goto nodeps

:run
echo Starting chainlab. The URL is printed below and the browser opens automatically.
%RUN% -m chainlab.ui
if errorlevel 1 pause
goto :eof

:nopython
echo.
echo [chainlab] Python not found.
echo Install Python 3.11+ and tick "Add Python to PATH", then run this file again.
pause
goto :eof

:nodeps
echo.
echo [chainlab] Dependencies missing. Run: pip install numpy pyyaml
pause
goto :eof
