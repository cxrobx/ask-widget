#!/bin/bash
# Build a self-contained "Onyx.app" and install it to /Applications.
#   ./launcher/build-app.sh          # build + install
#   ./launcher/build-app.sh --no-install
#
# Release signing is opt-in:
#   ONYX_SIGN_IDENTITY="Developer ID Application: …" ./launcher/build-app.sh
#   ONYX_NOTARY_PROFILE="notary-profile" ONYX_SIGN_IDENTITY="…" ./launcher/build-app.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
APP_NAME="Onyx"
BIN_NAME="Onyx"
BUILD="$DIR/build"
BUNDLE="$BUILD/$APP_NAME.app"
BUILDER_VENV="$DIR/.build-venv"
SERVER_DIST="$BUILD/server-dist"
BUILD_LOCK="$ROOT/requirements-build.lock"
SIGN_IDENTITY="${ONYX_SIGN_IDENTITY:--}"
NOTARY_PROFILE="${ONYX_NOTARY_PROFILE:-}"
APP_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$DIR/Info.plist")"

if [ ! -x "$BUILDER_VENV/bin/python" ]; then
  echo "→ Creating isolated bundler environment…"
  python3 -m venv "$BUILDER_VENV"
fi

echo "→ Preparing Python service bundler…"
"$BUILDER_VENV/bin/python" -m pip install --disable-pip-version-check \
  --requirement "$BUILD_LOCK"
PYTHON_APP_VERSION="$(PYTHONPATH="$ROOT/src" "$BUILDER_VENV/bin/python" -c \
  'from onyx import __version__; print(__version__)')"
if [ "$PYTHON_APP_VERSION" != "$APP_VERSION" ]; then
  echo "Version mismatch: Python=$PYTHON_APP_VERSION, Info.plist=$APP_VERSION" >&2
  exit 1
fi

echo "→ Cleaning…"
rm -rf "$BUILD"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"

echo "→ Bundling self-contained local service…"
"$BUILDER_VENV/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --onedir \
  --name onyx-server \
  --paths "$ROOT/src" \
  --add-data "$ROOT/static:static" \
  --collect-all markdown_it \
  --collect-all uvicorn \
  --collect-all pypdf \
  --distpath "$SERVER_DIST" \
  --workpath "$BUILD/pyinstaller-work" \
  --specpath "$BUILD" \
  "$DIR/server-entry.py"
mkdir -p "$BUNDLE/Contents/Resources/Server"
cp -R "$SERVER_DIST/onyx-server/." "$BUNDLE/Contents/Resources/Server/"

echo "→ Drawing icon…"
swiftc -O "$DIR/make-icon.swift" -o "$BUILD/make-icon"
"$BUILD/make-icon" "$BUILD/icon.png" "$DIR/onyx-gem.png"
ICONSET="$BUILD/AppIcon.iconset"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s        "$BUILD/icon.png" --out "$ICONSET/icon_${s}x${s}.png"     >/dev/null
  sips -z $((s*2)) $((s*2)) "$BUILD/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUNDLE/Contents/Resources/AppIcon.icns"

echo "→ Packaging Alfred workflow…"
ALFRED_WORKFLOW="$BUILD/Open-in-Onyx.alfredworkflow"
/usr/bin/zip -j -q "$ALFRED_WORKFLOW" \
  "$ROOT/integrations/alfred/info.plist" "$ROOT/integrations/alfred/onyx_search.py" "$BUILD/icon.png"

echo "→ Compiling Swift…"
swiftc -framework Cocoa -framework WebKit -framework UniformTypeIdentifiers -O \
  "$DIR/Onyx.swift" -o "$BUNDLE/Contents/MacOS/$BIN_NAME"

echo "→ Assembling bundle…"
cp "$DIR/Info.plist" "$BUNDLE/Contents/Info.plist"

echo "→ Signing…"
if [ "$SIGN_IDENTITY" = "-" ]; then
  # Ad-hoc signing gives a local development build a stable code identity.
  codesign --force --deep --sign - "$BUNDLE"
else
  codesign --force --deep --options runtime --timestamp \
    --sign "$SIGN_IDENTITY" "$BUNDLE"
fi
codesign --verify --deep --strict --verbose=2 "$BUNDLE"

ARCHIVE="$BUILD/Onyx-$APP_VERSION-macOS.zip"
CHECKSUM="$ARCHIVE.sha256"
echo "→ Creating release archive…"
ditto -c -k --sequesterRsrc --keepParent "$BUNDLE" "$ARCHIVE"

if [ -n "$NOTARY_PROFILE" ]; then
  if [ "$SIGN_IDENTITY" = "-" ]; then
    echo "ONYX_NOTARY_PROFILE requires ONYX_SIGN_IDENTITY." >&2
    exit 1
  fi
  echo "→ Submitting release archive for notarization…"
  xcrun notarytool submit "$ARCHIVE" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$BUNDLE"
  rm -f "$ARCHIVE"
  ditto -c -k --sequesterRsrc --keepParent "$BUNDLE" "$ARCHIVE"
fi
(
  cd "$BUILD"
  LC_ALL=C shasum -a 256 "$(basename "$ARCHIVE")" > "$(basename "$CHECKSUM")"
)

if [ "${1:-}" = "--no-install" ]; then
  echo "✓ Built: $BUNDLE"
  echo "✓ Archive: $ARCHIVE"
  echo "✓ Alfred workflow: $ALFRED_WORKFLOW"
  exit 0
fi

DEST="/Applications/$APP_NAME.app"
STAGED="$BUILD/install/$APP_NAME.app"
BACKUP="$BUILD/install/$APP_NAME.previous.app"
echo "→ Installing to ${DEST}"
mkdir -p "$BUILD/install"
rm -rf "$STAGED" "$BACKUP"
cp -R "$BUNDLE" "$STAGED"
if [ -e "$DEST" ]; then
  mv "$DEST" "$BACKUP"
fi
if ! mv "$STAGED" "$DEST"; then
  if [ -e "$BACKUP" ]; then mv "$BACKUP" "$DEST"; fi
  echo "Install failed; the previous app was restored." >&2
  exit 1
fi
rm -rf "$BACKUP"
# Clear the quarantine flag so it opens without the unidentified-developer prompt.
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
/System/Library/CoreServices/pbs -update >/dev/null 2>&1 || true
# The headless service (scripts/install-daemon.sh) keeps running the bundle it started from, and the
# app adopts whatever healthy service owns the port: left alone, the new app shows the old pages, and
# macOS stops honouring the running copy's Documents access once its files are replaced. Restart it
# on this build.
DAEMON="gui/$(id -u)/com.cx.onyx.server"
if launchctl print "$DAEMON" >/dev/null 2>&1; then
  echo "→ Restarting the background service on this build"
  launchctl kickstart -k "$DAEMON" >/dev/null 2>&1 \
    || echo "  Couldn't restart it; run: launchctl kickstart -k $DAEMON" >&2
fi

echo ""
echo "✓ Installed: $DEST"
if pgrep -xq "$APP_NAME"; then
  echo "  $APP_NAME is open: quit and reopen it to run this build."
fi
echo "  Archive: $ARCHIVE"
echo "  Alfred workflow: $ALFRED_WORKFLOW"
echo "  Open it from Spotlight/Launchpad as 'Onyx', or:  open -a 'Onyx'"
