"""Audio waveform painter with loading / empty states.

Rendering follows the dual-envelope convention used by Audacity and similar
editors: each horizontal pixel column covers a time slice and we draw two
vertical segments per column,

* outer (faint) — peak min/max envelope,
* inner (solid) — RMS envelope (perceived loudness),

aggregated across all pre-binned values that fall in the slice
(``min(mins)`` / ``max(maxs)`` / ``max(rms)``), not a single bin nearest the
column center.

The RMS layer is the key reason this widget can show useful structure even
for heavily compressed / limited audio: peak min/max collapses to ±full-scale
in every bin and would otherwise paint as a solid block, but RMS still
preserves loudness variations.

Pre-binned data comes from PCM streaming in ``waveform_gen``. When the
showwavespic fast path is used (mid/long files that are not visually
saturated), no real RMS data is available, so the widget transparently
degrades to a peak-only display for that file.
"""

from __future__ import annotations

from enum import Enum, auto

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget


class WaveformUiState(Enum):
    EMPTY = auto()
    LOADING = auto()
    NO_AUDIO = auto()
    ERROR = auto()
    READY = auto()


class WaveformWidget(QWidget):
    seek_frame = Signal(int)
    crosshair_hover = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self._sync_crosshair_frame: int | None = None
        self._mins: np.ndarray | None = None
        self._maxs: np.ndarray | None = None
        self._rms: np.ndarray | None = None
        self._view_start_f = 0
        self._view_span_f = 1
        self._total_frames = 1
        self._current_frame = 0
        self._state = WaveformUiState.EMPTY
        self._error_text: str = ""
        self._norm_ref: float = 1.0
        self._loading_text = "…"
        self._no_audio_text = "—"
        self._empty_text = "—"
        self._wave_cache: QPixmap | None = None
        self._wave_cache_dirty = True
        self.setMinimumHeight(100)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.MinimumExpanding,
        )

    def _invalidate_wave_cache(self) -> None:
        self._wave_cache = None
        self._wave_cache_dirty = True

    def set_status_texts(self, loading: str, no_audio: str, empty: str = "—") -> None:
        """Call once with tr() strings from parent."""
        self._loading_text = loading
        self._no_audio_text = no_audio
        self._empty_text = empty

    def reset_empty(self) -> None:
        self._state = WaveformUiState.EMPTY
        self._mins = None
        self._maxs = None
        self._rms = None
        self._norm_ref = 1.0
        self._error_text = ""
        self._view_start_f = 0
        self._view_span_f = 1
        self._total_frames = 1
        self._current_frame = 0
        self._sync_crosshair_frame = None
        self._invalidate_wave_cache()
        self.update()

    def set_loading(self) -> None:
        self._state = WaveformUiState.LOADING
        self._mins = None
        self._maxs = None
        self._rms = None
        self._norm_ref = 1.0
        self._error_text = ""
        self._sync_crosshair_frame = None
        self._invalidate_wave_cache()
        self.update()

    def set_no_audio(self) -> None:
        self._state = WaveformUiState.NO_AUDIO
        self._mins = None
        self._maxs = None
        self._rms = None
        self._norm_ref = 1.0
        self._error_text = ""
        self._sync_crosshair_frame = None
        self._invalidate_wave_cache()
        self.update()

    def set_error(self, message: str) -> None:
        self._state = WaveformUiState.ERROR
        self._error_text = message
        self._mins = None
        self._maxs = None
        self._rms = None
        self._norm_ref = 1.0
        self._sync_crosshair_frame = None
        self._invalidate_wave_cache()
        self.update()

    def set_peaks(
        self,
        mins: np.ndarray | None,
        maxs: np.ndarray | None,
        rms: np.ndarray | None = None,
    ) -> None:
        self._mins = mins
        self._maxs = maxs
        # Only retain RMS when there is actual energy; treat all-zero arrays
        # (showwavespic fast path or no-audio sentinel) as "no rms available"
        # so the renderer falls back to a peak-only display for that file.
        if rms is not None and rms.size > 0 and float(np.max(np.abs(rms))) > 1e-6:
            self._rms = rms
        else:
            self._rms = None
        if mins is None or maxs is None or mins.size == 0 or maxs.size == 0:
            self._norm_ref = 1.0
        else:
            abs_all = np.maximum(np.abs(mins.astype(np.float64)), np.abs(maxs.astype(np.float64)))
            pk_max = float(np.max(abs_all)) if abs_all.size else 0.0
            if abs_all.size < 8:
                self._norm_ref = max(pk_max, 1e-6)
            else:
                p94 = float(np.percentile(abs_all, 94.0))
                # Use global normalization across the whole track to avoid
                # per-zoom auto-stretching that makes local views look "always full".
                self._norm_ref = max(p94, pk_max * 0.04, 1e-6)
        self._state = WaveformUiState.READY
        self._error_text = ""
        self._invalidate_wave_cache()
        self.update()

    def set_sync_crosshair_frame(self, frame: int | None) -> None:
        if frame == self._sync_crosshair_frame:
            return
        self._sync_crosshair_frame = frame
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._invalidate_wave_cache()
        super().resizeEvent(event)

    def _view_window(self) -> tuple[int, int, int]:
        ttot = max(1, self._total_frames)
        v0 = max(0, min(self._view_start_f, ttot - 1))
        vs = max(1, min(self._view_span_f, ttot))
        if v0 + vs > ttot:
            v0 = max(0, ttot - vs)
        return ttot, v0, vs

    def _frame_at_x(self, x: float) -> int:
        w = max(1, self.width())
        xf = max(0.0, min(1.0, x / w))
        ttot, v0, vs = self._view_window()
        f = int(v0 + xf * vs)
        return max(0, min(f, ttot - 1))

    def _x_for_frame(self, frame: int) -> int:
        w = max(1, self.width())
        ttot, v0, vs = self._view_window()
        return int((frame - v0) * w / vs)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.crosshair_hover.emit(None)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.crosshair_hover.emit(self._frame_at_x(event.position().x()))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.seek_frame.emit(self._frame_at_x(event.position().x()))
        super().mousePressEvent(event)

    def set_view(
        self,
        view_start_frame: int,
        view_span_frames: int,
        total_frames: int,
        *,
        current_frame: int = 0,
    ) -> None:
        """Which part of the timeline the waveform column mapping follows (sync with timeline zoom)."""
        old_total = self._total_frames
        old_start = self._view_start_f
        old_span = self._view_span_f
        t = max(1, total_frames)
        self._total_frames = t
        self._view_start_f = max(0, min(view_start_frame, t - 1))
        span = max(1, view_span_frames)
        self._view_span_f = min(t, span)
        if self._view_start_f + self._view_span_f > t:
            self._view_start_f = max(0, t - self._view_span_f)
        self._current_frame = max(0, min(int(current_frame), t - 1))
        if (
            old_total != self._total_frames
            or old_start != self._view_start_f
            or old_span != self._view_span_f
        ):
            self._invalidate_wave_cache()
        self.update()

    def _vert_segment(
        self,
        *,
        top_norm: float,
        bot_norm: float,
        h: int,
        mid: float,
        mid_i: int,
        min_span: int,
    ) -> tuple[int, int]:
        """Map two normalized amplitudes (>=0 above, >=0 below midline) to pixel y-range."""
        top = max(-1.15, min(1.15, top_norm))
        bot = max(-1.15, min(1.15, bot_norm))
        yy0 = mid - top * mid * 0.95
        yy1 = mid + bot * mid * 0.95
        a = int(round(yy0))
        b = int(round(yy1))
        if b < a:
            a, b = b, a
        if b - a < min_span:
            a = max(0, mid_i - min_span // 2)
            b = min(h - 1, a + min_span - 1)
            if b - a < min_span - 1:
                a = max(0, mid_i - 1)
                b = min(h - 1, mid_i + 1)
        return a, b

    def _rebuild_wave_cache(self, w: int, h: int) -> None:
        if (
            self._state != WaveformUiState.READY
            or self._mins is None
            or self._maxs is None
            or len(self._mins) == 0
        ):
            self._wave_cache = None
            self._wave_cache_dirty = False
            return

        cache = QPixmap(w, h)
        cache.fill(QColor(28, 28, 32))
        p = QPainter(cache)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        try:
            n = len(self._mins)
            mid = h / 2
            ttot = max(1, self._total_frames)
            v0 = max(0, min(self._view_start_f, ttot - 1))
            vs = max(1, min(self._view_span_f, ttot))
            if v0 + vs > ttot:
                v0 = max(0, ttot - vs)
            # Frame-space edges for each pixel column -> map to bin indices [b0, b1).
            f_edges = v0 + np.arange(w + 1, dtype=np.float64) * (float(vs) / float(w))
            b_lo = np.floor(f_edges[:-1] * n / float(ttot)).astype(np.int64)
            b_hi = np.ceil(f_edges[1:] * n / float(ttot)).astype(np.int64)
            b_lo = np.clip(b_lo, 0, n - 1)
            b_hi = np.minimum(n, np.maximum(b_lo + 1, b_hi))

            los = np.empty(w, dtype=np.float64)
            his = np.empty(w, dtype=np.float64)
            has_rms = self._rms is not None and self._rms.size == n
            rms_col = np.empty(w, dtype=np.float64) if has_rms else None
            for cx in range(w):
                lo_i = int(b_lo[cx])
                hi_i = int(b_hi[cx])
                los[cx] = float(np.min(self._mins[lo_i:hi_i]))
                his[cx] = float(np.max(self._maxs[lo_i:hi_i]))
                if has_rms and rms_col is not None:
                    rms_col[cx] = float(np.max(self._rms[lo_i:hi_i]))

            ref = max(float(self._norm_ref), 1e-6)
            mid_i = int(round(mid))
            min_span = 2

            # Outer envelope: peak min/max. Faint when RMS overlay is available
            # so the inner RMS envelope reads as the primary visual.
            peak_color = QColor(80, 200, 160, 110) if has_rms else QColor(80, 200, 160)
            p.setPen(QPen(peak_color))
            for cx in range(w):
                lo = float(los[cx]) / ref
                hi = float(his[cx]) / ref
                # los[cx] is typically <= 0; flip sign so _vert_segment receives
                # symmetric "amplitude above / below midline" values.
                iy0, iy1 = self._vert_segment(
                    top_norm=hi,
                    bot_norm=-lo,
                    h=h,
                    mid=mid,
                    mid_i=mid_i,
                    min_span=min_span,
                )
                p.drawLine(cx, iy0, cx, iy1)

            # Inner envelope: RMS, drawn symmetric around the midline.
            if has_rms and rms_col is not None:
                p.setPen(QPen(QColor(120, 230, 180, 240)))
                for cx in range(w):
                    r = float(rms_col[cx]) / ref
                    if r <= 0.0:
                        continue
                    iy0, iy1 = self._vert_segment(
                        top_norm=r,
                        bot_norm=r,
                        h=h,
                        mid=mid,
                        mid_i=mid_i,
                        min_span=min_span,
                    )
                    p.drawLine(cx, iy0, cx, iy1)
        finally:
            p.end()

        self._wave_cache = cache
        self._wave_cache_dirty = False

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(28, 28, 32))
        w = max(1, self.width())
        h = self.height()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        try:
            if self._state == WaveformUiState.LOADING:
                painter.setPen(QPen(QColor(180, 160, 90)))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._loading_text)
                return
            if self._state == WaveformUiState.NO_AUDIO:
                painter.setPen(QPen(QColor(120, 120, 120)))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._no_audio_text)
                return
            if self._state == WaveformUiState.ERROR:
                painter.setPen(QPen(QColor(200, 100, 100)))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._error_text or "—")
                return
            if self._state == WaveformUiState.EMPTY or self._mins is None or self._maxs is None:
                painter.setPen(QPen(QColor(120, 120, 120)))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
                return

            if len(self._mins) == 0:
                painter.setPen(QPen(QColor(120, 120, 120)))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
                return

            if (
                self._wave_cache_dirty
                or self._wave_cache is None
                or self._wave_cache.width() != w
                or self._wave_cache.height() != h
            ):
                self._rebuild_wave_cache(w, h)
            if self._wave_cache is not None:
                painter.drawPixmap(0, 0, self._wave_cache)
        finally:
            px = self._x_for_frame(self._current_frame)
            if 0 <= px < w:
                painter.setPen(QPen(QColor(255, 220, 80), 2))
                painter.drawLine(px, 0, px, h)
            if self._sync_crosshair_frame is not None:
                hx = self._x_for_frame(self._sync_crosshair_frame)
                if 0 <= hx < w:
                    painter.setPen(QPen(QColor(255, 255, 255), 1))
                    painter.drawLine(hx, 0, hx, h)
