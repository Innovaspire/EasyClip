"""YouTube-style draggable subtitle overlay above QVideoWidget.

On Windows, QVideoWidget is backed by a native HWND that paints above any child
QWidget.  To render visible subtitles we use a borderless top-level tool window
positioned over the video widget.  The window is sized to tightly fit the text
block so clicks outside it pass through to the video naturally.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPainterPath, QResizeEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QWidget

from easyclip.core.subtitle import SubtitleTrack

_BAR_BG = QColor(0, 0, 0, 180)
_TEXT_COLOR = QColor(255, 255, 255)
_TEXT_PAD_H = 14
_TEXT_PAD_V = 8
_LINE_SPACING = 2
_CORNER_RADIUS = 8.0
_BOTTOM_MARGIN_FRAC = 0.12  # default: 12% from bottom


class SubtitleOverlay(QWidget):
    """Borderless tool window that renders a draggable subtitle pill.

    Parented to the main window so it doesn't get a taskbar entry.
    Sized to tightly fit the active subtitle text + background.
    """

    def __init__(
        self,
        video_widget: QVideoWidget,
        player: QMediaPlayer,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._video = video_widget
        self._player = player
        self._track: SubtitleTrack | None = None
        self._active_text: str | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)

        # Position as fraction of video dimensions (0..1).  Persisted across
        # text changes so multi-line subtitles stay near the same spot.
        self._pos_frac_x: float = 0.5   # centred
        self._pos_frac_y: float = 1.0 - _BOTTOM_MARGIN_FRAC  # near bottom

        # Pre-computed text layout (cached until text or video width changes)
        self._cached_lines: list[str] = []
        self._cached_font_size: int = 0
        self._cached_video_w: int = 0

        # Dragging state
        self._dragging: bool = False
        self._drag_origin: QPoint | None = None  # global mouse pos at press
        self._drag_win_origin: QPoint | None = None  # window pos at press

        # Track video widget size / position changes
        self._video.installEventFilter(self)
        if parent is not None:
            parent.installEventFilter(self)

        self._player.positionChanged.connect(self._on_position)
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_track(self, track: SubtitleTrack | None) -> None:
        self._track = track
        self._active_text = None
        self._cached_lines = []
        if track is None:
            self.hide()
        else:
            # Immediately check the current player position so the overlay
            # shows the active subtitle without waiting for a seek/play.
            self._on_position(self._player.position())
        self.update()

    def set_video_widget(self, video_widget: QVideoWidget) -> None:
        if self._video is not None:
            self._video.removeEventFilter(self)
        self._video = video_widget
        video_widget.installEventFilter(self)
        self._active_text = None
        self._cached_lines = []
        self.hide()
        self.update()

    def sync_geometry(self) -> None:
        """Reposition and resize to match the current active text."""
        if not self._active_text or self._track is None:
            self.hide()
            return
        self._layout_and_show()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def eventFilter(self, watched: object, event) -> bool:
        if watched is self._video or watched is self.parentWidget():
            if event.type() in (
                event.Type.Resize,
                event.Type.Move,
                event.Type.Show,
            ):
                # Invalidate layout cache so next paint re-measures
                self._cached_video_w = 0
                if self._active_text:
                    self._layout_and_show()
        return False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._active_text:
            self._layout_and_show()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._active_text:
            self._layout_and_show()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_origin = event.globalPosition().toPoint()
            self._drag_win_origin = self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and self._drag_origin is not None and self._drag_win_origin is not None:
            delta = event.globalPosition().toPoint() - self._drag_origin
            new_pos = self._drag_win_origin + delta
            clamped = self._clamp_to_video(new_pos)
            self.move(clamped)
            self._update_pos_fractions(clamped)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._drag_origin = None
            self._drag_win_origin = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
        event.accept()

    def enterEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def leaveEvent(self, event) -> None:
        if not self._dragging:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def paintEvent(self, event) -> None:
        if not self._active_text or not self._cached_lines:
            return

        w = self.width()
        h = self.height()
        if w < 4 or h < 4:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        font = QFont(painter.font().defaultFamily(), self._cached_font_size)
        painter.setFont(font)
        fm = QFontMetrics(font)
        line_h = fm.height()

        path = QPainterPath()
        rect = QRectF(0, 0, w, h)
        path.addRoundedRect(rect, _CORNER_RADIUS, _CORNER_RADIUS)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_BAR_BG)
        painter.drawPath(path)

        painter.setPen(_TEXT_COLOR)
        y = _TEXT_PAD_V
        for i, line in enumerate(self._cached_lines):
            line_rect = QRectF(_TEXT_PAD_H, y, w - 2 * _TEXT_PAD_H, line_h)
            painter.drawText(line_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextSingleLine, line)
            y += line_h + _LINE_SPACING

        painter.end()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_position(self, position_ms: int) -> None:
        if self._track is None or not self._track.entries:
            if self._active_text is not None:
                self._active_text = None
                self._cached_lines = []
                self.hide()
            return
        t = position_ms / 1000.0
        entry = self._track.find_active(t)
        new_text = entry.text if entry else None
        if new_text == self._active_text:
            return
        self._active_text = new_text
        self._cached_lines = []
        self._cached_video_w = 0
        if new_text is None:
            self.hide()
        else:
            self._layout_and_show()

    def _layout_and_show(self) -> None:
        """Measure text, position the window, and show it."""
        if not self._active_text:
            self.hide()
            return

        video = self._video
        if video is None:
            return
        vw = max(1, video.width())
        vh = max(1, video.height())
        if vw < 16 or vh < 16:
            self.hide()
            return

        font_size = max(14, int(round(vh * 0.045)))
        max_text_w = int(vw * 0.85)  # max 85% of video width

        font = QFont(self.font().defaultFamily(), font_size)
        fm = QFontMetrics(font)
        line_h = fm.height()

        # Recompute line wrapping only when needed
        if self._cached_video_w != vw or self._cached_font_size != font_size:
            self._cached_video_w = vw
            self._cached_font_size = font_size
            self._cached_lines = _wrap_text(font, self._active_text, max_text_w, fm)

        if not self._cached_lines:
            self.hide()
            return

        # Measure text block
        text_block_w = max(fm.horizontalAdvance(line) for line in self._cached_lines)
        win_w = text_block_w + 2 * _TEXT_PAD_H
        win_h = line_h * len(self._cached_lines) + _LINE_SPACING * max(0, len(self._cached_lines) - 1) + 2 * _TEXT_PAD_V

        # Compute position from saved fractions
        video_global_tl = video.mapToGlobal(QPoint(0, 0))
        x = video_global_tl.x() + int((vw - win_w) * self._pos_frac_x)
        y = video_global_tl.y() + int((vh - win_h) * self._pos_frac_y)

        # Clamp
        x = max(video_global_tl.x(), min(x, video_global_tl.x() + vw - win_w))
        y = max(video_global_tl.y(), min(y, video_global_tl.y() + vh - win_h))

        if self.parentWidget() is not None:
            local_tl = self.parentWidget().mapFromGlobal(QPoint(x, y))
            self.setGeometry(QRect(local_tl.x(), local_tl.y(), win_w, win_h))
        else:
            self.setGeometry(QRect(x, y, win_w, win_h))

        if not self.isVisible():
            self.show()
        self.update()

    def _clamp_to_video(self, pos: QPoint) -> QPoint:
        """Clamp a top-level window position so the window stays inside the video widget."""
        video = self._video
        if video is None:
            return pos
        video_global = video.mapToGlobal(QPoint(0, 0))
        vw = max(1, video.width())
        vh = max(1, video.height())
        ww = self.width()
        wh = self.height()
        x = max(video_global.x(), min(pos.x(), video_global.x() + vw - ww))
        y = max(video_global.y(), min(pos.y(), video_global.y() + vh - wh))
        return QPoint(x, y)

    def _update_pos_fractions(self, pos: QPoint) -> None:
        """Store position as fraction of video dimensions so it scales on resize."""
        video = self._video
        if video is None:
            return
        video_global = video.mapToGlobal(QPoint(0, 0))
        vw = max(1, video.width())
        vh = max(1, video.height())
        ww = max(1, self.width())
        wh = max(1, self.height())
        range_x = max(1, vw - ww)
        range_y = max(1, vh - wh)
        self._pos_frac_x = max(0.0, min(1.0, (pos.x() - video_global.x()) / range_x))
        self._pos_frac_y = max(0.0, min(1.0, (pos.y() - video_global.y()) / range_y))


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
