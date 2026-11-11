@echo off
rem snp-monitor - build a standalone Windows executable (dist\windows\snp-monitor.exe).
rem   build.cmd               run unit tests, then build
rem   build.cmd --skip-tests  build without running tests
setlocal EnableExtensions

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
if not defined SNP_VENV set "SNP_VENV=%APP_DIR%\.venv-windows"
set "DIST_DIR=%APP_DIR%\dist\windows"
set "WORK_DIR=%APP_DIR%\build\windows"

set "SNP_REQUIREMENTS=%APP_DIR%\requirements-dev.txt"
call "%APP_DIR%\run.cmd" --setup-only
if errorlevel 1 (
    echo [build] ERROR: environment setup failed
    exit /b 1
)
call "%SNP_VENV%\Scripts\activate.bat"

pushd "%APP_DIR%"
if /i "%~1"=="--skip-tests" goto build
echo [build] Running unit tests
python -m pytest -q -p no:cacheprovider
if errorlevel 1 goto tests_failed

:build
echo [build] Building executable into %DIST_DIR%
python -m PyInstaller --noconfirm --clean --log-level WARN --distpath "%DIST_DIR%" --workpath "%WORK_DIR%" snp-monitor.spec
if errorlevel 1 goto build_failed

echo [build] Smoke test
"%DIST_DIR%\snp-monitor.exe" --version
if errorlevel 1 goto smoke_failed

popd
echo [build] OK: %DIST_DIR%\snp-monitor.exe
exit /b 0

:tests_failed
popd
echo [build] ERROR: unit tests failed
exit /b 1

:build_failed
popd
echo [build] ERROR: PyInstaller build failed (close a running snp-monitor.exe first)
exit /b 1

:smoke_failed
popd
echo [build] ERROR: built executable failed the smoke test
exit /b 1
