@echo off
setlocal

:: Set UTF-8 code page for Unicode characters
chcp 65001 > nul

:: Set script directory and project root
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%..\.."
set "PROJECT_ROOT=%CD%"
popd

:: Virtual environment Python path (Windows: Scripts\python.exe)
set "VENV_PYTHON=%PROJECT_ROOT%\yahoodb\Scripts\python.exe"

:: Check if virtual environment exists
if not exist "%VENV_PYTHON%" (
    echo Error: Virtual environment not found at %VENV_PYTHON%
    echo Please create the virtual environment first:
    echo   "C:\workspace\vms\shared\workspace\common\developement\python\3.14\python.exe" -m venv "%PROJECT_ROOT%\yahoodb"
    echo   "%PROJECT_ROOT%\yahoodb\Scripts\pip.exe" install -r "%SCRIPT_DIR%requirements.txt"
    exit /b 1
)

:: Change to script directory
cd /d "%SCRIPT_DIR%"

:: Set UTF-8 for Python output
set PYTHONUTF8=1

echo.
echo +-----------------------------------------------------------+
echo ^|           NYSE Ticker Sync Script v0.0.0.5               ^|
echo ^|   Download and sync NYSE tickers to local database       ^|
echo +-----------------------------------------------------------+
echo.
echo Using Python: %VENV_PYTHON%
echo.

"%VENV_PYTHON%" sync_nyse.py %*

endlocal
