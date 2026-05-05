"""Download / install FFmpeg next to the frozen app (BtbN LGPL builds)."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

logger = logging.getLogger(__name__)

BTBN_LATEST = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"


@dataclass(frozen=True)
class _BundleSpec:
    """Single BtbN archive (LGPL) for a platform."""

    filename: str
    archive: Literal["zip", "tar.xz"]


def frozen_bundle_dir() -> Path | None:
    """Base directory for frozen app assets (onedir: next to easyclip.exe)."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def frozen_ffmpeg_dir() -> Path | None:
    """Directory where bundled ffmpeg should live for frozen app."""
    base = frozen_bundle_dir()
    if base is None:
        return None
    return base / "ffmpeg"


def skip_auto_download() -> bool:
    return os.environ.get("EASYCLIP_SKIP_BUNDLED_FFMPEG_DOWNLOAD", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def bundle_spec_for_platform() -> _BundleSpec | None:
    """BtbN asset name; macOS not shipped by BtbN in this stream — returns None."""
    if sys.platform == "win32":
        m = platform.machine().lower()
        if m in ("arm64", "aarch64"):
            return _BundleSpec("ffmpeg-master-latest-winarm64-lgpl.zip", "zip")
        return _BundleSpec("ffmpeg-master-latest-win64-lgpl.zip", "zip")
    if sys.platform.startswith("linux"):
        m = platform.machine().lower()
        if m in ("aarch64", "arm64"):
            return _BundleSpec("ffmpeg-master-latest-linuxarm64-lgpl.tar.xz", "tar.xz")
        return _BundleSpec("ffmpeg-master-latest-linux64-lgpl.tar.xz", "tar.xz")
    return None


def default_download_url() -> str | None:
    override = os.environ.get("EASYCLIP_FFMPEG_DOWNLOAD_URL", "").strip()
    if override:
        return override
    spec = bundle_spec_for_platform()
    if spec is None:
        return None
    return f"{BTBN_LATEST}/{spec.filename}"


def _norm_member(name: str) -> str:
    return name.replace("\\", "/")


def _find_ffmpeg_members(names: list[str]) -> tuple[str, str]:
    ff_m = pr_m = None
    for n in names:
        base = Path(_norm_member(n)).name.lower()
        if base in ("ffmpeg", "ffmpeg.exe"):
            ff_m = n
        elif base in ("ffprobe", "ffprobe.exe"):
            pr_m = n
    if not ff_m or not pr_m:
        raise RuntimeError("archive missing ffmpeg or ffprobe in expected layout")
    return ff_m, pr_m


def _install_from_zip(zip_path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        ff_m, pr_m = _find_ffmpeg_members(zf.namelist())
        with zf.open(ff_m) as src, open(dest_dir / Path(ff_m).name, "wb") as out:
            shutil.copyfileobj(src, out)
        with zf.open(pr_m) as src, open(dest_dir / Path(pr_m).name, "wb") as out:
            shutil.copyfileobj(src, out)


def _install_from_tar_xz(path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "r:xz") as tf:
        names = [m.name for m in tf.getmembers() if m.isfile()]
        ff_m, pr_m = _find_ffmpeg_members(names)
        src_ff = tf.extractfile(ff_m)
        src_fp = tf.extractfile(pr_m)
        if not src_ff or not src_fp:
            raise RuntimeError("failed to read ffmpeg/ffprobe from archive")
        with open(dest_dir / Path(ff_m).name, "wb") as out:
            shutil.copyfileobj(src_ff, out)
        with open(dest_dir / Path(pr_m).name, "wb") as out:
            shutil.copyfileobj(src_fp, out)
    for p in dest_dir.iterdir():
        if p.is_file() and p.name in ("ffmpeg", "ffprobe"):
            p.chmod(p.stat().st_mode | 0o111)


def install_downloaded_archive(archive_path: Path, dest_dir: Path) -> None:
    """Extract ffmpeg + ffprobe from a BtbN-style zip or .tar.xz into dest_dir."""
    suf = archive_path.suffix.lower()
    if archive_path.name.lower().endswith(".tar.xz"):
        _install_from_tar_xz(archive_path, dest_dir)
    elif suf == ".zip":
        _install_from_zip(archive_path, dest_dir)
    else:
        raise RuntimeError(f"unsupported archive: {archive_path}")


def download_url_to_file(
    url: str,
    dest_file: Path,
    *,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> None:
    """Stream download with optional progress (bytes_done, total_or_none)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "EasyClip/ffmpeg-bootstrap"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 — fixed BtbN HTTPS URL
            total: int | None = None
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                total = int(cl)
            n = 0
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_file, "wb") as f:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    n += len(chunk)
                    if on_progress:
                        on_progress(n, total)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} downloading FFmpeg") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e.reason}") from e


def download_and_install_to(dest_dir: Path, url: str | None = None) -> None:
    """Download BtbN bundle and install ffmpeg + ffprobe into dest_dir."""
    u = url or default_download_url()
    if not u:
        raise RuntimeError("no bundled FFmpeg download for this platform")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / ("bundle.tar.xz" if u.endswith(".tar.xz") else "bundle.zip")
        download_url_to_file(u, tmp)
        install_downloaded_archive(tmp, dest_dir)


def fetch_vendor_directory(repo_root: Path) -> None:
    """CLI / build script: fill ``<repo>/vendor`` with ffmpeg + ffprobe."""
    dest = repo_root / "vendor"
    download_and_install_to(dest)


def ensure_bundled_ffmpeg_with_ui() -> bool:
    """Frozen app only: ensure bundled ffmpeg in ``<exe-dir>/ffmpeg``."""
    from PySide6.QtCore import QEventLoop, Qt, QThread, Signal, Slot
    from PySide6.QtWidgets import QMessageBox, QProgressDialog

    from easyclip.core.ffmpeg_util import _pair_in_dir
    from easyclip.i18n.strings import tr

    base = frozen_bundle_dir()
    target = frozen_ffmpeg_dir()
    if base is None or target is None:
        return True
    if skip_auto_download():
        return True
    if _pair_in_dir(target) or _pair_in_dir(base):
        return True

    url = default_download_url()
    if not url:
        # e.g. macOS: BtbN does not publish this archive family; use PATH or ship binaries in app folder.
        return True

    prog = QProgressDialog()
    prog.setWindowTitle(tr("ffmpeg.bootstrap.title"))
    prog.setLabelText(tr("ffmpeg.bootstrap.downloading"))
    prog.setRange(0, 0)
    prog.setMinimumDuration(0)
    prog.setWindowModality(Qt.WindowModality.ApplicationModal)

    class _Dl(QThread):
        done_ok = Signal()
        failed = Signal(str)
        progressed = Signal(int, int)

        def __init__(self, download_url: str, target_dir: Path) -> None:
            super().__init__()
            self._download_url = download_url
            self._target_dir = target_dir

        def run(self) -> None:
            try:
                with tempfile.TemporaryDirectory() as td:
                    tmp = Path(td) / (
                        "bundle.tar.xz" if self._download_url.endswith(".tar.xz") else "bundle.zip"
                    )

                    def _pg(n: int, t: int | None) -> None:
                        self.progressed.emit(n, t if t is not None else -1)

                    download_url_to_file(self._download_url, tmp, on_progress=_pg)
                    install_downloaded_archive(tmp, self._target_dir)
                self.done_ok.emit()
            except Exception as e:  # noqa: BLE001
                self.failed.emit(str(e))

    th = _Dl(url, target)

    @Slot(int, int)
    def _on_prog(n: int, tot: int) -> None:
        mb = n / (1024 * 1024)
        line = tr("ffmpeg.bootstrap.mb", mb=f"{mb:.1f}")
        if tot > 0:
            prog.setRange(0, 1000)
            prog.setValue(min(1000, int(n / tot * 1000)))
        prog.setLabelText(tr("ffmpeg.bootstrap.downloading") + "\n" + line)

    th.progressed.connect(_on_prog)
    ok = [False]
    err: list[str] = []

    @Slot()
    def _ok() -> None:
        ok[0] = True
        prog.close()

    @Slot(str)
    def _fail(msg: str) -> None:
        err.append(msg)
        prog.close()

    th.done_ok.connect(_ok)
    th.failed.connect(_fail)
    loop = QEventLoop()
    th.finished.connect(loop.quit)
    th.start()
    prog.show()
    loop.exec()
    if ok[0] and (_pair_in_dir(target) or _pair_in_dir(base)):
        return True
    if ok[0]:
        QMessageBox.critical(
            None,
            tr("ffmpeg.bootstrap.failed_title"),
            tr("ffmpeg.bootstrap.verify_failed"),
        )
    elif err:
        QMessageBox.critical(
            None,
            tr("ffmpeg.bootstrap.failed_title"),
            tr("ffmpeg.bootstrap.failed_body", detail=err[0]),
        )
    QMessageBox.information(
        None,
        tr("ffmpeg.bootstrap.manual_title"),
        tr("ffmpeg.bootstrap.manual_body"),
    )
    return False
