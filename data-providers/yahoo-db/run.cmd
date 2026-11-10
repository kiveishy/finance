@echo off
rem Stock Data Service (yahoo-db) - run from source (Windows).
rem Creates/activates the venv, validates/installs dependencies, then starts the Flask dashboard + API.
rem   run.cmd                      start the service (http://localhost:5000)
rem   run.cmd --setup-only         prepare the environment only
rem Environment overrides:
rem   YAHOODB_PYTHON        base interpreter used to create the venv
rem   YAHOODB_VENV          venv directory (default .venv-windows)
rem   YAHOODB_REQUIREMENTS  requirements file (default requirements.txt)
setlocal EnableExtensions

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
if not defined YAHOODB_VENV set "YAHOODB_VENV=%APP_DIR%\.venv-windows"
if not defined YAHOODB_REQUIREMENTS set "YAHOODB_REQUIREMENTS=%APP_DIR%\requirements.txt"
rem venvs hard-code their own path: a moved/renamed or incomplete venv must be recreated.
set "VENV_MARKER=%YAHOODB_VENV%\.yahoo-db-location"
set "VENV_AT="
if exist "%VENV_MARKER%" set /p VENV_AT=<"%VENV_MARKER%"
if not exist "%YAHOODB_VENV%\pyvenv.cfg" goto venv_create
if not exist "%YAHOODB_VENV%\Scripts\python.exe" goto venv_create
if /i "%VENV_AT%"=="%YAHOODB_VENV%" goto venv_ready

:venv_create
if defined YAHOODB_PYTHON goto python_selected
set "YAHOODB_PYTHON=%APP_DIR%\..\..\..\..\python\3.14\python.exe"
if exist "%YAHOODB_PYTHON%" goto python_selected
set "YAHOODB_PYTHON=python"
:python_selected

if not exist "%YAHOODB_VENV%" goto venv_args_done
if exist "%YAHOODB_VENV%\pyvenv.cfg" goto venv_replace
if exist "%YAHOODB_VENV%\Scripts\python.exe" goto venv_replace
echo [run] ERROR: %YAHOODB_VENV% exists but is not a virtual environment. Remove it or set YAHOODB_VENV.
exit /b 1
:venv_replace
echo [run] Virtual environment %YAHOODB_VENV% is incomplete or was moved, recreating it
rem Move aside instead of venv --clear: in-place recreation left a truncated pip on shared folders.
set "VENV_OLD=%YAHOODB_VENV%.broken-%RANDOM%"
move "%YAHOODB_VENV%" "%VENV_OLD%" >nul
if errorlevel 1 (
    echo [run] ERROR: could not move the old virtual environment aside. Close programs using it and retry.
    exit /b 1
)
rmdir /s /q "%VENV_OLD%" 2>nul
:venv_args_done

echo [run] Creating virtual environment %YAHOODB_VENV%
echo [run] Base interpreter: %YAHOODB_PYTHON%
"%YAHOODB_PYTHON%" -m venv "%YAHOODB_VENV%"
if errorlevel 1 (
    echo [run] ERROR: could not create the virtual environment. Set YAHOODB_PYTHON to a Python 3.10+ interpreter.
    exit /b 1
)
> "%VENV_MARKER%" echo %YAHOODB_VENV%

:venv_ready
call "%YAHOODB_VENV%\Scripts\activate.bat"
if errorlevel 1 (
    echo [run] ERROR: could not activate %YAHOODB_VENV%
    exit /b 1
)

python "%APP_DIR%\tools\ensure_env.py" "%YAHOODB_REQUIREMENTS%"
if errorlevel 1 (
    echo [run] ERROR: dependency validation failed
    exit /b 1
)

if /i "%~1"=="--setup-only" (
    echo [run] Environment ready: %YAHOODB_VENV%
    exit /b 0
)

set "PYTHONUTF8=1"
rem main.py reads config.json from the working directory.
cd /d "%APP_DIR%"
python "%APP_DIR%\main.py" %*
exit /b %errorlevel%
