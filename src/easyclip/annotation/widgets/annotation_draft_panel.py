"""Annotation draft panel: merged thumbnail strip + per-frame draft + transition editors.

Replaces the separate FrameSelector and DraftEditor widgets with a single
integrated panel where each annotation frame is shown as:

    [thumbnail]  Frame N (Ts)
                 [frame description text edit]

    → Transition N→M (Ts→Ts)
      [transition description text edit]

When no annotation frames exist, a single QPlainTextEdit is shown for
clip-level description (simple mode).
"""

from __future__ import annotations

from PySide6.QtCore import QSize, QTimer, Qt, Signal, QThread
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from easyclip.annotation.project import AnnotatedClip, FrameAnnotation
from easyclip.i18n.strings import tr

DEFAULT_THUMB_W = 120
INFO_WIDTH = 50  # fixed width for left info column (# + 🗑)


# ── Thumbnail extraction thread (reused from FrameSelector) ────────


class _ThumbExtractThread(QThread):
    """Extract multiple frame thumbnails in a single background thread."""

    thumb_ready = Signal(int, bytes)  # frame_index, png_data

    def __init__(self, video_path: str, frames: list[tuple[int, float]]) -> None:
        super().__init__()
        self._video_path = video_path
        self._frames = frames

    def run(self) -> None:
        from easyclip.core.ffmpeg_util import extract_frame_png
        for frame_index, time_sec in self._frames:
            if self.isInterruptionRequested():
                return
            try:
                png = extract_frame_png(self._video_path, time_sec, max_side=160)
                if not self.isInterruptionRequested():
                    self.thumb_ready.emit(frame_index, png)
            except Exception:
                pass


# ── Thumbnail widget ────────────────────────────────────────────────


