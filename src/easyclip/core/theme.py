"""Theme system: light / dark / follow-system with palette, stylesheet, and custom widget colors."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


class Theme(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class WidgetColors:
    """Colors used by custom-painted widgets (timeline, waveform) and inline stylesheets."""

    # ── Timeline ──────────────────────────────────────────────
    timeline_ruler_bg: QColor
    timeline_track_bg: QColor
    timeline_separator: QColor
    timeline_major_tick: QColor
    timeline_label: QColor
    timeline_minor_tick: QColor
    timeline_border: QColor
    timeline_viewport_dot: QColor

    # ── Waveform ──────────────────────────────────────────────
    waveform_bg: QColor
    waveform_loading: QColor
    waveform_empty: QColor
    waveform_error: QColor

    # ── Stylesheet hex snippets ───────────────────────────────
    preview_label_bg: str
    preview_label_fg: str
    thumb_bg: str
    thumb_border: str
    thumb_active_border: str
    splitter_handle: str
    splitter_handle_hover: str
    thumb_duration_fg: str
    source_path_fg: str
    clip_del_btn_fg: str
    clip_del_btn_border: str
    clip_del_btn_bg: str
    clip_del_btn_hover_bg: str

    # ── Crosshair / playhead ──────────────────────────────────
    playhead_color: QColor
    crosshair_color: QColor

    # ── Text icon ─────────────────────────────────────────────
    text_icon_color: str

    @property
    def preview_label_ss(self) -> str:
        return f"background:{self.preview_label_bg};color:{self.preview_label_fg};"

    @property
    def thumb_base_ss(self) -> str:
        return f"border:1px solid {self.thumb_border};background:{self.thumb_bg};"

    @property
    def thumb_active_ss(self) -> str:
        return f"border:2px solid {self.thumb_active_border};background:{self.thumb_bg};"

    @property
    def splitter_ss(self) -> str:
        return (
            f"QSplitter::handle {{ background: {self.splitter_handle}; }} "
            f"QSplitter::handle:hover {{ background: {self.splitter_handle_hover}; }}"
        )

    @property
    def thumb_duration_ss(self) -> str:
        return f"color:{self.thumb_duration_fg};"

    @property
    def source_path_ss(self) -> str:
        return f"color:{self.source_path_fg}; padding: 2px 0;"

    @property
    def clip_del_btn_ss(self) -> str:
        return (
            f"QPushButton {{ color:{self.clip_del_btn_fg}; "
            f"border:1px solid {self.clip_del_btn_border}; "
            f"border-radius:4px; background:{self.clip_del_btn_bg}; }} "
            f"QPushButton:hover {{ background:{self.clip_del_btn_hover_bg}; }}"
        )


# ── Dark palette ──────────────────────────────────────────────
_DARK = WidgetColors(
    timeline_ruler_bg=QColor(46, 46, 54),
    timeline_track_bg=QColor(34, 34, 40),
    timeline_separator=QColor(24, 24, 28),
    timeline_major_tick=QColor(140, 140, 150),
    timeline_label=QColor(200, 200, 210),
    timeline_minor_tick=QColor(70, 70, 78),
    timeline_border=QColor(100, 100, 110),
    timeline_viewport_dot=QColor(120, 200, 255),
    waveform_bg=QColor(28, 28, 32),
    waveform_loading=QColor(180, 160, 90),
    waveform_empty=QColor(120, 120, 120),
    waveform_error=QColor(200, 100, 100),
    playhead_color=QColor(255, 220, 80),
    crosshair_color=QColor(255, 255, 255),
    preview_label_bg="#111111",
    preview_label_fg="#888888",
    thumb_bg="#222222",
    thumb_border="#555555",
    thumb_active_border="#4da3ff",
    splitter_handle="#4a4a52",
    splitter_handle_hover="#5c8a9a",
    thumb_duration_fg="#b8b8b8",
    source_path_fg="#a8a8a8",
    clip_del_btn_fg="#ee4444",
    clip_del_btn_border="#994444",
    clip_del_btn_bg="#2b1a1a",
    clip_del_btn_hover_bg="#3a2020",
    text_icon_color="#E6E6E6",
)

# ── Light palette (low-saturation warm paper tones) ──────────
_LIGHT = WidgetColors(
    timeline_ruler_bg=QColor(216, 214, 207),
    timeline_track_bg=QColor(232, 230, 224),
    timeline_separator=QColor(196, 194, 188),
    timeline_major_tick=QColor(138, 135, 129),
    timeline_label=QColor(80, 78, 74),
    timeline_minor_tick=QColor(188, 186, 180),
    timeline_border=QColor(175, 172, 167),
    timeline_viewport_dot=QColor(70, 125, 185),
    waveform_bg=QColor(235, 233, 226),
    waveform_loading=QColor(155, 125, 35),
    waveform_empty=QColor(165, 162, 157),
    waveform_error=QColor(185, 65, 65),
    playhead_color=QColor(210, 160, 25),
    crosshair_color=QColor(100, 97, 93),
    preview_label_bg="#eae8e0",
    preview_label_fg="#6b6963",
    thumb_bg="#e7e5dd",
    thumb_border="#c5c2b9",
    thumb_active_border="#6a9ec0",
    splitter_handle="#c5c2b9",
    splitter_handle_hover="#9bb6c2",
    thumb_duration_fg="#787670",
    source_path_fg="#6b6963",
    clip_del_btn_fg="#b5443f",
    clip_del_btn_border="#d4b8b5",
    clip_del_btn_bg="#f2e8e7",
    clip_del_btn_hover_bg="#ebdddb",
    text_icon_color="#52514c",
)


def _system_is_dark() -> bool:
    """Detect OS-level dark mode preference. Defaults to dark on unknown platforms."""
    if sys.platform == "darwin":
        import subprocess

        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return result.returncode == 0 and "dark" in result.stdout.lower()
        except Exception:
            return False
    if sys.platform == "win32":
        try:
            import winreg
        except Exception:
            return False
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return val == 0
        except Exception:
            return False
    # Linux: check gsettings
    try:
        import subprocess

        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return "dark" in result.stdout.lower()
    except Exception:
        return False


def _system_theme() -> Theme:
    return Theme.DARK if _system_is_dark() else Theme.LIGHT


_current_theme: Theme = Theme.SYSTEM
_widget_colors: WidgetColors = _DARK  # default to dark until first resolve

_on_theme_changed_callbacks: list[Callable[[WidgetColors], None]] = []


def effective_theme() -> Theme:
    if _current_theme == Theme.SYSTEM:
        return _system_theme()
    return _current_theme


def current_theme() -> Theme:
    return _current_theme


def widget_colors() -> WidgetColors:
    return _widget_colors


def set_theme(theme: Theme) -> None:
    global _current_theme, _widget_colors
    _current_theme = theme
    eff = effective_theme()
    _widget_colors = _DARK if eff == Theme.DARK else _LIGHT
    _apply_palette(eff)
    for cb in _on_theme_changed_callbacks:
        cb(_widget_colors)


def on_theme_changed(callback: Callable[[WidgetColors], None]) -> None:
    _on_theme_changed_callbacks.append(callback)


def _apply_palette(theme: Theme) -> None:
    """Apply a light or dark QPalette + minimal global stylesheet."""
    app = QApplication.instance()
    if app is None:
        return

    if theme == Theme.DARK:
        p = QPalette()
        p.setColor(QPalette.ColorRole.Window, QColor(43, 43, 50))
        p.setColor(QPalette.ColorRole.WindowText, QColor(225, 225, 230))
        p.setColor(QPalette.ColorRole.Base, QColor(30, 30, 36))
        p.setColor(QPalette.ColorRole.AlternateBase, QColor(43, 43, 50))
        p.setColor(QPalette.ColorRole.ToolTipBase, QColor(50, 50, 58))
        p.setColor(QPalette.ColorRole.ToolTipText, QColor(225, 225, 230))
        p.setColor(QPalette.ColorRole.Text, QColor(225, 225, 230))
        p.setColor(QPalette.ColorRole.Button, QColor(53, 53, 60))
        p.setColor(QPalette.ColorRole.ButtonText, QColor(225, 225, 230))
        p.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
        p.setColor(QPalette.ColorRole.Link, QColor(80, 160, 240))
        p.setColor(QPalette.ColorRole.Highlight, QColor(70, 120, 200))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor(240, 240, 245))
        p.setColor(QPalette.ColorRole.PlaceholderText, QColor(130, 130, 140))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(130, 130, 140))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(130, 130, 140))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(130, 130, 140))
        app.setPalette(p)
        app.setStyleSheet(_GLOBAL_DARK_SS)
    else:
        p = QPalette()
        p.setColor(QPalette.ColorRole.Window, QColor(234, 232, 226))
        p.setColor(QPalette.ColorRole.WindowText, QColor(61, 60, 55))
        p.setColor(QPalette.ColorRole.Base, QColor(242, 240, 234))
        p.setColor(QPalette.ColorRole.AlternateBase, QColor(237, 235, 229))
        p.setColor(QPalette.ColorRole.ToolTipBase, QColor(245, 243, 237))
        p.setColor(QPalette.ColorRole.ToolTipText, QColor(61, 60, 55))
        p.setColor(QPalette.ColorRole.Text, QColor(61, 60, 55))
        p.setColor(QPalette.ColorRole.Button, QColor(242, 240, 234))
        p.setColor(QPalette.ColorRole.ButtonText, QColor(61, 60, 55))
        p.setColor(QPalette.ColorRole.BrightText, QColor(190, 50, 50))
        p.setColor(QPalette.ColorRole.Link, QColor(55, 105, 175))
        p.setColor(QPalette.ColorRole.Highlight, QColor(130, 160, 195))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor(245, 243, 237))
        p.setColor(QPalette.ColorRole.PlaceholderText, QColor(158, 155, 148))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(158, 155, 148))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(158, 155, 148))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(158, 155, 148))
        app.setPalette(p)
        app.setStyleSheet(_GLOBAL_LIGHT_SS)


_GLOBAL_DARK_SS = """
QToolTip { color: #e0e0e6; background: #32323a; border: 1px solid #50505c; padding: 4px; }
QMenu { background: #3a3a44; color: #e0e0e6; border: 1px solid #50505c; }
QMenu::item:selected { background: #4a6a9a; }
QMenuBar { background: #353540; color: #e0e0e6; }
QMenuBar::item:selected { background: #4a6a9a; }
QComboBox { background: #353540; color: #e0e0e6; border: 1px solid #50505c; padding: 3px 6px; border-radius: 4px; }
QComboBox QAbstractItemView { background: #3a3a44; color: #e0e0e6; selection-background-color: #4a6a9a; }
QSpinBox, QDoubleSpinBox { background: #353540; color: #e0e0e6; border: 1px solid #50505c; padding: 3px; border-radius: 4px; }
QSpinBox:disabled, QDoubleSpinBox:disabled { color: #6a6a78; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 18px; background: #404050; border: 1px solid #50505c; border-top-right-radius: 3px; }
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover { background: #505068; }
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed { background: #606080; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 18px; background: #404050; border: 1px solid #50505c; border-bottom-right-radius: 3px; }
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: #505068; }
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed { background: #606080; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { width: 10px; height: 8px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { width: 10px; height: 8px; }
QLineEdit { background: #2e2e36; color: #e0e0e6; border: 1px solid #50505c; padding: 3px 6px; border-radius: 4px; }
QPlainTextEdit { background: #2e2e36; color: #e0e0e6; border: 1px solid #50505c; }
QTabWidget::pane { border: 1px solid #50505c; background: #353540; }
QTabBar::tab { background: #3a3a44; color: #c0c0c8; padding: 6px 14px; border: 1px solid #50505c; }
QTabBar::tab:selected { background: #4a4a54; color: #f0f0f5; }
QGroupBox { color: #e0e0e6; border: 1px solid #50505c; border-radius: 4px; margin-top: 1em; padding-top: 0.5em; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QScrollBar:vertical { background: #2a2a32; width: 12px; border-radius: 6px; }
QScrollBar::handle:vertical { background: #555562; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #2a2a32; height: 12px; border-radius: 6px; }
QScrollBar::handle:horizontal { background: #555562; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QStatusBar { background: #2e2e38; color: #b0b0b8; }
QDialog { background: #3a3a44; }
"""

_GLOBAL_LIGHT_SS = """
QToolTip { color: #3d3c37; background: #f5f3ed; border: 1px solid #c5c2b9; padding: 4px; }
QMenu { background: #f2f0ea; color: #3d3c37; border: 1px solid #c5c2b9; }
QMenu::item:selected { background: #cddbe8; }
QMenuBar { background: #eae8e1; color: #3d3c37; }
QMenuBar::item:selected { background: #cddbe8; }
QPushButton { background: #f2f0ea; color: #3d3c37; border: 1px solid #c5c2b9; border-radius: 4px; padding: 3px 8px; }
QPushButton:hover { background: #faf9f3; border-color: #adaaa1; }
QPushButton:pressed { background: #e0ded6; border-color: #adaaa1; }
QPushButton:disabled { color: #a09d95; border-color: #d5d2ca; }
QToolButton { background: #f2f0ea; color: #3d3c37; border: 1px solid #c5c2b9; border-radius: 4px; padding: 2px 5px; }
QToolButton:hover { background: #faf9f3; border-color: #adaaa1; }
QToolButton:pressed { background: #e0ded6; }
QComboBox { background: #f2f0ea; color: #3d3c37; border: 1px solid #c5c2b9; padding: 3px 6px; border-radius: 4px; }
QComboBox:hover { border-color: #adaaa1; }
QComboBox QAbstractItemView { background: #f2f0ea; color: #3d3c37; selection-background-color: #cddbe8; }
QSpinBox, QDoubleSpinBox { background: #f2f0ea; color: #3d3c37; border: 1px solid #c5c2b9; padding: 3px; border-radius: 4px; }
QSpinBox:disabled, QDoubleSpinBox:disabled { color: #a09d95; }
QSpinBox:hover, QDoubleSpinBox:hover { border-color: #adaaa1; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 18px; background: #e8e5dd; border: 1px solid #c5c2b9; border-top-right-radius: 3px; }
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover { background: #d8d5cc; }
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed { background: #c5c2b9; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 18px; background: #e8e5dd; border: 1px solid #c5c2b9; border-bottom-right-radius: 3px; }
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: #d8d5cc; }
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed { background: #c5c2b9; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { width: 10px; height: 8px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { width: 10px; height: 8px; }
QLineEdit { background: #f2f0ea; color: #3d3c37; border: 1px solid #c5c2b9; padding: 3px 6px; border-radius: 4px; }
QLineEdit:hover { border-color: #adaaa1; }
QPlainTextEdit { background: #f2f0ea; color: #3d3c37; border: 1px solid #c5c2b9; }
QTabWidget::pane { border: 1px solid #c5c2b9; background: #edebe4; }
QTabBar::tab { background: #e0ded6; color: #5c5a54; padding: 6px 14px; border: 1px solid #c5c2b9; }
QTabBar::tab:selected { background: #edebe4; color: #3d3c37; }
QGroupBox { color: #3d3c37; border: 1px solid #c5c2b9; border-radius: 4px; margin-top: 1em; padding-top: 0.5em; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QScrollBar:vertical { background: #e0ded6; width: 12px; border-radius: 6px; }
QScrollBar::handle:vertical { background: #b0aca3; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #98958c; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #e0ded6; height: 12px; border-radius: 6px; }
QScrollBar::handle:horizontal { background: #b0aca3; border-radius: 5px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #98958c; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QStatusBar { background: #e0ded6; color: #6b6963; }
QDialog { background: #eae8e1; }
"""


def init_theme(theme: Theme) -> None:
    """Call once at startup. Resolves SYSTEM to the actual theme and applies it."""
    set_theme(theme)
