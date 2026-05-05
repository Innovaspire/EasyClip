#!/usr/bin/env python3
"""Download BtbN LGPL ffmpeg/ffprobe into repo ``vendor/`` (for PyInstaller bundle)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> None:
    from easyclip.core.ffmpeg_bootstrap import bundle_spec_for_platform, fetch_vendor_directory

    if bundle_spec_for_platform() is None:
        print("fetch_ffmpeg_vendor: skipped (no BtbN manifest for this OS)", file=sys.stderr)
        raise SystemExit(0)
    fetch_vendor_directory(ROOT)
    print("vendor:", ROOT / "vendor")


if __name__ == "__main__":
    main()
