@echo off
rem NYSE ticker sync - prepares the environment via run.cmd, then runs sync_nyse.py with the given arguments.
rem Uses the same YAHOODB_* environment overrides as run.cmd.
setlocal EnableExtensions

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
if not defined YAHOODB_VENV set "YAHOODB_VENV=%APP_DIR%\.venv-windows"

call "%APP_DIR%\run.cmd" --setup-only
if errorlevel 1 exit /b 1

set "PYTHONUTF8=1"
cd /d "%APP_DIR%"
"%YAHOODB_VENV%\Scripts\python.exe" "%APP_DIR%\sync_nyse.py" %*
exit /b %errorlevel%
