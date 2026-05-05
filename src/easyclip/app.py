"""Application entry."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox

from easyclip.i18n.strings import tr
from easyclip.widgets.main_window import MainWindow


def _resolve_app_icon() -> QIcon | None:
    """Resolve app icon path for both dev run and frozen build."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir / "app.ico",
                exe_dir / "_internal" / "app.ico",
            ]
        )
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "app.ico")
    else:
        repo_root = Path(__file__).resolve().parents[2]
        candidates.append(repo_root / "packaging" / "app.ico")

    for path in candidates:
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return None


def _ensure_ffmpeg_after_window_shown(window: MainWindow) -> None:
    """Run frozen FFmpeg bootstrap after UI is visible; do not abort startup on failure."""
    if not getattr(sys, "frozen", False):
        return
    # If ffmpeg is already resolvable (bundled or PATH), skip bootstrap entirely.
    # This avoids unnecessary startup dialogs for users who already have a working setup.
    try:
        from easyclip.core.ffmpeg_util import find_ffmpeg

        ffmpeg, ffprobe = find_ffmpeg()
        if ffmpeg and ffprobe:
            return
    except Exception:
        pass
    from easyclip.core.ffmpeg_bootstrap import ensure_bundled_ffmpeg_with_ui, skip_auto_download

    if skip_auto_download():
        return
    if ensure_bundled_ffmpeg_with_ui():
        return
    # Keep app usable (e.g. settings / basic browsing) even if FFmpeg is unavailable.
    window.statusBar().showMessage(tr("ffmpeg.bootstrap.limited_status"), 15_000)
    QMessageBox.warning(
        window,
        tr("ffmpeg.bootstrap.limited_title"),
        tr("ffmpeg.bootstrap.limited_body"),
    )


def _ensure_window_visible(window: MainWindow) -> None:
    """Force top-level window to a visible, normal state."""
    if not window.isVisible():
        window.show()
    if window.windowState() & Qt.WindowState.WindowMinimized:
        window.showNormal()
    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        fg = window.frameGeometry()
        if not fg.intersects(avail):
            # Recover from off-screen placement (multi-monitor / DPI changes).
            new_w = min(max(window.width(), 960), avail.width())
            new_h = min(max(window.height(), 640), avail.height())
            window.resize(new_w, new_h)
            x = avail.x() + max(0, (avail.width() - new_w) // 2)
            y = avail.y() + max(0, (avail.height() - new_h) // 2)
            window.move(x, y)
    window.raise_()
    window.activateWindow()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("EasyClip")
    app.setOrganizationName("Innovaspire")
    app_icon = _resolve_app_icon()
    if app_icon is not None:
        app.setWindowIcon(app_icon)
    w = MainWindow()
    if app_icon is not None:
        w.setWindowIcon(app_icon)
    w.show()
    _ensure_window_visible(w)
    # A second pass helps on Windows when DWM/taskbar restores stale placement.
    QTimer.singleShot(250, lambda: _ensure_window_visible(w))
    # Delay bootstrap a little so the main window can render first.
    QTimer.singleShot(1200, lambda: _ensure_ffmpeg_after_window_shown(w))
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
