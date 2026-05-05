#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --group dev
need_fetch=1
if test -f vendor/ffmpeg.exe && test -f vendor/ffprobe.exe; then need_fetch=0; fi
if test -f vendor/ffmpeg && test -f vendor/ffprobe; then need_fetch=0; fi
if test "$need_fetch" = 1; then
  echo "Fetching FFmpeg into vendor/ (skipped if unsupported)..."
  uv run python scripts/fetch_ffmpeg_vendor.py || true
fi
if test -f packaging/app.ico; then
  echo "Using app icon from spec data/icon settings: packaging/app.ico"
else
  echo "Icon not found at packaging/app.ico. Spec will build without app icon."
fi
uv run pyinstaller packaging/easyclip.spec --noconfirm
# Ensure ffmpeg binaries are placed in dist/easyclip/ffmpeg for runtime lookup.
mkdir -p dist/easyclip/ffmpeg
if test -f vendor/ffmpeg.exe && test -f vendor/ffprobe.exe; then
  cp -f vendor/ffmpeg.exe dist/easyclip/ffmpeg/
  cp -f vendor/ffprobe.exe dist/easyclip/ffmpeg/
elif test -f vendor/ffmpeg && test -f vendor/ffprobe; then
  cp -f vendor/ffmpeg dist/easyclip/ffmpeg/
  cp -f vendor/ffprobe dist/easyclip/ffmpeg/
fi
echo "Output: dist/easyclip/"
echo "Bundled FFmpeg location: dist/easyclip/ffmpeg/"
