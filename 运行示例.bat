@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"

set "RUN="
python -c "import numpy,yaml" >nul 2>nul && set "RUN=python"
if not defined RUN py -3 -c "import numpy,yaml" >nul 2>nul && set "RUN=py -3"
if not defined RUN python3 -c "import numpy,yaml" >nul 2>nul && set "RUN=python3"
if not defined RUN goto nodeps

set "CFG=%~1"
if "%CFG%"=="" set "CFG=chainlab\examples\amp8_chain.yaml"

if not exist "out" mkdir "out"
%RUN% -m chainlab.cli run "%CFG%" --html "out\report.html"
if errorlevel 1 goto failed

start "" "out\report.html"
goto :eof

:failed
echo.
echo [chainlab] Run failed. See the error message above.
pause
goto :eof

:nodeps
echo.
echo [chainlab] Needs Python 3.11+ with numpy and pyyaml.
echo Run "Open UI.bat" first to install dependencies.
pause
goto :eof
