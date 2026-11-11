@echo off
rem snp-monitor - run from source (Windows).
rem Creates/activates .venv-windows, validates/installs dependencies, then starts the web server.
rem   run.cmd                  start snp-monitor and open the UI in Chrome
rem   run.cmd --no-browser     start without opening a browser
rem   run.cmd --setup-only     prepare the environment only
rem Environment overrides:
rem   SNP_PYTHON        base interpreter used to create the venv (default ..\..\..\..\python\3.14\python.exe)
rem   SNP_VENV          venv directory (default .venv-windows)
rem   SNP_REQUIREMENTS  requirements file (default requirements.txt)
setlocal EnableExtensions

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
if not defined SNP_VENV set "SNP_VENV=%APP_DIR%\.venv-windows"
if not defined SNP_REQUIREMENTS set "SNP_REQUIREMENTS=%APP_DIR%\requirements.txt"
rem venvs hard-code their own path: a moved/renamed or incomplete venv must be recreated.
set "VENV_MARKER=%SNP_VENV%\.snp-monitor-location"
set "VENV_AT="
if exist "%VENV_MARKER%" set /p VENV_AT=<"%VENV_MARKER%"
if not exist "%SNP_VENV%\pyvenv.cfg" goto venv_create
if not exist "%SNP_VENV%\Scripts\python.exe" goto venv_create
if /i "%VENV_AT%"=="%SNP_VENV%" goto venv_ready

:venv_create
if defined SNP_PYTHON goto python_selected
set "SNP_PYTHON=%APP_DIR%\..\..\..\..\python\3.14\python.exe"
if exist "%SNP_PYTHON%" goto python_selected
set "SNP_PYTHON=python"
:python_selected

if not exist "%SNP_VENV%" goto venv_args_done
if exist "%SNP_VENV%\pyvenv.cfg" goto venv_replace
if exist "%SNP_VENV%\Scripts\python.exe" goto venv_replace
echo [run] ERROR: %SNP_VENV% exists but is not a virtual environment. Remove it or set SNP_VENV.
exit /b 1
:venv_replace
echo [run] Virtual environment %SNP_VENV% is incomplete or was moved, recreating it
rem Move aside instead of venv --clear: in-place recreation left a truncated pip on shared folders.
set "VENV_OLD=%SNP_VENV%.broken-%RANDOM%"
move "%SNP_VENV%" "%VENV_OLD%" >nul
if errorlevel 1 (
    echo [run] ERROR: could not move the old virtual environment aside. Close programs using it and retry.
    exit /b 1
)
rmdir /s /q "%VENV_OLD%" 2>nul
:venv_args_done

echo [run] Creating virtual environment %SNP_VENV%
echo [run] Base interpreter: %SNP_PYTHON%
"%SNP_PYTHON%" -m venv "%SNP_VENV%"
if errorlevel 1 (
    echo [run] ERROR: could not create the virtual environment. Set SNP_PYTHON to a Python 3.10+ interpreter.
    exit /b 1
)
> "%VENV_MARKER%" echo %SNP_VENV%

:venv_ready
call "%SNP_VENV%\Scripts\activate.bat"
if errorlevel 1 (
    echo [run] ERROR: could not activate %SNP_VENV%
    exit /b 1
)

python "%APP_DIR%\tools\ensure_env.py" "%SNP_REQUIREMENTS%"
if errorlevel 1 (
    echo [run] ERROR: dependency validation failed
    exit /b 1
)

if /i "%~1"=="--setup-only" (
    echo [run] Environment ready: %SNP_VENV%
    exit /b 0
)

set "PYTHONUTF8=1"
python "%APP_DIR%\main.py" %*
exit /b %errorlevel%
