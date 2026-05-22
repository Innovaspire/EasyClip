"""Custom video preview widget using QVideoSink — no native surface, so
subtitle text can be painted directly on top of the video in paintEvent.

Replaces the old QVideoWidget + SubtitleOverlay (top-level Tool window)
combo.  Because everything is painted in a single QWidget, window drag
does not desync the subtitle.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
)
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QSizePolicy, QWidget

from easyclip.core.subtitle import SubtitleTrack

SUBTITLE_BG = QColor(0, 0, 0, 180)
SUBTITLE_FG = QColor(255, 255, 255)
SUBTITLE_PAD_H = 14
SUBTITLE_PAD_V = 8
SUBTITLE_RADIUS = 8.0
SUBTITLE_LINE_SPACING = 2
SUBTITLE_MARGIN_BOTTOM = 0.88  # fraction from top (0=top, 1=bottom)

VIDEO_OPEN_SUFFIXES = frozenset(
    {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".flv", ".wmv", ".mpeg", ".mpg"}
)


class VideoPreviewWidget(QWidget):
    """Widget that receives video frames from QVideoSink and renders subtitles on top."""

    video_dropped = Signal(object)  # Path

    def __init__(self, player: QMediaPlayer | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._player = player
        self._current_image: QImage | None = None
        self._subtitle_track: SubtitleTrack | None = None
        self._active_text: str | None = None
        self._cached_lines: list[str] = []
        self._cached_font_size: int = 0
        self._cached_widget_w: int = 0
        self._cached_bar_rect: QRect | None = None
        self._cached_bar_h: int = 0
        self._cached_bar_frac_y: float = -1.0
        self._subtitle_frac_y: float = SUBTITLE_MARGIN_BOTTOM

        # Drag state for subtitle repositioning
        self._dragging_subtitle: bool = False
        self._drag_origin_y: float = 0.0
        self._drag_frac_start: float = 0.0

        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_video_frame(self, frame: QVideoFrame) -> None:
        if frame.isValid():
            image = frame.toImage()
            if not image.isNull():
                self._current_image = image
                self.update()

    def current_image(self) -> QImage | None:
        return self._current_image

    def set_subtitle_track(self, track: SubtitleTrack | None) -> None:
        self._subtitle_track = track
        self._active_text = None
        self._cached_lines = []
        self._cached_widget_w = 0
        self._cached_bar_rect = None
        if track is not None and self._player is not None:
            self.update_subtitle_position(self._player.position())
        self.update()

    def update_subtitle_position(self, position_ms: int) -> None:
        if self._subtitle_track is None or not self._subtitle_track.entries:
            if self._active_text is not None:
                self._active_text = None
                self._cached_lines = []
                self._cached_bar_rect = None
                self.update()
            return
        t = position_ms / 1000.0
        entry = self._subtitle_track.find_active(t)
        new_text = entry.text if entry else None
        if new_text != self._active_text:
            self._active_text = new_text
            self._cached_lines = []
            self._cached_widget_w = 0
            self._cached_bar_rect = None
            self.update()

    # ------------------------------------------------------------------
    # Drag-drop (video files)
    # ------------------------------------------------------------------

    def _first_video_path(self, md: QMimeData) -> Path | None:
        if md is None or not md.hasUrls():
            return None
        for u in md.urls():
            if not u.isLocalFile():
                continue
            p = Path(u.toLocalFile())
            if p.is_file() and p.suffix.lower() in VIDEO_OPEN_SUFFIXES:
                return p
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._first_video_path(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._first_video_path(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        path = self._first_video_path(event.mimeData())
        if path is not None:
            event.acceptProposedAction()
            self.video_dropped.emit(path)
        else:
            event.ignore()

    # ------------------------------------------------------------------
    # Subtitle dragging
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._active_text:
            if self._subtitle_hit_test(event.position().y()):
                self._dragging_subtitle = True
                self._drag_origin_y = event.position().y()
                self._drag_frac_start = self._subtitle_frac_y
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging_subtitle:
            if not self._active_text:
                self._dragging_subtitle = False
                self.setCursor(Qt.CursorShape.ArrowCursor)
                super().mouseMoveEvent(event)
                return
            dy = event.position().y() - self._drag_origin_y
            h = max(1, self.height())
            # Top clamp only; bottom naturally limited by paint-time clamp
            top_clamp = 8.0 / h
            new_frac = max(top_clamp, min(1.0, self._drag_frac_start + dy / h))
            self._subtitle_frac_y = new_frac
            self._cached_bar_rect = None  # position changed
            self.update()
            event.accept()
            return
        if self._active_text and self._subtitle_hit_test(event.position().y()):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            was_dragging = self._dragging_subtitle
            self._dragging_subtitle = False  # unconditionally reset
            if was_dragging:
                self.setCursor(Qt.CursorShape.OpenHandCursor if (
                    self._active_text and self._subtitle_hit_test(event.position().y())
                ) else Qt.CursorShape.ArrowCursor)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if self._dragging_subtitle:
            self._dragging_subtitle = False  # safety reset
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def _subtitle_bar_rect(self) -> QRect | None:
        """Return the subtitle bar rect (in widget coords), or None if not visible.
        Result is cached until widget size or subtitle text changes."""
        if not self._active_text:
            self._cached_bar_rect = None
            return None
        h = self.height()
        w = self.width()
        if h < 4 or w < 4:
            return None
        # Use cache when widget size, frac, and text haven't changed
        if (self._cached_bar_rect is not None
                and self._cached_widget_w == w
                and self._cached_bar_frac_y == self._subtitle_frac_y):
            return self._cached_bar_rect

        font_size = max(14, int(round(h * 0.045)))
        font = QFont(self.font().defaultFamily(), font_size)
        fm = QFontMetrics(font)
        line_count = len(self._cached_lines) if self._cached_lines else 1
        line_h = fm.height()
        text_h = line_h * line_count + SUBTITLE_LINE_SPACING * max(0, line_count - 1)
        bar_h = text_h + 2 * SUBTITLE_PAD_V
        self._cached_bar_h = bar_h
        bar_w = max(fm.horizontalAdvance(line) for line in (self._cached_lines or [" "])) + 2 * SUBTITLE_PAD_H
        top_frac = 8.0 / max(h, 1)
        clamped_frac = max(top_frac, min(self._subtitle_frac_y, 1.0))
        bar_x = (w - bar_w) // 2
        bar_y = int(h * clamped_frac) - bar_h
        bar_x = max(0, min(bar_x, w - bar_w))
        bar_y = max(0, min(bar_y, h - bar_h))
        self._cached_bar_rect = QRect(bar_x, bar_y, bar_w, bar_h)
        self._cached_bar_frac_y = self._subtitle_frac_y
        return self._cached_bar_rect

    def _subtitle_hit_test(self, y: float) -> bool:
        """Return True if *y* is within the subtitle bounding box."""
        r = self._subtitle_bar_rect()
        return r is not None and r.y() <= y <= r.y() + r.height()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w, h = self.width(), self.height()
        if w < 2 or h < 2:
            painter.end()
            return

        # --- video frame ---
        if self._current_image is not None and not self._current_image.isNull():
            target = self._fit_rect(self._current_image.size(), QSize(w, h))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self._current_image)
            # Fill letterbox areas with black
            painter.fillRect(0, 0, w, target.y(), QColor(0, 0, 0))
            painter.fillRect(0, target.y() + target.height(), w, h - target.y() - target.height(), QColor(0, 0, 0))
        else:
            painter.fillRect(0, 0, w, h, QColor(20, 20, 25))
            painter.setPen(QColor(120, 120, 130))
            painter.setFont(QFont(painter.font().defaultFamily(), 14))
            painter.drawText(QRect(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "Drop a video file here")

        # --- subtitle ---
        if self._active_text:
            self._draw_subtitle(painter, w, h)

        painter.end()

    def _fit_rect(self, img_size: QSize, widget_size: QSize) -> QRect:
        """Return a QRect that scales *img_size* proportionally into *widget_size*, centred."""
        iw, ih = img_size.width(), img_size.height()
        ww, wh = widget_size.width(), widget_size.height()
        if iw <= 0 or ih <= 0:
            return QRect(0, 0, ww, wh)
        scale = min(ww / iw, wh / ih)
        dw = int(iw * scale)
        dh = int(ih * scale)
        dx = (ww - dw) // 2
        dy = (wh - dh) // 2
        return QRect(dx, dy, dw, dh)

    def _draw_subtitle(self, painter: QPainter, w: int, h: int) -> None:
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            font_size = max(14, int(round(h * 0.045)))
            max_text_w = int(w * 0.85)
            font = QFont(painter.font().defaultFamily(), font_size)
            fm = QFontMetrics(font)

            # Recompute line wrapping when dimensions change or cache is stale
            if self._cached_widget_w != w or self._cached_font_size != font_size or not self._cached_lines:
                self._cached_widget_w = w
                self._cached_font_size = font_size
                self._cached_lines = _wrap_text(font, self._active_text, max_text_w, fm)

            if not self._cached_lines:
                return

            painter.setFont(font)
            line_h = fm.height()

            # Use the same bar rect as _subtitle_hit_test so paint and hit-test match
            bar_rect = self._subtitle_bar_rect()
            if bar_rect is None:
                return
            bar_x, bar_y, bar_w, bar_h = bar_rect.x(), bar_rect.y(), bar_rect.width(), bar_rect.height()

            # Background pill
            path = QPainterPath()
            path.addRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), SUBTITLE_RADIUS, SUBTITLE_RADIUS)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(SUBTITLE_BG)
            painter.drawPath(path)

            # Text
            painter.setPen(SUBTITLE_FG)
            y = bar_y + SUBTITLE_PAD_V
            for i, line in enumerate(self._cached_lines):
                line_rect = QRectF(bar_x + SUBTITLE_PAD_H, y, bar_w - 2 * SUBTITLE_PAD_H, line_h)
                painter.drawText(line_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextSingleLine, line)
                y += line_h + SUBTITLE_LINE_SPACING
        finally:
            painter.restore()


def _wrap_text(font: QFont, text: str, max_width: int, fm: QFontMetrics) -> list[str]:
    """Word-wrap text into lines that fit within max_width pixels. Caps at 3 lines."""
    words = text.split()
    lines: list[str] = []
    current_line: str = ""

    for word in words:
        test = f"{current_line} {word}".strip()
        if fm.horizontalAdvance(test) <= max_width:
            current_line = test
        else:
            if current_line:
                lines.append(current_line)
            if fm.horizontalAdvance(word) > max_width:
                current_line = ""
                for ch in word:
                    if fm.horizontalAdvance(current_line + ch) > max_width:
                        if current_line:
                            lines.append(current_line)
                            current_line = ""
                    current_line += ch
            else:
                current_line = word
    if current_line:
        lines.append(current_line)

    if not lines:
        lines = [text]

    if len(lines) > 3:
        lines = lines[:3]

    return lines
