# Build EasyClip with PyInstaller (onedir). Run from repo root.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..
${iconPath} = Join-Path (Get-Location) "packaging\app.ico"
uv sync --group dev
$vendor = Join-Path (Get-Location) "vendor"
$winOk = (Test-Path (Join-Path $vendor "ffmpeg.exe")) -and (Test-Path (Join-Path $vendor "ffprobe.exe"))
$uxOk = (Test-Path (Join-Path $vendor "ffmpeg")) -and (Test-Path (Join-Path $vendor "ffprobe"))
if (-not ($winOk -or $uxOk)) {
  Write-Host "Fetching FFmpeg into vendor/ (skipped on platforms without a manifest)..."
  uv run python scripts/fetch_ffmpeg_vendor.py
  $winOk = (Test-Path (Join-Path $vendor "ffmpeg.exe")) -and (Test-Path (Join-Path $vendor "ffprobe.exe"))
  $uxOk = (Test-Path (Join-Path $vendor "ffmpeg")) -and (Test-Path (Join-Path $vendor "ffprobe"))
}
if (-not ($winOk -or $uxOk)) {
  throw "FFmpeg binaries not found in vendor/. Aborting release build to keep package not-ready from shipping."
}
if (Test-Path $iconPath) {
  Write-Host "Using app icon from spec data/icon settings: $iconPath"
} else {
  Write-Host "Icon not found at packaging/app.ico. Spec will build without app icon."
}
uv run pyinstaller packaging/easyclip.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller build failed with exit code $LASTEXITCODE."
}
# Ensure ffmpeg binaries are placed in dist/easyclip/ffmpeg for runtime lookup.
$distRoot = Join-Path (Get-Location) "dist\easyclip"
$distFfmpeg = Join-Path $distRoot "ffmpeg"
New-Item -ItemType Directory -Force -Path $distFfmpeg | Out-Null
if ($winOk) {
  Copy-Item (Join-Path $vendor "ffmpeg.exe") $distFfmpeg -Force
  Copy-Item (Join-Path $vendor "ffprobe.exe") $distFfmpeg -Force
} elseif ($uxOk) {
  Copy-Item (Join-Path $vendor "ffmpeg") $distFfmpeg -Force
  Copy-Item (Join-Path $vendor "ffprobe") $distFfmpeg -Force
}
Write-Host "Output: dist/easyclip/"
Write-Host "Bundled FFmpeg location: dist/easyclip/ffmpeg/"
