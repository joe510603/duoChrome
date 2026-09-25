#!/usr/bin/env bash
# Build duoChrome.app and wrap it into duoChrome.dmg
#
# Usage: ./build.sh
# Output: dist/duochrome.dmg (plus dist/duochrome.app as a side effect)
#
# Requirements (already present on this machine):
#   - Python 3.9+ with duoChrome installed (`pip install -e .`)
#   - PyInstaller (`pip install pyinstaller`)
#   - hdiutil (built-in on macOS)

set -euo pipefail

# Resolve paths relative to this script (works regardless of cwd)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "${SCRIPT_DIR}/../.." && pwd )"
DIST_DIR="${PROJECT_DIR}/dist"
APP="${DIST_DIR}/duochrome.app"
DMG="${DIST_DIR}/duochrome.dmg"
VOL_NAME="duoChrome"
MOUNT_DIR="/tmp/duochrome-dmg-staging"

# pyinstaller / pip-installed scripts live here on macOS user installs but
# are not on PATH by default in a fresh shell. Add them.
export PATH="${HOME}/Library/Python/3.9/bin:${PATH}"

cd "${PROJECT_DIR}"

# 0. Sanity: clean previous build artifacts
echo "==> Cleaning previous build..."
rm -rf "${DIST_DIR}/build" "${DIST_DIR}/duochrome" "${APP}" "${DMG}"

# 1. PyInstaller
echo "==> Running PyInstaller..."
pyinstaller --noconfirm --clean "${SCRIPT_DIR}/duochrome.spec"

if [ ! -d "${APP}" ]; then
    echo "✗ PyInstaller failed to produce ${APP}" >&2
    exit 1
fi
echo "✓ ${APP}"

# 2. Create .dmg
echo "==> Building .dmg..."

# 2a. Create a writable staging folder
rm -rf "${MOUNT_DIR}"
mkdir -p "${MOUNT_DIR}"

# 2b. Copy .app in, plus a /Applications symlink so users get drag-to-install
cp -R "${APP}" "${MOUNT_DIR}/"
ln -s /Applications "${MOUNT_DIR}/Applications"

# 2c. Make a "background" nice to look at (optional — skip if py3cairo missing)
# Just use defaults for now.

# 2d. Build the dmg with hdiutil
hdiutil create \
    -volname "${VOL_NAME}" \
    -srcfolder "${MOUNT_DIR}" \
    -ov \
    -format UDZO \
    "${DMG}"

# 2e. Cleanup staging
rm -rf "${MOUNT_DIR}"

echo ""
echo "============================================="
echo "✓ Built: ${DMG}"
echo "  Size: $(du -h "${DMG}" | cut -f1)"
echo "============================================="
echo "Send the DMG to users — they double-click it, drag duochrome.app into"
echo "/Applications, then launch from Spotlight/Dock."
echo ""
echo "First launch will auto-download Chromium (~200MB) via Playwright."