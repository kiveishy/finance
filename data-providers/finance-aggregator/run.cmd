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
rem venvs hard-code their own path: a moved/renamed or incomplete venv must be recreated.
set "VENV_MARKER=%FINANCE_VENV%\.finance-aggregator-location"
set "VENV_AT="
if exist "%VENV_MARKER%" set /p VENV_AT=<"%VENV_MARKER%"
if not exist "%FINANCE_VENV%\pyvenv.cfg" goto venv_create
if not exist "%FINANCE_VENV%\Scripts\python.exe" goto venv_create
if /i "%VENV_AT%"=="%FINANCE_VENV%" goto venv_ready

:venv_create
if defined FINANCE_PYTHON goto python_selected
set "FINANCE_PYTHON=%APP_DIR%\..\..\..\..\python\3.14\python.exe"
if exist "%FINANCE_PYTHON%" goto python_selected
set "FINANCE_PYTHON=python"
:python_selected

if not exist "%FINANCE_VENV%" goto venv_args_done
if exist "%FINANCE_VENV%\pyvenv.cfg" goto venv_replace
if exist "%FINANCE_VENV%\Scripts\python.exe" goto venv_replace
echo [run] ERROR: %FINANCE_VENV% exists but is not a virtual environment. Remove it or set FINANCE_VENV.
exit /b 1
:venv_replace
echo [run] Virtual environment %FINANCE_VENV% is incomplete or was moved, recreating it
rem Move aside instead of venv --clear: in-place recreation left a truncated pip on shared folders.
set "VENV_OLD=%FINANCE_VENV%.broken-%RANDOM%"
move "%FINANCE_VENV%" "%VENV_OLD%" >nul
if errorlevel 1 (
    echo [run] ERROR: could not move the old virtual environment aside. Close programs using it and retry.
    exit /b 1
)
rmdir /s /q "%VENV_OLD%" 2>nul
:venv_args_done

echo [run] Creating virtual environment %FINANCE_VENV%
echo [run] Base interpreter: %FINANCE_PYTHON%
"%FINANCE_PYTHON%" -m venv "%FINANCE_VENV%"
if errorlevel 1 (
    echo [run] ERROR: could not create the virtual environment. Set FINANCE_PYTHON to a Python 3.10+ interpreter.
    exit /b 1
)
> "%VENV_MARKER%" echo %FINANCE_VENV%

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
