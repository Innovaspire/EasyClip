"""Application entry."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox

from easyclip.core.settings import AppSettings
from easyclip.core.theme import init_theme
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
    """Run FFmpeg bootstrap after UI is visible (dev and frozen); do not abort startup on failure."""
    try:
        from easyclip.core.ffmpeg_util import check_ffmpeg_runnable, find_ffmpeg

        ffmpeg, ffprobe = find_ffmpeg()
        ff_ok, _ = check_ffmpeg_runnable(ffmpeg)
        fp_ok, _ = check_ffmpeg_runnable(ffprobe)
        if ff_ok and fp_ok:
            return
    except Exception:
        pass

    from easyclip.core.ffmpeg_bootstrap import ensure_ffmpeg_with_ui, skip_auto_download

    if skip_auto_download():
        return
    if ensure_ffmpeg_with_ui(window):
        return
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


def _fix_macos_app_name() -> None:
    """Set the macOS application menu name to 'EasyClip' via Cocoa.

    Changes both ``NSProcessInfo.processName`` (for ``ps`` / Activity Monitor)
    and the application menu title (the item next to the Apple logo).

    Uses ctypes ObjC bridge with explicit function-pointer casts for ARM64
    (where ``objc_msgSend`` is not variadic and must be called with the
    correct prototype for each argument count).
    """
    import ctypes
    import ctypes.util

    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))  # type: ignore[arg-type]
    except Exception:
        return
    try:
        # -- base function types --
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]

        # -- 2-arg objc_msgSend (id, sel) -> id --
        _msg2 = objc.objc_msgSend
        _msg2.restype = ctypes.c_void_p
        _msg2.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        # -- 3-arg casts --
        _MSG3_ID = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )
        _MSG3_STR = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p
        )
        _MSG3_VOID = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )
        _msg3_id = ctypes.cast(objc.objc_msgSend, _MSG3_ID)
        _msg3_str = ctypes.cast(objc.objc_msgSend, _MSG3_STR)
        _msg3_void = ctypes.cast(objc.objc_msgSend, _MSG3_VOID)

        NSString = objc.objc_getClass(b"NSString")
        if not NSString:
            return

        # Create "EasyClip" NSString once, reuse below.
        sel_str = objc.sel_registerName(b"stringWithUTF8String:")
        name_easyclip = _msg3_str(NSString, sel_str, b"EasyClip")
        if not name_easyclip:
            return

        # 1. Set NSProcessInfo.processName (affects ps / Activity Monitor / Dock).
        NSProcessInfo = objc.objc_getClass(b"NSProcessInfo")
        if NSProcessInfo:
            pi = _msg2(NSProcessInfo, objc.sel_registerName(b"processInfo"))
            if pi:
                _msg3_void(pi, objc.sel_registerName(b"setProcessName:"), name_easyclip)

        # 2. Rename the application menu item (what actually shows next to the Apple logo).
        NSApp_cls = objc.objc_getClass(b"NSApplication")
        if NSApp_cls:
            nsapp = _msg2(NSApp_cls, objc.sel_registerName(b"sharedApplication"))
            if nsapp:
                main_menu = _msg2(nsapp, objc.sel_registerName(b"mainMenu"))
                if main_menu:
                    app_menu_item = _msg3_id(
                        main_menu, objc.sel_registerName(b"itemAtIndex:"), 0
                    )
                    if app_menu_item:
                        _msg3_void(
                            app_menu_item,
                            objc.sel_registerName(b"setTitle:"),
                            name_easyclip,
                        )
    except Exception:
        pass


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("EasyClip")
    app.setApplicationDisplayName("EasyClip")
    app.setOrganizationName("Innovaspire")

    if sys.platform == "darwin":
        _fix_macos_app_name()

    settings = AppSettings()
    init_theme(settings.theme())
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
