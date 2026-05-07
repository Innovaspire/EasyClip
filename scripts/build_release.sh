#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-0.0.0}"
PLATFORM="${2:-unknown}"
VERSION_CLEAN="${VERSION#v}"

uv sync --group dev

need_fetch=1
if test -f vendor/ffmpeg.exe && test -f vendor/ffprobe.exe; then need_fetch=0; fi
if test -f vendor/ffmpeg && test -f vendor/ffprobe; then need_fetch=0; fi
if test "$need_fetch" = 1; then
  echo "Fetching FFmpeg into vendor/..."
  uv run python scripts/fetch_ffmpeg_vendor.py
fi

if test -f packaging/app.ico; then
  echo "Using app icon from spec data/icon settings: packaging/app.ico"
else
  echo "Icon not found at packaging/app.ico. Spec will build without app icon."
fi

uv run pyinstaller packaging/easyclip.spec --noconfirm

OS="$(uname -s)"

if [ "$OS" = "Darwin" ]; then
  # --- macOS: .app bundle --------------------------------------------------
  APP="dist/EasyClip.app"
  MACOS_DIR="${APP}/Contents/MacOS"

  # Bundle FFmpeg into the .app
  if test -f vendor/ffmpeg && test -f vendor/ffprobe; then
    mkdir -p "${MACOS_DIR}/ffmpeg"
    cp -f vendor/ffmpeg "${MACOS_DIR}/ffmpeg/"
    cp -f vendor/ffprobe "${MACOS_DIR}/ffmpeg/"
  fi

  # Code sign: use env-provided identity, else fall back to ad-hoc
  CODESIGN_IDENTITY="${EASYCLIP_CODESIGN_IDENTITY:-}"
  if [ -n "${CODESIGN_IDENTITY}" ]; then
    codesign --deep --force --sign "${CODESIGN_IDENTITY}" "${APP}"
    echo "Signed with: ${CODESIGN_IDENTITY}"
  else
    codesign --deep --force --sign - "${APP}"
    echo "Signed with ad-hoc identity"
  fi

  ARCHIVE_NAME="EasyClip-v${VERSION_CLEAN}-${PLATFORM}"
  hdiutil create -volname "EasyClip" \
    -srcfolder "${APP}" \
    -ov -format UDZO \
    "dist/${ARCHIVE_NAME}.dmg"
  echo "DMG: dist/${ARCHIVE_NAME}.dmg"

else
  # --- Linux / other Unix --------------------------------------------------
  mkdir -p dist/easyclip/ffmpeg
  if test -f vendor/ffmpeg.exe && test -f vendor/ffprobe.exe; then
    cp -f vendor/ffmpeg.exe dist/easyclip/ffmpeg/
    cp -f vendor/ffprobe.exe dist/easyclip/ffmpeg/
  elif test -f vendor/ffmpeg && test -f vendor/ffprobe; then
    cp -f vendor/ffmpeg dist/easyclip/ffmpeg/
    cp -f vendor/ffprobe dist/easyclip/ffmpeg/
  fi

  PARENT="EasyClip-v${VERSION_CLEAN}"
  ARCHIVE_NAME="EasyClip-v${VERSION_CLEAN}-${PLATFORM}"
  STAGING="dist/${PARENT}"

  rm -rf "${STAGING}"
  mkdir -p "${STAGING}"
  cp -R dist/easyclip/* "${STAGING}/"

  if [ "$OS" = "Linux" ]; then
    chmod +x "${STAGING}/easyclip" 2>/dev/null || true
  fi
  chmod +x "${STAGING}/ffmpeg/ffmpeg" "${STAGING}/ffmpeg/ffprobe" 2>/dev/null || true

  cd dist
  zip -r "${ARCHIVE_NAME}.zip" "${PARENT}"
  cd ..

  echo "Archive: dist/${ARCHIVE_NAME}.zip"
fi
