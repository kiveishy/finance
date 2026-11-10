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

:: Check if virtual environment exists, create if not
if not exist "%VENV_PYTHON%" (
    echo Virtual environment not found at %VENV_PYTHON%
    echo Creating virtual environment...
    python -m venv "%PROJECT_ROOT%\yahoodb"
    if errorlevel 1 (
        echo Error: Failed to create virtual environment.
        exit /b 1
    )
    echo Virtual environment created successfully.

    echo Installing dependencies from requirements.txt...
    "%PROJECT_ROOT%\yahoodb\Scripts\pip.exe" install --upgrade pip
    "%PROJECT_ROOT%\yahoodb\Scripts\pip.exe" install -r "%SCRIPT_DIR%requirements.txt"
    if errorlevel 1 (
        echo Error: Failed to install dependencies.
        exit /b 1
    )
    echo Dependencies installed successfully.
)

:: Change to script directory
cd /d "%SCRIPT_DIR%"

:: Set UTF-8 for Python output
set PYTHONUTF8=1

echo.
echo +-----------------------------------------------------------+
echo ^|           Stock Data Service v0.0.0.5                    ^|
echo ^|   Flask Web Application for Yahoo Finance Data           ^|
echo +-----------------------------------------------------------+
echo.
echo Starting Flask Web Server...
echo Using Python: %VENV_PYTHON%
echo.
echo Dashboard will be available at: http://localhost:5000
echo.

"%VENV_PYTHON%" main.py

endlocal
