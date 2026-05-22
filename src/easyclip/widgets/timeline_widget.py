"""Timeline with ruler, playhead, clip regions, zoomed view, and hover crosshair."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from easyclip.core.project import Clip
from easyclip.core.theme import WidgetColors, on_theme_changed, widget_colors
from easyclip.i18n.strings import tr

RULER_H = 24
TRACK_PAD = 4
MIN_INBOX_LABEL_W = 22
MAX_BADGES_PER_CLUSTER = 5
BADGE_CLUSTER_GAP = 16


def _nice_major_step(span_frames: int, target_ticks: int = 8) -> int:
    """Pick a 'round' frame step so about ``target_ticks`` majors fit in ``span_frames``."""
    if span_frames <= 0:
        return 1
    raw = max(1, span_frames // max(3, target_ticks))
    candidates = (
        1,
        2,
        5,
        10,
        15,
        25,
        50,
        100,
        150,
        250,
        500,
        750,
        1000,
        1500,
        2500,
        5000,
        7500,
        10_000,
        15_000,
        30_000,
        60_000,
        120_000,
        300_000,
        600_000,
    )
    for c in candidates:
        if c >= raw:
            return c
    step = 10 ** int(math.log10(raw))
    return max(1, ((raw + step - 1) // step) * step)


def _format_time_sec(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    if sec >= 3600:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        return f"{h}:{m:02d}:{s:02d}"
    m = int(sec // 60)
    s = sec - m * 60
    if abs(s - round(s)) < 0.05:
        return f"{m}:{int(round(s)):02d}"
    return f"{m}:{s:04.1f}"


def _format_time_precise(sec: float) -> str:
    """Millisecond-precise time string for playhead / hover labels."""
    if sec < 0:
        sec = 0.0
    ms = int(round(sec * 1000.0))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    if h:
        return f"{h}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m}:{s:02d}.{ms:03d}"


_TIME_LABEL_BG = QColor(0, 0, 0, 150)
_TIME_LABEL_FG_HOVER = QColor(220, 200, 120)
_TIME_LABEL_Y = 2
_TIME_LABEL_PAD_H = 4
_TIME_LABEL_PAD_V = 1


def _draw_precise_time_labels(
    painter: QPainter,
    w: int,
    x_for_frame,
    current_frame: int,
    hover_frame: int | None,
    fps: float,
    wc: WidgetColors,
) -> None:
    """Draw current-frame and hover time labels in the ruler area."""
    painter.save()
    try:
        font = QFont()
        font.setPixelSize(10)
        painter.setFont(font)
        fm = painter.fontMetrics()

        # Current frame (playhead) time — always drawn
        cur_sec = current_frame / max(fps, 1e-6)
        cur_text = _format_time_precise(cur_sec)
        cur_x = x_for_frame(current_frame)
        _draw_time_label(painter, fm, cur_text, cur_x, w, wc.playhead_color)

        # Hover time — drawn when mouse is inside the widget and differs from current
        if hover_frame is not None and hover_frame != current_frame:
            hover_sec = hover_frame / max(fps, 1e-6)
            hover_text = _format_time_precise(hover_sec)
            hover_x = x_for_frame(hover_frame)
            _draw_time_label(painter, fm, hover_text, hover_x, w, _TIME_LABEL_FG_HOVER)
    finally:
        painter.restore()


def _draw_time_label(
    painter: QPainter,
    fm,
    text: str,
    cx: int,
    w: int,
    fg: QColor,
) -> None:
    tw = fm.horizontalAdvance(text)
    bw = tw + 2 * _TIME_LABEL_PAD_H
    bh = fm.height() + 2 * _TIME_LABEL_PAD_V
    # Clamp x so the label stays inside the widget
    x = max(0, min(w - bw, cx - bw // 2))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_TIME_LABEL_BG)
    painter.drawRoundedRect(QRect(x, _TIME_LABEL_Y, bw, bh), 3, 3)
    painter.setPen(QPen(fg))
    painter.drawText(x + _TIME_LABEL_PAD_H, _TIME_LABEL_Y + _TIME_LABEL_PAD_V + fm.ascent(), text)


class TimelineWidget(QWidget):
    seek_frame = Signal(int)
    reset_view = Signal()
    # int frame index, or None when the pointer leaves the widget
    crosshair_hover = Signal(object)
    # Right-click / right-drag on a closed clip block (same row index as clip list).
    clip_select_requested = Signal(int)
    clip_drag_delta = Signal(int, int)  # clip_idx, delta_frames (may be 0; used for view-edge pan)
    clip_drag_finished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._wc = widget_colors()
        self._rmb_clip_idx: int | None = None
        self._rmb_last_x: float = 0.0
        self._rmb_drag_frac: float = 0.0
        self._total = 1
        self._current = 0
        self._clips: list[Clip] = []
        self._view_start = 0
        self._view_span = 1
        self._fps = 30.0
        self._sync_crosshair_frame: int | None = None
        self._selected_clip_index: int | None = None
        self._hover_clip_index: int | None = None
        self._hover_frame: int | None = None
        self.setMinimumHeight(RULER_H + 36)
        on_theme_changed(self._on_theme_changed)

    def _on_theme_changed(self, wc: WidgetColors) -> None:
        self._wc = wc
        self.update()

    def set_state(
        self,
        total_frames: int,
        current_frame: int,
        clips: list[Clip],
        *,
        view_start: int = 0,
        view_span: int | None = None,
        fps: float = 30.0,
        selected_clip_index: int | None = None,
    ) -> None:
        self._total = max(1, total_frames)
        self._current = max(0, min(current_frame, self._total - 1))
        self._clips = clips
        self._view_start = max(0, min(view_start, self._total - 1))
        span = self._total if view_span is None else max(1, view_span)
        self._view_span = min(self._total, max(1, span))
        if self._view_start + self._view_span > self._total:
            self._view_start = max(0, self._total - self._view_span)
        self._fps = max(fps, 1e-6)
        if selected_clip_index is None or not (0 <= selected_clip_index < len(self._clips)):
            self._selected_clip_index = None
        else:
            self._selected_clip_index = int(selected_clip_index)
        self.update()

    def set_sync_crosshair_frame(self, frame: int | None) -> None:
        if frame == self._sync_crosshair_frame:
            return
        self._sync_crosshair_frame = frame
        self.update()

    def _frame_at_x(self, x: float) -> int:
        w = max(1, self.width())
        xf = max(0.0, min(1.0, x / w))
        f = int(self._view_start + xf * self._view_span)
        return max(0, min(f, self._total - 1))

    def _x_for_frame(self, frame: int) -> int:
        w = max(1, self.width())
        if self._view_span <= 0:
            return 0
        return int((frame - self._view_start) * w / self._view_span)

    def _visible_clip_geometries(self) -> list[tuple[int, int, int, int]]:
        """Return visible closed clips as tuples: (clip_idx, x0, x1, width)."""
        geoms: list[tuple[int, int, int, int]] = []
        ttot = max(1, self._total)
        v0 = max(0, min(self._view_start, ttot - 1))
        vs = max(1, min(self._view_span, ttot))
        v1 = v0 + vs - 1
        for idx, c in enumerate(self._clips):
            if c.end_frame is None:
                continue
            s = max(c.start_frame, v0)
            e = min(c.end_frame, v1)
            if e < v0 or s > v1:
                continue
            x0 = self._x_for_frame(s)
            x1 = self._x_for_frame(e + 1)
            ww = max(1, x1 - x0)
            geoms.append((idx, x0, x1, ww))
        return geoms

    def _clip_index_at_pos(self, x: float, y: float) -> int | None:
        """Hit-test clip at pointer; when overlapping, prefer the topmost (last drawn)."""
        if y < RULER_H + TRACK_PAD or y > self.height() - TRACK_PAD:
            return None
        xi = int(x)
        geoms = self._visible_clip_geometries()
        for idx, x0, x1, _ in reversed(geoms):
            if x0 <= xi < x1:
                return idx
        return None

    def _set_hover_clip(self, idx: int | None) -> None:
        if idx == self._hover_clip_index:
            return
        self._hover_clip_index = idx
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._rmb_clip_idx is None:
            self.crosshair_hover.emit(None)
            self._set_hover_clip(None)
            self._hover_frame = None
            QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        px = event.position().x()
        py = event.position().y()
        if self._rmb_clip_idx is not None:
            dx = px - self._rmb_last_x
            self._rmb_last_x = px
            w = max(1, self.width())
            vs = max(1, self._view_span)
            self._rmb_drag_frac += dx / w * vs
            df = int(self._rmb_drag_frac)
            self._rmb_drag_frac -= float(df)
            self.clip_drag_delta.emit(self._rmb_clip_idx, df)
            self.crosshair_hover.emit(self._frame_at_x(px))
            event.accept()
            return

        self.crosshair_hover.emit(self._frame_at_x(px))
        self._hover_frame = self._frame_at_x(px)
        hover_idx = self._clip_index_at_pos(px, py)
        self._set_hover_clip(hover_idx)
        if hover_idx is not None and 0 <= hover_idx < len(self._clips):
            clip = self._clips[hover_idx]
            if clip.end_frame is not None:
                tip = tr("timeline.clip_hover_tooltip", clip_no=hover_idx + 1)
                QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            f = self._frame_at_x(event.position().x())
            self.seek_frame.emit(f)
            return
        if event.button() == Qt.MouseButton.RightButton:
            idx = self._clip_index_at_pos(event.position().x(), event.position().y())
            if idx is not None:
                self.clip_select_requested.emit(idx)
                self._rmb_clip_idx = idx
                self._rmb_last_x = event.position().x()
                self._rmb_drag_frac = 0.0
                self.grabMouse()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton and self._rmb_clip_idx is not None:
            self.releaseMouse()
            self._rmb_clip_idx = None
            self.clip_drag_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_view.emit()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        ruler_rect = QRect(0, 0, w, RULER_H)
        track_rect = QRect(0, RULER_H, w, max(1, h - RULER_H))

        painter.fillRect(ruler_rect, self._wc.timeline_ruler_bg)
        painter.fillRect(track_rect, self._wc.timeline_track_bg)
        painter.setPen(QPen(self._wc.timeline_separator))
        painter.drawLine(0, RULER_H, w, RULER_H)

        ttot = max(1, self._total)
        v0 = max(0, min(self._view_start, ttot - 1))
        vs = max(1, min(self._view_span, ttot))
        if v0 + vs > ttot:
            v0 = max(0, ttot - vs)
        v_end = min(ttot - 1, v0 + vs - 1)

        sec_visible = vs / self._fps
        use_frame_labels = sec_visible < 3.0
        major = _nice_major_step(vs)
        f = (v0 // major) * major
        if f < v0:
            f += major

        small = QFont()
        small.setPixelSize(10)
        painter.setFont(small)

        while f <= v_end:
            xi = self._x_for_frame(f)
            painter.setPen(QPen(self._wc.timeline_major_tick))
            painter.drawLine(xi, RULER_H - 8, xi, RULER_H)
            if use_frame_labels:
                label = str(f)
            else:
                label = _format_time_sec(f / self._fps)
            painter.setPen(QPen(self._wc.timeline_label))
            tw = painter.fontMetrics().horizontalAdvance(label)
            tx = max(2, min(w - tw - 2, xi - tw // 2))
            painter.drawText(tx, 14, label)
            f += major

        painter.setPen(QPen(self._wc.timeline_minor_tick))
        sub = max(1, major // 5)
        if major >= 10 and sub < major:
            g = (v0 // sub) * sub
            if g < v0:
                g += sub
            while g <= v_end:
                if g % major != 0:
                    xi = self._x_for_frame(g)
                    painter.drawLine(xi, RULER_H - 4, xi, RULER_H)
                g += sub

        # ── Precise time labels at playhead and hover positions ──
        # Hover source: timeline mouse position first, then waveform crosshair
        eff_hover = self._hover_frame
        if eff_hover is None and self._sync_crosshair_frame is not None:
            eff_hover = self._sync_crosshair_frame
        # Compute crosshair top so it stops just below the time label
        label_h = painter.fontMetrics().height() + 2 * _TIME_LABEL_PAD_V
        crosshair_top = _TIME_LABEL_Y + label_h + 2
        _draw_precise_time_labels(
            painter, w, self._x_for_frame, self._current,
            eff_hover, self._fps, self._wc,
        )

        ty0 = RULER_H + TRACK_PAD
        ty1 = h - TRACK_PAD
        geoms = self._visible_clip_geometries()
        narrow_badges: list[tuple[int, int, str]] = []  # (x_center, clip_idx, label)

        label_font = QFont()
        label_font.setPixelSize(10)
        painter.setFont(label_font)
        fm = painter.fontMetrics()

        for clip_idx, x0, x1, width_px in geoms:
            is_hover = clip_idx == self._hover_clip_index
            is_selected = clip_idx == self._selected_clip_index
            fill = QColor(90, 120, 200, 180)
            if is_hover:
                fill = QColor(75, 105, 190, 210)
            elif is_selected:
                fill = QColor(100, 140, 230, 205)
            painter.fillRect(x0, ty0, width_px, max(1, ty1 - ty0), fill)

            border_col = QColor(70, 90, 150, 200)
            border_w = 1
            if is_selected:
                border_col = QColor(170, 210, 255, 240)
                border_w = 2
            elif is_hover:
                border_col = QColor(150, 190, 255, 220)
            painter.setPen(QPen(border_col, border_w))
            painter.drawRect(x0, ty0, max(1, width_px - 1), max(1, (ty1 - ty0) - 1))

            label = str(clip_idx + 1)
            if width_px >= MIN_INBOX_LABEL_W:
                tw = fm.horizontalAdvance(label)
                tx = max(x0 + 2, min(x1 - tw - 2, x0 + (width_px - tw) // 2))
                ty = ty0 + 12
                painter.setPen(QPen(QColor(20, 26, 40, 220), 2))
                painter.drawText(tx + 1, ty + 1, label)
                painter.setPen(QPen(QColor(245, 248, 255)))
                painter.drawText(tx, ty, label)
            else:
                narrow_badges.append((x0 + (width_px // 2), clip_idx, label))

        if narrow_badges:
            narrow_badges.sort(key=lambda it: it[0])
            clusters: list[list[tuple[int, int, str]]] = []
            current_cluster: list[tuple[int, int, str]] = []
            for item in narrow_badges:
                if not current_cluster:
                    current_cluster = [item]
                    continue
                if item[0] - current_cluster[-1][0] <= BADGE_CLUSTER_GAP:
                    current_cluster.append(item)
                else:
                    clusters.append(current_cluster)
                    current_cluster = [item]
            if current_cluster:
                clusters.append(current_cluster)

            badge_h = 12
            badge_y = 2
            for cluster in clusters:
                total = len(cluster)
                show_items = cluster[:MAX_BADGES_PER_CLUSTER]
                overflow = total - len(show_items)
                if overflow > 0:
                    keep = max(1, MAX_BADGES_PER_CLUSTER - 1)
                    show_items = cluster[:keep]
                    shown_count = len(show_items)
                    show_items.append((cluster[keep - 1][0], -1, f"...+{total - shown_count}"))

                # Center the row around cluster median x.
                texts = [it[2] for it in show_items]
                widths = [fm.horizontalAdvance(t) + 8 for t in texts]
                row_w = sum(widths) + max(0, len(widths) - 1) * 3
                cx = cluster[len(cluster) // 2][0]
                x = max(2, min(w - row_w - 2, cx - row_w // 2))
                for k, text in enumerate(texts):
                    bw = widths[k]
                    rect = QRect(x, badge_y, bw, badge_h)
                    is_hover_badge = False
                    if show_items[k][1] >= 0:
                        is_hover_badge = show_items[k][1] == self._hover_clip_index
                    col = QColor(85, 110, 185, 140)
                    if is_hover_badge:
                        col = QColor(70, 100, 185, 185)
                    painter.fillRect(rect, col)
                    painter.setPen(QPen(QColor(185, 205, 255, 170)))
                    painter.drawRect(rect.adjusted(0, 0, -1, -1))
                    painter.setPen(QPen(QColor(235, 242, 255, 225)))
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
                    x += bw + 3

        cx = self._x_for_frame(self._current)
        painter.setPen(QPen(self._wc.playhead_color, 2))
        painter.drawLine(cx, RULER_H, cx, h)

        if self._sync_crosshair_frame is not None:
            hx = self._x_for_frame(self._sync_crosshair_frame)
            if 0 <= hx < w:
                painter.setPen(QPen(self._wc.crosshair_color, 1))
                painter.drawLine(hx, crosshair_top, hx, h)

        painter.setPen(QPen(self._wc.timeline_border))
        painter.drawRect(0, 0, w - 1, h - 1)
        if self._view_span < self._total:
            painter.setPen(QPen(self._wc.timeline_viewport_dot))
            painter.drawText(6, h - 4, "🔍")

        # Arrow indicators when playhead is off-screen
        arrow_size = 6
        track_mid_y = (RULER_H + h) // 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._wc.playhead_color)
        if cx < 0:
            painter.drawPolygon([
                QPoint(arrow_size, track_mid_y - arrow_size),
                QPoint(0, track_mid_y),
                QPoint(arrow_size, track_mid_y + arrow_size),
            ])
        elif cx > w:
            painter.drawPolygon([
                QPoint(w - arrow_size, track_mid_y - arrow_size),
                QPoint(w, track_mid_y),
                QPoint(w - arrow_size, track_mid_y + arrow_size),
            ])
