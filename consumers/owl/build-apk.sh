#!/usr/bin/env bash
# Owl - build the Android APK (dist/android/owl-debug.apk) from the web UI via Capacitor.
# Requirements: Node.js 20+, JDK 21, Android SDK (ANDROID_HOME / ANDROID_SDK_ROOT or ~/Android/Sdk).
#   ./build-apk.sh            debug APK (installable for testing)
#   ./build-apk.sh release    unsigned release APK (sign with apksigner before distribution)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOBILE_DIR="$APP_DIR/mobile"
OUT_DIR="$APP_DIR/dist/android"
VARIANT="${1:-debug}"

command -v node >/dev/null || { echo "[apk] ERROR: Node.js not found (install Node.js 20+)" >&2; exit 1; }
command -v java >/dev/null || { echo "[apk] ERROR: Java not found (install JDK 21)" >&2; exit 1; }
export ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}"
[[ -d "$ANDROID_HOME" ]] || { echo "[apk] ERROR: Android SDK not found; set ANDROID_HOME" >&2; exit 1; }
echo "[apk] Android SDK: $ANDROID_HOME"

cd "$MOBILE_DIR"
npm install --no-audit --no-fund
[[ -d android ]] || npx cap add android
npx cap sync android

cd android
chmod +x gradlew
if [[ "$VARIANT" == "release" ]]; then
    ./gradlew assembleRelease
    src="app/build/outputs/apk/release/app-release-unsigned.apk"; dst="owl-release-unsigned.apk"
else
    ./gradlew assembleDebug
    src="app/build/outputs/apk/debug/app-debug.apk"; dst="owl-debug.apk"
fi
mkdir -p "$OUT_DIR"
cp "$src" "$OUT_DIR/$dst"
echo "[apk] OK: $OUT_DIR/$dst"
