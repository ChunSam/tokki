#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Tokki"
APP_VERSION="${APP_VERSION:-1.4.1}"
APP_BUILD="${APP_BUILD:-26}"
APP_ARCHS="${APP_ARCHS:-$(uname -m)}"
BUNDLE_DIR=".build/${APP_NAME}.app"
EXECUTABLE_NAME="TokenTrackerMenuBar"
APP_ICON_SOURCE="Sources/TokenTrackerMenuBar/Resources/AppIcon.png"
APP_ICON_FILE="AppIcon.icns"
MACOS_DIR="${BUNDLE_DIR}/Contents/MacOS"
RESOURCES_DIR="${BUNDLE_DIR}/Contents/Resources"

TMP_DIRS=()
cleanup() {
  for dir in "${TMP_DIRS[@]}"; do
    rm -rf "${dir}"
  done
}
trap cleanup EXIT

read -r -a ARCHS <<< "${APP_ARCHS}"
if [[ "${#ARCHS[@]}" -eq 0 ]]; then
  echo "APP_ARCHS must contain at least one architecture" >&2
  exit 1
fi

BUILD_TMP_DIR="$(mktemp -d)"
TMP_DIRS+=("${BUILD_TMP_DIR}")
EXECUTABLE_OUTPUT="${BUILD_TMP_DIR}/${EXECUTABLE_NAME}"
BUILD_RELEASE_DIR=""

if [[ "${#ARCHS[@]}" -eq 1 ]]; then
  swift build -c release --arch "${ARCHS[0]}"
  ARCH_BUILD_DIR="$(swift build -c release --arch "${ARCHS[0]}" --show-bin-path)"
  cp "${ARCH_BUILD_DIR}/${EXECUTABLE_NAME}" "${EXECUTABLE_OUTPUT}"
  BUILD_RELEASE_DIR="$(cd "${ARCH_BUILD_DIR}" && pwd -P)"
else
  LIPO_INPUTS=()
  for arch in "${ARCHS[@]}"; do
    swift build -c release --arch "${arch}"
    ARCH_BUILD_DIR="$(swift build -c release --arch "${arch}" --show-bin-path)"
    # Some build backends reuse one output directory across architectures.
    ARCH_EXECUTABLE="${BUILD_TMP_DIR}/${EXECUTABLE_NAME}-${arch}"
    cp "${ARCH_BUILD_DIR}/${EXECUTABLE_NAME}" "${ARCH_EXECUTABLE}"
    LIPO_INPUTS+=("${ARCH_EXECUTABLE}")
    if [[ -z "${BUILD_RELEASE_DIR}" ]]; then
      BUILD_RELEASE_DIR="$(cd "${ARCH_BUILD_DIR}" && pwd -P)"
    fi
  done
  xcrun lipo -create "${LIPO_INPUTS[@]}" -output "${EXECUTABLE_OUTPUT}"
fi

rm -rf "${BUNDLE_DIR}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}"
cp "${EXECUTABLE_OUTPUT}" "${MACOS_DIR}/${EXECUTABLE_NAME}"
find "${BUILD_RELEASE_DIR}" -maxdepth 1 -name "TokenTrackerMenuBar_*.bundle" -exec cp -R {} "${RESOURCES_DIR}/" \;

if [[ -f "${APP_ICON_SOURCE}" ]]; then
  # iconutil requires a directory whose name ends in .iconset, so create it
  # inside a fresh mktemp base to avoid an rm -rf/mkdir symlink race.
  ICONSET_TMP_BASE="$(mktemp -d)"
  TMP_DIRS+=("${ICONSET_TMP_BASE}")
  APP_ICONSET_DIR="${ICONSET_TMP_BASE}/AppIcon.iconset"
  mkdir -p "${APP_ICONSET_DIR}"
  sips -z 16 16 "${APP_ICON_SOURCE}" --out "${APP_ICONSET_DIR}/icon_16x16.png" >/dev/null
  sips -z 32 32 "${APP_ICON_SOURCE}" --out "${APP_ICONSET_DIR}/icon_16x16@2x.png" >/dev/null
  sips -z 32 32 "${APP_ICON_SOURCE}" --out "${APP_ICONSET_DIR}/icon_32x32.png" >/dev/null
  sips -z 64 64 "${APP_ICON_SOURCE}" --out "${APP_ICONSET_DIR}/icon_32x32@2x.png" >/dev/null
  sips -z 128 128 "${APP_ICON_SOURCE}" --out "${APP_ICONSET_DIR}/icon_128x128.png" >/dev/null
  sips -z 256 256 "${APP_ICON_SOURCE}" --out "${APP_ICONSET_DIR}/icon_128x128@2x.png" >/dev/null
  sips -z 256 256 "${APP_ICON_SOURCE}" --out "${APP_ICONSET_DIR}/icon_256x256.png" >/dev/null
  sips -z 512 512 "${APP_ICON_SOURCE}" --out "${APP_ICONSET_DIR}/icon_256x256@2x.png" >/dev/null
  sips -z 512 512 "${APP_ICON_SOURCE}" --out "${APP_ICONSET_DIR}/icon_512x512.png" >/dev/null
  sips -z 1024 1024 "${APP_ICON_SOURCE}" --out "${APP_ICONSET_DIR}/icon_512x512@2x.png" >/dev/null
  iconutil -c icns "${APP_ICONSET_DIR}" -o "${RESOURCES_DIR}/${APP_ICON_FILE}"
fi

cat > "${BUNDLE_DIR}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>TokenTrackerMenuBar</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleIdentifier</key>
  <string>local.token-tracker.menubar</string>
  <key>CFBundleName</key>
  <string>Tokki</string>
  <key>CFBundleDisplayName</key>
  <string>Tokki</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${APP_VERSION}</string>
  <key>CFBundleVersion</key>
  <string>${APP_BUILD}</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
</dict>
</plist>
PLIST

echo "Built ${BUNDLE_DIR}"
