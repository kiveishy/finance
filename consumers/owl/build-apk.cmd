@echo off
rem Owl - build the Android APK (dist\android\owl-debug.apk) from the web UI via Capacitor.
rem Requirements: Node.js 20+, JDK 21, Android SDK (ANDROID_HOME / ANDROID_SDK_ROOT or %LOCALAPPDATA%\Android\Sdk).
rem   build-apk.cmd            debug APK (installable for testing)
rem   build-apk.cmd release    unsigned release APK (sign with apksigner before distribution)
setlocal EnableExtensions

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "MOBILE_DIR=%APP_DIR%\mobile"
set "OUT_DIR=%APP_DIR%\dist\android"
set "VARIANT=debug"
if /i "%~1"=="release" set "VARIANT=release"

where node >nul 2>nul
if errorlevel 1 (
    echo [apk] ERROR: Node.js not found. Install Node.js 20+ from https://nodejs.org
    exit /b 1
)
where java >nul 2>nul
if errorlevel 1 (
    echo [apk] ERROR: Java not found. Install JDK 21 and set JAVA_HOME.
    exit /b 1
)
if not defined ANDROID_HOME if defined ANDROID_SDK_ROOT set "ANDROID_HOME=%ANDROID_SDK_ROOT%"
if not defined ANDROID_HOME if exist "%LOCALAPPDATA%\Android\Sdk" set "ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk"
if not defined ANDROID_HOME (
    echo [apk] ERROR: Android SDK not found. Install Android Studio or the command line tools and set ANDROID_HOME.
    exit /b 1
)
echo [apk] Android SDK: %ANDROID_HOME%

pushd "%MOBILE_DIR%"
call npm install --no-audit --no-fund
if errorlevel 1 goto failed
if exist android goto sync
call npx cap add android
if errorlevel 1 goto failed
:sync
call npx cap sync android
if errorlevel 1 goto failed

pushd android
if /i "%VARIANT%"=="release" (call gradlew.bat assembleRelease) else (call gradlew.bat assembleDebug)
if errorlevel 1 (
    popd
    goto failed
)
popd
popd

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
if /i "%VARIANT%"=="release" (
    copy /y "%MOBILE_DIR%\android\app\build\outputs\apk\release\app-release-unsigned.apk" "%OUT_DIR%\owl-release-unsigned.apk" >nul
) else (
    copy /y "%MOBILE_DIR%\android\app\build\outputs\apk\debug\app-debug.apk" "%OUT_DIR%\owl-debug.apk" >nul
)
if errorlevel 1 (
    echo [apk] ERROR: APK not found in the Gradle output
    exit /b 1
)
echo [apk] OK: APK written to %OUT_DIR%
exit /b 0

:failed
popd
echo [apk] ERROR: Android build failed
exit /b 1
