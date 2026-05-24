"""Frame selector widget: thumbnail strip for manual annotation frames."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from easyclip.annotation.project import FrameAnnotation
from easyclip.i18n.strings import tr

THUMB_W = 120
THUMB_H = 90


class _ThumbExtractThread(QThread):
    """Extract multiple frame thumbnails in a single background thread.
    Emits ``thumb_ready`` for each successfully extracted frame so the UI
    can update incrementally instead of waiting for all frames.
    """
    thumb_ready = Signal(int, bytes)   # frame_index, png_data

    def __init__(self, video_path: str, frames: list[tuple[int, float]]) -> None:
        """*frames*: list of (frame_index, timestamp_sec)."""
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
                pass  # skip frames that fail to decode


class _AnnotationThumbWidget(QFrame):
    """Single annotation thumbnail: pixmap + frame number label."""

    clicked = Signal(int)  # frame_index

    def __init__(self, frame_index: int, seq_num: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frame_index = frame_index
        self._seq_num = seq_num
        self._selected = False

        self.setFixedSize(THUMB_W + 12, THUMB_H + 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(THUMB_W, THUMB_H)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setStyleSheet("background: #1a1a2e; border: 1px solid #333;")
        self._thumb_label.setText(tr("annotation.annotation_num", num=seq_num))
        layout.addWidget(self._thumb_label)

        self._num_label = QLabel(tr("annotation.annotation_num", num=seq_num))
        self._num_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._num_label.setStyleSheet("color: #aaa; font-size: 10px;")
        layout.addWidget(self._num_label)

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(THUMB_W, THUMB_H, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        self._thumb_label.setPixmap(scaled)
        self._thumb_label.setText("")

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        if selected:
            self.setStyleSheet(
                "_AnnotationThumbWidget { border: 2px solid #6496f0; background: #1e2a3a; border-radius: 4px; }"
            )
        else:
            self.setStyleSheet(
                "_AnnotationThumbWidget { border: 2px solid transparent; background: transparent; border-radius: 4px; }"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit(self._frame_index)
        super().mousePressEvent(event)

    def update_seq_num(self, seq_num: int) -> None:
        self._seq_num = seq_num
        self._num_label.setText(tr("annotation.annotation_num", num=seq_num))
        if self._thumb_label.pixmap() is None:
            self._thumb_label.setText(tr("annotation.annotation_num", num=seq_num))


class FrameSelector(QWidget):
    """Horizontal thumbnail strip of manual annotation frames, sorted by frame_index."""

    frame_removed = Signal(int)          # index in sorted list
    frame_selected = Signal(int)         # frame_index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._annotations: list[FrameAnnotation] = []
        self._pixmaps: dict[int, QPixmap] = {}      # frame_index -> QPixmap
        self._video_path: str = ""
        self._selected_frame_index: int | None = None
        self._thumb_widgets: dict[int, _AnnotationThumbWidget] = {}  # frame_index -> widget
        self._extract_thread: _ThumbExtractThread | None = None
        self._thumb_pending: bool = False  # set when new annotations arrive while thread is running

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header
        header = QHBoxLayout()
        header.addWidget(QLabel(tr("annotation.frames_label")))
        self._btn_remove = QPushButton(tr("annotation.remove_frame"))
        self._btn_remove.setEnabled(False)
        self._btn_remove.clicked.connect(self._on_remove)
        header.addWidget(self._btn_remove)
        header.addStretch()
        layout.addLayout(header)

        # Scrollable thumbnail strip
        self._scroll = QScrollArea()
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFixedHeight(THUMB_H + 40)
        self._scroll.setWidgetResizable(True)

        self._strip = QWidget()
        self._strip_layout = QHBoxLayout(self._strip)
        self._strip_layout.setContentsMargins(4, 2, 4, 2)
        self._strip_layout.setSpacing(4)
        self._strip_layout.addStretch()
        self._scroll.setWidget(self._strip)

        layout.addWidget(self._scroll)

    def set_video_source(self, video_path: str) -> None:
        """Set the video file path for thumbnail extraction."""
        self._video_path = video_path

    def set_annotations(self, annotations: list[FrameAnnotation]) -> None:
        """Rebuild the thumbnail strip from *annotations*, sorted by frame_index."""
        self._annotations = sorted(annotations, key=lambda a: a.frame_index)
        self._rebuild_strip()
        self._extract_missing_thumbnails()

    def set_pixmap(self, frame_index: int, pixmap: QPixmap) -> None:
        """Set a pre-extracted pixmap for a given frame."""
        self._pixmaps[frame_index] = pixmap
        w = self._thumb_widgets.get(frame_index)
        if w is not None:
            w.set_thumbnail(pixmap)

    def select_annotation_by_frame(self, frame_index: int | None) -> None:
        """Select the annotation with the given frame_index, or clear selection."""
        if self._selected_frame_index is not None:
            old_w = self._thumb_widgets.get(self._selected_frame_index)
            if old_w is not None:
                old_w.set_selected(False)
        self._selected_frame_index = frame_index
        self._btn_remove.setEnabled(frame_index is not None)
        if frame_index is not None:
            w = self._thumb_widgets.get(frame_index)
            if w is not None:
                w.set_selected(True)

    def clear(self) -> None:
        self._annotations.clear()
        self._pixmaps.clear()
        self._selected_frame_index = None
        self._btn_remove.setEnabled(False)
        self._stop_extract()
        self._rebuild_strip()

    def cleanup(self) -> None:
        """Stop extraction thread and wait for it to finish (for app shutdown)."""
        self._stop_extract(wait=True)

    def refresh_language(self) -> None:
        self._btn_remove.setText(tr("annotation.remove_frame"))
        for w in self._thumb_widgets.values():
            w.update_seq_num(w._seq_num)

    # ── internal ───────────────────────────────────────────────────

    def _rebuild_strip(self) -> None:
        """Clear and rebuild the thumbnail strip from self._annotations."""
        # Remove old widgets — hide first so they disappear immediately
        while self._strip_layout.count() > 1:  # keep the trailing stretch
            item = self._strip_layout.takeAt(0)
            if item and item.widget():
                w = item.widget()
                w.hide()
                w.deleteLater()
        self._thumb_widgets.clear()

        for seq_num, ann in enumerate(self._annotations, 1):
            w = _AnnotationThumbWidget(ann.frame_index, seq_num)
            w.clicked.connect(self._on_thumb_clicked)
            # Show cached pixmap if available
            if ann.frame_index in self._pixmaps:
                w.set_thumbnail(self._pixmaps[ann.frame_index])
            self._thumb_widgets[ann.frame_index] = w
            self._strip_layout.insertWidget(self._strip_layout.count() - 1, w)

        # Restore selection
        if self._selected_frame_index is not None:
            self.select_annotation_by_frame(self._selected_frame_index)

    def _extract_missing_thumbnails(self) -> None:
        """Extract thumbnails for annotations that don't have cached pixmaps.

        Follows the slicing page's single-thread + pending pattern:
        at most one extraction thread runs at a time.  If a thread is already
        running, just mark pending — the finished handler will drain it.
        """
        if not self._video_path:
            return
        # If a thread is already running, mark pending and interrupt it so
        # it can drain the updated annotation set when it finishes.
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
        """If new annotations arrived while the thread was running, start a fresh one."""
        if self._extract_thread is not None and self._extract_thread.isRunning():
            return  # still running — will drain when it finishes
        if self._thumb_pending:
            self._thumb_pending = False
            self._start_extract_thread()

    def _on_extract_finished(self) -> None:
        """Called when the extraction thread finishes (via QThread.finished)."""
        sender = self.sender()
        if sender is not self._extract_thread:
            return  # stale signal from a superseded thread
        self._extract_thread = None
        self._drain_pending_extract()

    def _stop_extract(self, *, wait: bool = False) -> None:
        """Interrupt the running extraction thread.

        Args:
            wait: If True, block until the thread finishes (for shutdown).
        """
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
        # When wait=False, keep the reference — _on_extract_finished
        # will clear it when the thread actually finishes.  Clearing
        # it here would orphan a still-running thread and trigger
        # "QThread: Destroyed while thread is still running".

    def _on_thumb_ready(self, frame_index: int, png: bytes) -> None:
        """Handle a thumbnail extracted by the batch thread."""
        im = QImage.fromData(png, "PNG")
        if im.isNull():
            return
        pix = QPixmap.fromImage(im)
        self._pixmaps[frame_index] = pix
        w = self._thumb_widgets.get(frame_index)
        if w is not None:
            w.set_thumbnail(pix)

    def _on_thumb_clicked(self, frame_index: int) -> None:
        self.select_annotation_by_frame(frame_index)
        self.frame_selected.emit(frame_index)

    def _on_remove(self) -> None:
        if self._selected_frame_index is None:
            return
        # Find the index in the sorted annotations list
        for i, ann in enumerate(self._annotations):
            if ann.frame_index == self._selected_frame_index:
                self.frame_removed.emit(i)
                return
