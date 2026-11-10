@echo off
rem Finance Aggregator - run from source (Windows).
rem Creates/activates the venv, validates/installs dependencies, then starts the app.
rem   run.cmd                      interactive CLI
rem   run.cmd status               one-shot command
rem   run.cmd serve                HTTP API server
rem   run.cmd --setup-only         prepare the environment only
rem Environment overrides:
rem   FINANCE_PYTHON        base interpreter used to create the venv
rem   FINANCE_VENV          venv directory (default .venv-windows)
rem   FINANCE_REQUIREMENTS  requirements file (default requirements.txt)
setlocal EnableExtensions

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
if not defined FINANCE_VENV set "FINANCE_VENV=%APP_DIR%\.venv-windows"
if not defined FINANCE_REQUIREMENTS set "FINANCE_REQUIREMENTS=%APP_DIR%\requirements.txt"

if exist "%FINANCE_VENV%\Scripts\python.exe" goto venv_ready

if defined FINANCE_PYTHON goto python_selected
set "FINANCE_PYTHON=%APP_DIR%\..\..\..\..\python\3.14\python.exe"
if exist "%FINANCE_PYTHON%" goto python_selected
set "FINANCE_PYTHON=python"
:python_selected

echo [run] Creating virtual environment %FINANCE_VENV%
echo [run] Base interpreter: %FINANCE_PYTHON%
"%FINANCE_PYTHON%" -m venv "%FINANCE_VENV%"
if errorlevel 1 (
    echo [run] ERROR: could not create the virtual environment. Set FINANCE_PYTHON to a Python 3.10+ interpreter.
    exit /b 1
)

:venv_ready
call "%FINANCE_VENV%\Scripts\activate.bat"
if errorlevel 1 (
    echo [run] ERROR: could not activate %FINANCE_VENV%
    exit /b 1
)

python "%APP_DIR%\tools\ensure_env.py" "%FINANCE_REQUIREMENTS%"
if errorlevel 1 (
    echo [run] ERROR: dependency validation failed
    exit /b 1
)

if /i "%~1"=="--setup-only" (
    echo [run] Environment ready: %FINANCE_VENV%
    exit /b 0
)

set "PYTHONUTF8=1"
python "%APP_DIR%\main.py" %*
exit /b %errorlevel%
