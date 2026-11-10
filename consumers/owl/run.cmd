@echo off
rem Owl - run from source (Windows).
rem Creates/activates .venv-windows, validates/installs dependencies, then starts the web server.
rem   run.cmd                  start owl and open the browser
rem   run.cmd --no-browser     start without opening a browser
rem   run.cmd --setup-only     prepare the environment only
rem Environment overrides:
rem   FINANCE_PYTHON      base interpreter used to create the venv
rem   OWL_VENV            venv directory (default .venv-windows)
rem   OWL_AGGREGATOR_DIR  finance-aggregator sources (default ..\..\data-providers\finance-aggregator)
setlocal EnableExtensions

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
if not defined OWL_AGGREGATOR_DIR set "OWL_AGGREGATOR_DIR=%APP_DIR%\..\..\data-providers\finance-aggregator"
if not exist "%OWL_AGGREGATOR_DIR%\run.cmd" (
    echo [owl] ERROR: finance-aggregator not found at %OWL_AGGREGATOR_DIR%. Set OWL_AGGREGATOR_DIR.
    exit /b 1
)
if not defined OWL_VENV set "OWL_VENV=%APP_DIR%\.venv-windows"
if not defined OWL_REQUIREMENTS set "OWL_REQUIREMENTS=%APP_DIR%\requirements.txt"

rem The aggregator's run script owns venv creation, repair and dependency validation.
set "FINANCE_VENV=%OWL_VENV%"
set "FINANCE_REQUIREMENTS=%OWL_REQUIREMENTS%"
call "%OWL_AGGREGATOR_DIR%\run.cmd" --setup-only
if errorlevel 1 (
    echo [owl] ERROR: environment setup failed
    exit /b 1
)

if /i "%~1"=="--setup-only" exit /b 0

call "%OWL_VENV%\Scripts\activate.bat"
set "PYTHONUTF8=1"
python "%APP_DIR%\main.py" %*
exit /b %errorlevel%
