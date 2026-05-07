"""Download / install FFmpeg into the app directory (dev or frozen).

Never modifies system-PATH files — only writes into the app-managed ``ffmpeg/`` folder.
On macOS, downloads individual binaries from ffmpeg.martin-riedl.de; other platforms use BtbN LGPL archives.
"""

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
MARTIN_RIEDL_BASE = "https://ffmpeg.martin-riedl.de"

# ---------------------------------------------------------------------------
# Platform / path helpers
# ---------------------------------------------------------------------------


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _repo_root() -> Path | None:
    try:
        return Path(__file__).resolve().parents[3]
    except IndexError:
        return None


def get_app_ffmpeg_dir() -> Path:
    """Destination for auto-downloaded FFmpeg.

    Dev mode:  ``<repo>/ffmpeg/``
    Frozen:    ``<exe>/ffmpeg/``
    """
    if _is_frozen():
        return Path(sys.executable).resolve().parent / "ffmpeg"
    root = _repo_root()
    if root is not None:
        return root / "ffmpeg"
    return Path.cwd() / "ffmpeg"


def _is_in_app_dir(path: str | Path) -> bool:
    try:
        app = get_app_ffmpeg_dir().resolve()
        return Path(path).resolve().is_relative_to(app)
    except ValueError:
        return False


def _is_on_system_path(path: str | Path) -> bool:
    """True if *path* lives under a PATH entry and is NOT in the app dir."""
    if _is_in_app_dir(path):
        return False
    p = Path(path).resolve()
    sep = os.pathsep
    for d in os.environ.get("PATH", "").split(sep):
        try:
            if p.is_relative_to(Path(d).resolve()):
                return True
        except ValueError:
            continue
    return False


def skip_auto_download() -> bool:
    return os.environ.get("EASYCLIP_SKIP_BUNDLED_FFMPEG_DOWNLOAD", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


# ---------------------------------------------------------------------------
# Bundle specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _BundleSpec:
    filename: str
    archive: Literal["zip", "tar.xz"]


def bundle_spec_for_platform() -> _BundleSpec | None:
    """BtbN archive for Windows/Linux; returns None on macOS (martin-riedl.de handles that separately)."""
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
    """Single-archive download URL (BtbN); None on macOS where two-file download is used."""
    override = os.environ.get("EASYCLIP_FFMPEG_DOWNLOAD_URL", "").strip()
    if override:
        return override
    spec = bundle_spec_for_platform()
    if spec is None:
        return None
    return f"{BTBN_LATEST}/{spec.filename}"


def _is_macos() -> bool:
    return sys.platform == "darwin"


# ---------------------------------------------------------------------------
# Archive extraction
# ---------------------------------------------------------------------------


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
    _chmod_binaries(dest_dir)


_ZIP_MAGIC = b"PK\x03\x04"


def _install_single_binary(zip_path: Path, dest_dir: Path, expected_name: str) -> Path:
    """Extract a single-binary zip, or copy directly if already a raw binary.

    evermeet.cx / martin-riedl.de may serve the raw executable directly (no zip wrapper).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / expected_name

    with open(zip_path, "rb") as fh:
        head = fh.read(4)

    if head[:4] == _ZIP_MAGIC:
        # Zip-wrapped single binary.
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                base = Path(_norm_member(name)).name
                if base.lower() == expected_name.lower():
                    with zf.open(name) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
                    return dest
        raise RuntimeError(f"missing {expected_name} in archive")
    else:
        # Raw binary — copy the file directly.
        shutil.copyfile(zip_path, dest)
        return dest


def _chmod_binaries(dest_dir: Path) -> None:
    for name in ("ffmpeg", "ffprobe"):
        p = dest_dir / name
        if p.is_file():
            p.chmod(p.stat().st_mode | 0o111)


def install_downloaded_archive(archive_path: Path, dest_dir: Path) -> None:
    suf = archive_path.suffix.lower()
    if archive_path.name.lower().endswith(".tar.xz"):
        _install_from_tar_xz(archive_path, dest_dir)
    elif suf == ".zip":
        _install_from_zip(archive_path, dest_dir)
    else:
        raise RuntimeError(f"unsupported archive: {archive_path}")


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def _download_bytes(
    url: str,
    *,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> bytes:
    """Download URL into memory with optional progress callback."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "EasyClip/ffmpeg-bootstrap"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            total: int | None = None
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                total = int(cl)
            chunks: list[bytes] = []
            n = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                n += len(chunk)
                if on_progress:
                    on_progress(n, total)
            return b"".join(chunks)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e.reason}") from e