class _ThumbWidget(QFrame):
    """Single annotation thumbnail with selection border (image only, no label)."""

    clicked = Signal(int)  # frame_index

    def __init__(self, frame_index: int, thumb_w: int, thumb_h: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frame_index = frame_index
        self._selected = False
        self._thumb_w = thumb_w
        self._thumb_h = thumb_h
        self._pixmap: QPixmap | None = None

        # Only fix HEIGHT; let QSplitter control the WIDTH.
        # setFixedSize() locks both min & max width, making the handle immovable.
        self.setFixedHeight(thumb_h + 4)
        self.setMinimumWidth(64)     # min image 60 + 4 border
        self.setMaximumWidth(304)    # max image 300 + 4 border
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        self._thumb_label = QLabel()
        self._thumb_label.setFixedHeight(thumb_h)
        # Width follows layout (widget_width - 4px margins = label_width)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setStyleSheet("background: #1a1a2e; border: 1px solid #333;")
        layout.addWidget(self._thumb_label)

    def sizeHint(self):
        """Provide initial size hint for QSplitter to use."""
        return QSize(self._thumb_w + 4, self._thumb_h + 4)

    def resize_thumb(self, thumb_w: int, thumb_h: int, *,
                     smooth: bool = True) -> None:
        """Resize the widget and re-scale the cached pixmap if present.

        Args:
            smooth: Use SmoothTransformation (True) or FastTransformation
                    (False).  Pass False during interactive drag for speed;
                    a final smooth pass should follow on drag-end.
        """
        self._thumb_w = thumb_w
        self._thumb_h = thumb_h
        self.setFixedHeight(thumb_h + 4)
        self._thumb_label.setFixedHeight(thumb_h)
        if self._pixmap is not None:
            mode = (Qt.TransformationMode.SmoothTransformation if smooth
                    else Qt.TransformationMode.FastTransformation)
            self._thumb_label.setPixmap(
                self._pixmap.scaled(thumb_w, thumb_h,
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    mode)
            )

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._thumb_label.setPixmap(
            pixmap.scaled(self._thumb_w, self._thumb_h,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        )

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        if selected:
            self.setStyleSheet(
                "_ThumbWidget { border: 2px solid #6496f0; background: #1e2a3a; border-radius: 4px; }"
            )
        else:
            self.setStyleSheet(
                "_ThumbWidget { border: 2px solid transparent; background: transparent; border-radius: 4px; }"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit(self._frame_index)
        super().mousePressEvent(event)


# ── Main panel ──────────────────────────────────────────────────────


class AnnotationDraftPanel(QWidget):
    """Merged frame thumbnail strip + per-frame draft editor + transition editor.

    Signals (same as FrameSelector for drop-in replacement):
        frame_removed(int)   - index in sorted annotations list
        frame_selected(int)  - frame_index
    """

    frame_removed = Signal(int)
    frame_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._annotations: list[FrameAnnotation] = []
        self._pixmaps: dict[int, QPixmap] = {}       # frame_index → QPixmap
        self._video_path: str = ""
        self._selected_frame_index: int | None = None
        self._global_expanded: bool = False
        self._extract_thread: _ThumbExtractThread | None = None
        self._thumb_pending: bool = False

        # Thumbnail aspect ratio — locked from the first received pixmap.
        # Until then, use a reasonable default (16:9).
        self._aspect_ratio: float = 16.0 / 9.0

        # Per-frame widgets, keyed by frame_index
        self._thumb_widgets: dict[int, _ThumbWidget] = {}
        self._frame_edits: dict[int, QPlainTextEdit] = {}
        self._thumb_splitters: list[QSplitter] = []   # sync all rows' thumb widths
        self._trans_widgets: list[dict] = []  # [{label, edit, fa, info_wrap}, ...]
        self._dynamic_rows: list[QWidget] = []  # frame rows + transition widgets, cleaned on rebuild
        self._syncing: bool = False  # gate for _on_thumb_width_changed

        # Debounce timer: after drag settles, re-render thumbnails with
        # SmoothTransformation for final quality (drag uses Fast).
        self._smooth_timer = QTimer(self)
        self._smooth_timer.setSingleShot(True)
        self._smooth_timer.setInterval(150)
        self._smooth_timer.timeout.connect(self._apply_smooth_thumbs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget(self)
        layout.addWidget(self._stack)

        # ── Page 0: Simple mode (no annotation frames) ──────────────
        self._simple_edit = QPlainTextEdit()
        self._simple_edit.setPlaceholderText(tr("annotation.draft_placeholder"))
        self._stack.addWidget(self._simple_edit)  # index 0

        # ── Page 1: Integrated per-frame mode (QScrollArea) ─────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._per_frame_container = QWidget()
        self._per_frame_layout = QVBoxLayout(self._per_frame_container)
        self._per_frame_layout.setContentsMargins(0, 0, 0, 0)
        self._per_frame_layout.setSpacing(4)

        # Global context collapsible section
        self._global_toggle = QPushButton()
        self._global_toggle.setFlat(True)
        self._global_toggle.setStyleSheet("text-align: left; font-weight: bold; padding: 2px 0;")
        self._global_toggle.clicked.connect(self._toggle_global_context)
        self._per_frame_layout.addWidget(self._global_toggle)
        self._update_global_toggle_text()

        self._global_edit = QPlainTextEdit()
        self._global_edit.setMaximumHeight(50)
        self._global_edit.setPlaceholderText(tr("annotation.draft.global_context_placeholder"))
        self._global_edit.hide()
        self._per_frame_layout.addWidget(self._global_edit)

        # Separator
        self._global_sep = QWidget()
        self._global_sep.setFixedHeight(4)
        self._global_sep.hide()
        self._per_frame_layout.addWidget(self._global_sep)

        # Dynamic thumbnail dimensions (user adjusts width by dragging the
        # splitter handle; height is computed from _aspect_ratio).
        # _thumb_w / _thumb_h = the thumbnail IMAGE size (label interior).
        # The _ThumbWidget outer size is +4px (2px selection border each side).
        self._thumb_w: int = DEFAULT_THUMB_W
        self._thumb_h: int = int(DEFAULT_THUMB_W / self._aspect_ratio)

        # Dynamic frame/transition rows inserted here
        self._per_frame_layout.addStretch()

        self._scroll.setWidget(self._per_frame_container)
        self._stack.addWidget(self._scroll)  # index 1

        self._stack.setCurrentIndex(0)

    # ── Public API ──────────────────────────────────────────────────

    def set_video_source(self, video_path: str) -> None:
        self._video_path = video_path

    def set_annotations(self, annotations: list[FrameAnnotation]) -> None:
        """Switch between simple mode and per-frame mode."""
        self._annotations = sorted(annotations, key=lambda a: a.frame_index)

        if self._annotations:
            self._rebuild_per_frame_ui()
            self._extract_missing_thumbnails()
            self._stack.setCurrentIndex(1)
        else:
            self._clear_per_frame_ui()
            self._stack.setCurrentIndex(0)

    def select_annotation_by_frame(self, frame_index: int | None) -> None:
        """Select the annotation with the given frame_index, or clear selection."""
        if self._selected_frame_index is not None:
            old_w = self._thumb_widgets.get(self._selected_frame_index)
            if old_w is not None:
                old_w.set_selected(False)
        self._selected_frame_index = frame_index
        if frame_index is not None:
            w = self._thumb_widgets.get(frame_index)
            if w is not None:
                w.set_selected(True)

    def set_pixmap(self, frame_index: int, pixmap: QPixmap) -> None:
        """Set a pre-extracted pixmap for a given frame.

        On the first pixmap, locks the thumbnail aspect ratio to the
        video's actual ratio and resizes all existing widgets in-place.
        """
        self._pixmaps[frame_index] = pixmap
        w = self._thumb_widgets.get(frame_index)
        if w is not None:
            w.set_thumbnail(pixmap)
        self._lock_aspect_ratio_from_pixmap(pixmap)

    def _lock_aspect_ratio_from_pixmap(self, pixmap: QPixmap) -> None:
        """If the pixmap's aspect ratio differs from the current setting,
        lock to the new ratio and resize all existing widgets."""
        if pixmap.isNull():
            return
        ar = pixmap.width() / max(pixmap.height(), 1)
        if abs(ar - self._aspect_ratio) <= 0.01:
            return
        self._aspect_ratio = ar
        self._thumb_h = int(self._thumb_w / ar)
        self._syncing = True
        try:
            for tw in self._thumb_widgets.values():
                tw.resize_thumb(self._thumb_w, self._thumb_h)
            for edit in self._frame_edits.values():
                edit.setMinimumHeight(self._thumb_h)
                edit.setMaximumHeight(self._thumb_h)
            self._sync_splitter_positions()
        finally:
            self._syncing = False

    def global_draft_text(self) -> str:
        """Get the clip-level draft text."""
        if self._stack.currentIndex() == 1:
            return self._global_edit.toPlainText()
        return self._simple_edit.toPlainText()

    def set_global_draft_text(self, text: str) -> None:
        self._simple_edit.setPlainText(text)
        self._global_edit.setPlainText(text)

    def sync_from_clip(self, clip: AnnotatedClip) -> None:
        """Load all data from an AnnotatedClip."""
        self._simple_edit.setPlainText(clip.draft_prompt)
        self._global_edit.setPlainText(clip.draft_prompt)
        self.set_annotations(list(clip.annotations))

    def sync_to_clip(self, clip: AnnotatedClip) -> None:
        """Write all editor content back to an AnnotatedClip."""
        clip.draft_prompt = self.global_draft_text()

        # Sync per-frame edits back to annotations
        for fa in self._annotations:
            edit = self._frame_edits.get(fa.frame_index)
            if edit is not None:
                fa.draft_text = edit.toPlainText()

        for tw in self._trans_widgets:
            tw["fa"].transition_text = tw["edit"].toPlainText()

        clip.annotations = list(self._annotations)

    def build_text_prompt(self) -> str:
        """Build a formatted text-only prompt string (no images)."""
        if not self._annotations:
            text = self._simple_edit.toPlainText().strip()
            return text if text else ""

        # Sync edits first
        for fa in self._annotations:
            edit = self._frame_edits.get(fa.frame_index)
            if edit is not None:
                fa.draft_text = edit.toPlainText()
        for tw in self._trans_widgets:
            tw["fa"].transition_text = tw["edit"].toPlainText()

        lines = []
        for i, fa in enumerate(self._annotations):
            if fa.draft_text.strip():
                lines.append(
                    f"Frame {fa.frame_index} ({fa.timestamp_sec:.1f}s): {fa.draft_text}"
                )
            if i < len(self._annotations) - 1 and fa.transition_text.strip():
                next_fa = self._annotations[i + 1]
                lines.append(
                    f"  → Transition {fa.frame_index}→{next_fa.frame_index}"
                    f" ({fa.timestamp_sec:.1f}s→{next_fa.timestamp_sec:.1f}s):"
                    f" {fa.transition_text}"
                )

        global_draft = self._global_edit.toPlainText().strip()
        if global_draft:
            lines.append("")
            lines.append(f"Clip context: {global_draft}")

        return "\n".join(lines)

    def clear(self) -> None:
        self._simple_edit.clear()
        self._global_edit.clear()
        self._clear_per_frame_ui()
        self._annotations = []
        self._pixmaps.clear()
        self._selected_frame_index = None
        self._stop_extract()
        self._stack.setCurrentIndex(0)

    def cleanup(self) -> None:
        """Stop extraction thread (for app shutdown)."""
        self._stop_extract(wait=True)

    def refresh_language(self) -> None:
        self._simple_edit.setPlaceholderText(tr("annotation.draft_placeholder"))
        self._global_edit.setPlaceholderText(tr("annotation.draft.global_context_placeholder"))
        self._update_global_toggle_text()
        # Rebuild per-frame UI to refresh labels (if in per-frame mode)
        if self._annotations:
            self._rebuild_per_frame_ui()

    # ── Per-frame UI rebuild ────────────────────────────────────────

    def _clear_per_frame_ui(self) -> None:
        """Remove all dynamic frame/transition widgets and their containers."""
        self._thumb_widgets.clear()
        self._frame_edits.clear()
        self._thumb_splitters.clear()
        self._trans_widgets.clear()

        # Remove from layout first (deleteLater is deferred; without removeWidget
        # the old items stay in the layout and accumulate on every rebuild).
        for row in self._dynamic_rows:
            self._per_frame_layout.removeWidget(row)
            row.deleteLater()
        self._dynamic_rows.clear()

    def _rebuild_per_frame_ui(self) -> None:
        """Rebuild all frame rows and transition sections."""
        self._clear_per_frame_ui()

        # Insert before the stretch at the end
        insert_pos = self._per_frame_layout.count() - 1

        for i, fa in enumerate(self._annotations):
            # ── Frame row: [info] | thumbnail | text edit ─────────
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(6)

            # Left info column: sequence number + trash (fixed width for alignment)
            info_wrap = QWidget()
            info_wrap.setFixedWidth(INFO_WIDTH)
            info_col = QVBoxLayout(info_wrap)
            info_col.setContentsMargins(0, 0, 0, 0)
            info_col.setSpacing(2)
            info_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)

            seq_label = QLabel(f"#{i + 1}")
            seq_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            seq_label.setStyleSheet("color: #888; font-size: 11px; font-weight: bold;")
            info_col.addWidget(seq_label)

            btn_del = QPushButton("🗑")
            btn_del.setFixedSize(24, 24)
            btn_del.setToolTip(tr("annotation.remove_frame"))
            btn_del.setStyleSheet("QPushButton { border: none; font-size: 13px; } QPushButton:hover { color: #e05555; }")
            btn_del.clicked.connect(lambda checked, idx=i: self._on_delete_frame(idx))
            info_col.addWidget(btn_del, 0, Qt.AlignmentFlag.AlignCenter)

            row_layout.addWidget(info_wrap, 0)

            # Thumbnail ↔ text edit splitter (draggable, all rows synced)
            thumb_splitter = QSplitter(Qt.Orientation.Horizontal)
            thumb_splitter.setHandleWidth(4)

            thumb = _ThumbWidget(fa.frame_index, self._thumb_w, self._thumb_h)
            thumb.clicked.connect(self._on_thumb_clicked)
            if fa.frame_index in self._pixmaps:
                thumb.set_thumbnail(self._pixmaps[fa.frame_index])
            self._thumb_widgets[fa.frame_index] = thumb
            thumb_splitter.addWidget(thumb)

            edit = QPlainTextEdit()
            edit.setMinimumHeight(self._thumb_h)
            edit.setMaximumHeight(self._thumb_h)
            edit.setPlaceholderText(tr("annotation.draft.frame_placeholder"))
            edit.setPlainText(fa.draft_text)
            edit.textChanged.connect(
                lambda fa_ref=fa, ed=edit: setattr(fa_ref, 'draft_text', ed.toPlainText())
            )
            self._frame_edits[fa.frame_index] = edit
            thumb_splitter.addWidget(edit)

            thumb_splitter.setStretchFactor(0, 0)   # thumb: keep size on window resize
            thumb_splitter.setStretchFactor(1, 1)   # edit: absorb extra space
            thumb_splitter.setCollapsible(0, False)
            thumb_splitter.setCollapsible(1, False)

            thumb_splitter.splitterMoved.connect(self._on_thumb_width_changed)
            self._thumb_splitters.append(thumb_splitter)

            row_layout.addWidget(thumb_splitter, 1)

            self._per_frame_layout.insertWidget(insert_pos, row)
            self._dynamic_rows.append(row)
            insert_pos += 1

            # ── Transition row: info block | text edit ────────
            if i < len(self._annotations) - 1:
                next_fa = self._annotations[i + 1]

                trans_row = QWidget()
                trans_layout = QHBoxLayout(trans_row)
                trans_layout.setContentsMargins(0, 4, 0, 4)
                trans_layout.setSpacing(6)

                # Transition info block — spans info+thumbnail columns (merged cell style)
                trans_info_w = INFO_WIDTH + self._thumb_w + 4 + 6  # info + spacing + thumbnail
                trans_info_wrap = QWidget()
                trans_info_wrap.setFixedWidth(trans_info_w)
                trans_info = QVBoxLayout(trans_info_wrap)
                trans_info.setContentsMargins(0, 0, 0, 0)
                trans_info.setSpacing(2)
                trans_info.setAlignment(Qt.AlignmentFlag.AlignCenter)

                t1 = QLabel("Transition")
                t1.setAlignment(Qt.AlignmentFlag.AlignCenter)
                t1.setStyleSheet("color: #888; font-size: 12px; font-weight: bold;")
                trans_info.addWidget(t1)

                t2 = QLabel(f"#{fa.frame_index} → #{next_fa.frame_index}")
                t2.setAlignment(Qt.AlignmentFlag.AlignCenter)
                t2.setStyleSheet("color: #aaa; font-size: 11px;")
                trans_info.addWidget(t2)

                t3 = QLabel(f"{fa.timestamp_sec:.1f}s → {next_fa.timestamp_sec:.1f}s")
                t3.setAlignment(Qt.AlignmentFlag.AlignCenter)
                t3.setStyleSheet("color: #888; font-size: 11px;")
                trans_info.addWidget(t3)

                trans_layout.addWidget(trans_info_wrap, 0)

                trans_edit = QPlainTextEdit()
                trans_edit.setMaximumHeight(50)
                trans_edit.setPlaceholderText(tr("annotation.draft.transition_placeholder"))
                trans_edit.setPlainText(fa.transition_text)
                trans_edit.textChanged.connect(
                    lambda fa_ref=fa, ed=trans_edit: setattr(fa_ref, 'transition_text', ed.toPlainText())
                )
                trans_layout.addWidget(trans_edit, 1)

                self._per_frame_layout.insertWidget(insert_pos, trans_row)
                self._dynamic_rows.append(trans_row)
                insert_pos += 1

                self._trans_widgets.append({
                    "label": trans_row, "edit": trans_edit, "fa": fa,
                    "info_wrap": trans_info_wrap,
                })

        # Restore selection state
        if self._selected_frame_index is not None:
            self.select_annotation_by_frame(self._selected_frame_index)

    # ── Thumbnail width sync ───────────────────────────────────────

    def _on_thumb_width_changed(self, pos: int, index: int) -> None:
        """One frame row's splitter was dragged — sync all rows.

        Uses a boolean gate (_syncing) to ignore splitterMoved signals
        that fire while we programmatically resize widgets and sync
        splitter positions via setSizes.
        """
        if self._syncing:
            return
        # pos = splitter pane width = thumb_w + 4 (border).  → image width.
        new_w = max(60, min(300, pos - 4))
        if new_w == self._thumb_w:
            return
        self._thumb_w = new_w
        self._thumb_h = int(new_w / self._aspect_ratio)

        self._syncing = True
        try:
            for tw in self._thumb_widgets.values():
                tw.resize_thumb(self._thumb_w, self._thumb_h, smooth=False)
            for edit in self._frame_edits.values():
                edit.setMinimumHeight(self._thumb_h)
                edit.setMaximumHeight(self._thumb_h)
            # Sync other splitters; skip the one being dragged
            self._sync_splitter_positions(skip=self.sender())
            for tw in self._trans_widgets:
                iw = tw.get("info_wrap")
                if iw is not None:
                    iw.setFixedWidth(INFO_WIDTH + self._thumb_w + 4 + 6)
            # Schedule a smooth-quality re-render after drag settles
            self._smooth_timer.start()
        finally:
            self._syncing = False

    def _sync_splitter_positions(self, skip: object = None) -> None:
        """Set all thumb splitter handle positions to match _thumb_w.

        Uses setSizes with exact pixel values (summing to available space)
        instead of moveSplitter, which can be unreliable during
        programmatic sync because Qt's layout may not have settled.
        """
        thumb_pane = self._thumb_w + 4
        for ts in self._thumb_splitters:
            if ts is skip:
                continue
            avail = ts.width() - ts.handleWidth()
            ts.setSizes([thumb_pane, max(1, avail - thumb_pane)])

    def _apply_smooth_thumbs(self) -> None:
        """Re-render all thumbnails with SmoothTransformation.

        Called by _smooth_timer after the drag settles.  During drag,
        FastTransformation is used for responsiveness; this final pass
        produces high-quality bilinear-interpolated thumbnails.
        """
        for tw in self._thumb_widgets.values():
            tw.resize_thumb(self._thumb_w, self._thumb_h, smooth=True)

    # ── Global context toggle ───────────────────────────────────────

    def _toggle_global_context(self) -> None:
        self._global_expanded = not self._global_expanded
        self._global_edit.setVisible(self._global_expanded)
        self._global_sep.setVisible(self._global_expanded)
        self._update_global_toggle_text()

    def _update_global_toggle_text(self) -> None:
        arrow = "▼" if self._global_expanded else "▶"
        label = tr("annotation.draft.global_context_optional")
        self._global_toggle.setText(f"{arrow} {label}")

    # ── Thumbnail extraction ────────────────────────────────────────

    def _extract_missing_thumbnails(self) -> None:
        if not self._video_path:
            return
        if self._extract_thread is not None and self._extract_thread.isRunning():
            self._thumb_pending = True
            self._extract_thread.requestInterruption()
            return
        self._start_extract_thread()

    def _start_extract_thread(self) -> None:
        missing: list[tuple[int, float]] = []
        for ann in self._annotations:
            if ann.frame_index not in self._pixmaps:
                missing.append((ann.frame_index, ann.timestamp_sec))
        if not missing:
            return
        t = _ThumbExtractThread(self._video_path, missing)
        t.thumb_ready.connect(self._on_thumb_ready)
        t.finished.connect(self._on_extract_finished)
        t.finished.connect(t.deleteLater)
        self._extract_thread = t
        t.start()

    def _drain_pending_extract(self) -> None:
        if self._extract_thread is not None and self._extract_thread.isRunning():
            return
        if self._thumb_pending:
            self._thumb_pending = False
            self._start_extract_thread()

    def _on_extract_finished(self) -> None:
        sender = self.sender()
        if sender is not self._extract_thread:
            return
        self._extract_thread = None
        self._drain_pending_extract()

    def _stop_extract(self, *, wait: bool = False) -> None:
        self._thumb_pending = False
        t = self._extract_thread
        if t is None:
            return
        if t.isRunning():
            t.requestInterruption()
            try:
                t.thumb_ready.disconnect(self._on_thumb_ready)
            except (TypeError, RuntimeError):
                pass
            if wait:
                t.wait(5000)
                self._extract_thread = None

    def _on_thumb_ready(self, frame_index: int, png: bytes) -> None:
        im = QImage.fromData(png, "PNG")
        if im.isNull():
            return
        pix = QPixmap.fromImage(im)
        self._pixmaps[frame_index] = pix
        w = self._thumb_widgets.get(frame_index)
        if w is not None:
            w.set_thumbnail(pix)
        self._lock_aspect_ratio_from_pixmap(pix)

    # ── Event handlers ──────────────────────────────────────────────

    def _on_thumb_clicked(self, frame_index: int) -> None:
        self.select_annotation_by_frame(frame_index)
        self.frame_selected.emit(frame_index)

    def _on_delete_frame(self, index: int) -> None:
        """Delete the annotation frame at the given sorted index."""
        self.frame_removed.emit(index)