def download_url_to_file(
    url: str,
    dest_file: Path,
    *,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> None:
    data = _download_bytes(url, on_progress=on_progress)
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    dest_file.write_bytes(data)


# ---------------------------------------------------------------------------
# macOS: martin-riedl.de two-file download
# ---------------------------------------------------------------------------


def _macos_arch() -> str:
    """Return martin-riedl.de arch segment: ``arm64`` or ``amd64``."""
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "amd64"


def _martin_riedl_latest_version(os_name: str, arch: str) -> str:
    """Scrape the latest snapshot version-id from the history page."""
    import re
    url = f"{MARTIN_RIEDL_BASE}/info/history/{os_name}/{arch}/snapshot"
    data = _download_bytes(url)
    html = data.decode("utf-8", errors="replace")
    # History page links to detail pages: /info/detail/{os}/{arch}/{version_id}
    m = re.search(rf'/info/detail/{os_name}/{arch}/([^/"]+)', html)
    if not m:
        raise RuntimeError("could not determine latest FFmpeg version from build server")
    return m.group(1)


def _macos_download_urls() -> tuple[str, str]:
    """Return (ffmpeg_url, ffprobe_url) for macOS arm64/amd64 from martin-riedl.de."""
    if os.environ.get("EASYCLIP_FFMPEG_DOWNLOAD_URL", "").strip():
        ff = os.environ["EASYCLIP_FFMPEG_DOWNLOAD_URL"].strip()
        fp = os.environ.get("EASYCLIP_FFPROBE_DOWNLOAD_URL", "").strip()
        if not fp:
            fp = ff
        return ff, fp

    arch = _macos_arch()
    ver = _martin_riedl_latest_version("macos", arch)
    base = f"{MARTIN_RIEDL_BASE}/download/macos/{arch}/{ver}"
    return f"{base}/ffmpeg.zip", f"{base}/ffprobe.zip"


def download_macos_vendor(dest_dir: Path) -> None:
    """Download FFmpeg + FFprobe for macOS into *dest_dir* (public wrapper for build scripts)."""
    _download_macos_bundles(dest_dir)


def _download_macos_bundles(
    dest_dir: Path,
    *,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    """Download ffmpeg + ffprobe for macOS (two individual zip downloads).

    *on_progress* receives overall fraction 0.0–1.0.
    """
    ffmpeg_url, ffprobe_url = _macos_download_urls()

    def _combined_dl_progress(
        stage: int,
    ) -> Callable[[int, int | None], None]:
        def cb(n: int, tot: int | None) -> None:
            if on_progress is None:
                return
            stage_frac = n / max(tot or 50_000_000, 1) if tot else 0.0
            overall = (stage + stage_frac) / 2.0
            on_progress(min(1.0, overall))
        return cb

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ff_data = _download_bytes(ffmpeg_url, on_progress=_combined_dl_progress(0))
        ff_tmp = tmp / "ffmpeg"
        ff_tmp.write_bytes(ff_data)
        _install_single_binary(ff_tmp, dest_dir, "ffmpeg")

        fp_data = _download_bytes(ffprobe_url, on_progress=_combined_dl_progress(1))
        fp_tmp = tmp / "ffprobe"
        fp_tmp.write_bytes(fp_data)
        _install_single_binary(fp_tmp, dest_dir, "ffprobe")

    _chmod_binaries(dest_dir)


# ---------------------------------------------------------------------------
# Unified bootstrap
# ---------------------------------------------------------------------------


def ensure_ffmpeg_with_ui(window) -> bool:
    """Ensure runnable FFmpeg is available, downloading if needed/allowed.

    Works in both dev and frozen mode. Never modifies system-PATH files.

    Returns True if FFmpeg is available after the check (found, downloaded, or already ok).
    """
    from PySide6.QtCore import QEventLoop, Qt, QThread, Signal, Slot
    from PySide6.QtWidgets import QMessageBox, QProgressDialog

    from easyclip.core.ffmpeg_util import _pair_in_dir, check_ffmpeg_runnable, find_ffmpeg
    from easyclip.i18n.strings import tr

    if skip_auto_download():
        pair = find_ffmpeg()
        runnable, _ = check_ffmpeg_runnable(pair[0])
        return runnable

    app_dir = get_app_ffmpeg_dir()
    is_macos = _is_macos()

    # --- check for already-working ffmpeg -------------------------------------------------
    # Try ffmpeg/ dir first (app-managed).
    ff_path, fp_path = _pair_in_dir(app_dir) or (None, None)
    if ff_path and fp_path:
        runnable, reason = check_ffmpeg_runnable(ff_path)
        if runnable:
            return True
        # Arch mismatch in app dir — replace.
        if reason == "arch_mismatch":
            answer = QMessageBox.question(
                window,
                tr("ffmpeg.bootstrap.title"),
                tr("ffmpeg.bootstrap.arch_mismatch_app_body"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                if _do_download_with_progress(window, app_dir, is_macos):
                    return True
        # If user said no or download failed, fall through to PATH check.

    # Try vendor/ dir (legacy dev-only location).
    if not _is_frozen():
        root = _repo_root()
        if root is not None:
            vf, vp = _pair_in_dir(root / "vendor") or (None, None)
            if vf and vp:
                runnable, _ = check_ffmpeg_runnable(vf)
                if runnable:
                    return True

    # --- app dir has no working ffmpeg ----------------------------------------------------
    # Check if app dir has ffmpeg at all; if not, offer download.
    ff_in_app, _ = _pair_in_dir(app_dir) or (None, None)
    if not ff_in_app:
        answer = QMessageBox.question(
            window,
            tr("ffmpeg.bootstrap.not_found_title"),
            tr("ffmpeg.bootstrap.not_found_body"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            if _do_download_with_progress(window, app_dir, is_macos):
                return True

    # --- fall back to system PATH ---------------------------------------------------------
    ff_on_path, fp_on_path = find_ffmpeg()
    if _pair_in_dir(Path(ff_on_path).parent if ff_on_path else Path(".")) is not None or (
        ff_on_path and _is_in_app_dir(ff_on_path)
    ):
        # Already checked app dir; find_ffmpeg may have returned the same broken one.
        pass
    elif ff_on_path and fp_on_path:
        runnable, reason = check_ffmpeg_runnable(ff_on_path)
        if runnable:
            return True
        if reason == "arch_mismatch" and _is_on_system_path(ff_on_path):
            msg = tr("ffmpeg.bootstrap.arch_mismatch_system_body", path=str(ff_on_path))
            answer = QMessageBox.warning(
                window,
                tr("ffmpeg.bootstrap.arch_mismatch_system_title"),
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                if _do_download_with_progress(window, app_dir, is_macos):
                    return True

    # --- nothing worked — offer Homebrew, then manual install ----------------------
    if is_macos and _offer_brew_install(window):
        # Re-check PATH after brew install.
        ff, fp = find_ffmpeg()
        ff_ok, _ = check_ffmpeg_runnable(ff)
        fp_ok, _ = check_ffmpeg_runnable(fp)
        if ff_ok and fp_ok:
            return True

    QMessageBox.information(
        window,
        tr("ffmpeg.bootstrap.manual_title"),
        tr("ffmpeg.bootstrap.manual_body"),
    )
    return False


def _offer_brew_install(window) -> bool:
    """Offer to install FFmpeg via Homebrew (macOS only).

    Runs ``brew install ffmpeg`` directly and shows progress.
    Returns True if installation succeeded.
    """
    import subprocess
    from PySide6.QtCore import QEventLoop, Qt, QThread, Signal, Slot
    from PySide6.QtWidgets import QMessageBox, QProgressDialog
    from easyclip.i18n.strings import tr

    brew_path = shutil.which("brew")
    if not brew_path:
        # Homebrew not installed — show instructions.
        answer = QMessageBox.question(
            window,
            tr("ffmpeg.bootstrap.brew_title"),
            tr("ffmpeg.bootstrap.brew_not_installed_body"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                subprocess.Popen(
                    ["/bin/bash", "-c",
                     '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'],
                )
            except Exception:
                pass
        return False

    answer = QMessageBox.question(
        window,
        tr("ffmpeg.bootstrap.brew_title"),
        tr("ffmpeg.bootstrap.brew_available_body"),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False

    prog = QProgressDialog(window)
    prog.setWindowTitle(tr("ffmpeg.bootstrap.brew_title"))
    prog.setLabelText(tr("ffmpeg.bootstrap.downloading"))
    prog.setRange(0, 0)
    prog.setMinimumDuration(0)
    prog.setWindowModality(Qt.WindowModality.ApplicationModal)

    class _BrewThread(QThread):
        done_ok = Signal()
        failed = Signal(str)

        def run(self) -> None:
            try:
                proc = subprocess.run(
                    [brew_path, "install", "ffmpeg"],
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                if proc.returncode != 0:
                    self.failed.emit(proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}")
                else:
                    self.done_ok.emit()
            except subprocess.TimeoutExpired:
                self.failed.emit("timed out")
            except OSError as e:
                self.failed.emit(str(e))

    th = _BrewThread()
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

    if ok[0]:
        QMessageBox.information(
            window,
            tr("ffmpeg.bootstrap.brew_title"),
            tr("ffmpeg.bootstrap.install_restart"),
        )
        return True
    elif err:
        QMessageBox.warning(
            window,
            tr("ffmpeg.bootstrap.failed_title"),
            tr("ffmpeg.bootstrap.failed_body", detail=err[0]),
        )
    return False


def _do_download_with_progress(window, dest_dir: Path, is_macos: bool) -> bool:
    """Run the download in a background thread with QProgressDialog. Returns True on success."""
    from PySide6.QtCore import QEventLoop, Qt, QThread, Signal, Slot
    from PySide6.QtWidgets import QMessageBox, QProgressDialog

    from easyclip.core.ffmpeg_util import _pair_in_dir
    from easyclip.i18n.strings import tr

    url = default_download_url()

    if is_macos and not os.environ.get("EASYCLIP_FFMPEG_DOWNLOAD_URL", "").strip():
        # macOS uses two-file martin-riedl.de download (no single URL).
        pass
    elif not url and not is_macos:
        # No BtbN spec for this platform and no env var override; can't download.
        QMessageBox.information(
            window,
            tr("ffmpeg.bootstrap.failed_title"),
            tr("ffmpeg.bootstrap.failed_body", detail="no download source for this platform"),
        )
        return False

    prog = QProgressDialog(window)
    prog.setWindowTitle(tr("ffmpeg.bootstrap.title"))
    prog.setLabelText(tr("ffmpeg.bootstrap.downloading"))
    prog.setRange(0, 0)
    prog.setMinimumDuration(0)
    prog.setWindowModality(Qt.WindowModality.ApplicationModal)

    class _Dl(QThread):
        done_ok = Signal()
        failed = Signal(str)
        progressed = Signal(int, int)
        progressed_frac = Signal(float)

        def __init__(self, dest: Path, macos: bool, single_url: str | None) -> None:
            super().__init__()
            self._dest = dest
            self._macos = macos
            self._single_url = single_url

        def run(self) -> None:
            try:
                if self._macos:
                    _download_macos_bundles(
                        self._dest,
                        on_progress=lambda f: self.progressed_frac.emit(f),
                    )
                else:
                    assert self._single_url is not None
                    with tempfile.TemporaryDirectory() as td:
                        suffix = ".tar.xz" if self._single_url.endswith(".tar.xz") else ".zip"
                        tmp = Path(td) / f"bundle{suffix}"

                        def _pg(n: int, t: int | None) -> None:
                            self.progressed.emit(n, t if t is not None else -1)

                        download_url_to_file(self._single_url, tmp, on_progress=_pg)
                        install_downloaded_archive(tmp, self._dest)
                self.done_ok.emit()
            except Exception as e:
                self.failed.emit(str(e))

    th = _Dl(dest_dir, is_macos, url)

    @Slot(int, int)
    def _on_bytes(n: int, tot: int) -> None:
        mb = n / (1024 * 1024)
        line = tr("ffmpeg.bootstrap.mb", mb=f"{mb:.1f}")
        if tot > 0:
            prog.setRange(0, 1000)
            prog.setValue(min(1000, int(n / tot * 1000)))
        prog.setLabelText(tr("ffmpeg.bootstrap.downloading") + "\n" + line)

    @Slot(float)
    def _on_frac(f: float) -> None:
        prog.setRange(0, 1000)
        prog.setValue(int(f * 1000))
        mb = f * 50  # rough estimate for macOS combined size
        prog.setLabelText(tr("ffmpeg.bootstrap.downloading") + "\n" + tr("ffmpeg.bootstrap.mb", mb=f"{mb:.1f}"))

    th.progressed.connect(_on_bytes)
    th.progressed_frac.connect(_on_frac)
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

    if ok[0] and bool(_pair_in_dir(dest_dir)):
        return True

    if ok[0]:
        QMessageBox.critical(
            window,
            tr("ffmpeg.bootstrap.failed_title"),
            tr("ffmpeg.bootstrap.verify_failed"),
        )
    elif err:
        QMessageBox.warning(
            window,
            tr("ffmpeg.bootstrap.network_error_title"),
            tr("ffmpeg.bootstrap.network_error_body", detail=err[0]),
        )
    return False


# ---------------------------------------------------------------------------
# Legacy helpers (kept for script compatibility)
# ---------------------------------------------------------------------------


def frozen_bundle_dir() -> Path | None:
    if not _is_frozen():
        return None
    return Path(sys.executable).resolve().parent


def frozen_ffmpeg_dir() -> Path | None:
    base = frozen_bundle_dir()
    if base is None:
        return None
    return base / "ffmpeg"


def download_and_install_to(dest_dir: Path, url: str | None = None) -> None:
    u = url or default_download_url()
    if not u:
        raise RuntimeError("no bundled FFmpeg download for this platform")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / ("bundle.tar.xz" if u.endswith(".tar.xz") else "bundle.zip")
        download_url_to_file(u, tmp)
        install_downloaded_archive(tmp, dest_dir)


def fetch_vendor_directory(repo_root: Path) -> None:
    dest = repo_root / "vendor"
    download_and_install_to(dest)


def ensure_bundled_ffmpeg_with_ui() -> bool:
    """Legacy entry point for frozen-only auto-download (kept for compat)."""
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
            except Exception as e:
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
