"""Annotation editor widget: draft text area with integrated frame thumbnails."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QWidget,
)

from easyclip.annotation.project import AnnotatedClip, FrameAnnotation
from easyclip.annotation.widgets.annotation_draft_panel import AnnotationDraftPanel
from easyclip.i18n.strings import tr


class AnnotationEditor(QWidget):
    """Draft editor with integrated frame thumbnail strip + per-frame text.

    The mode toggle and prompt edit have been moved to the annotation page
    (placed alongside the video preview for better space usage).

    Signals forwarded from the internal AnnotationDraftPanel:
        frame_removed(int)   - index in sorted annotations list
        frame_selected(int)  - frame_index
    """

    frame_removed = Signal(int)
    frame_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Integrated draft panel (replaces old DraftEditor + FrameSelector)
        layout.addWidget(QLabel(tr("annotation.draft_label")))
        self._draft_panel = AnnotationDraftPanel()
        self._draft_panel.frame_removed.connect(self.frame_removed.emit)
        self._draft_panel.frame_selected.connect(self.frame_selected.emit)
        layout.addWidget(self._draft_panel, 1)

    # ── Draft panel (delegated) ────────────────────────────────────────

    def set_video_source(self, video_path: str) -> None:
        self._draft_panel.set_video_source(video_path)

    def draft_text(self) -> str:
        return self._draft_panel.global_draft_text()

    def set_draft_text(self, text: str) -> None:
        self._draft_panel.set_global_draft_text(text)

    def set_annotations(self, annotations: list[FrameAnnotation]) -> None:
        self._draft_panel.set_annotations(annotations)

    def select_annotation_by_frame(self, frame_index: int | None) -> None:
        self._draft_panel.select_annotation_by_frame(frame_index)

    def set_pixmap(self, frame_index: int, pixmap) -> None:
        """Set a pre-extracted pixmap for a given frame."""
        from PySide6.QtGui import QPixmap
        self._draft_panel.set_pixmap(frame_index, pixmap)

    def sync_from_clip(self, clip: AnnotatedClip) -> None:
        self._draft_panel.sync_from_clip(clip)

    def sync_to_clip(self, clip: AnnotatedClip) -> None:
        self._draft_panel.sync_to_clip(clip)

    def build_text_prompt(self) -> str:
        return self._draft_panel.build_text_prompt()

    def clear(self) -> None:
        self._draft_panel.clear()

    def cleanup(self) -> None:
        self._draft_panel.cleanup()

    def refresh_language(self) -> None:
        self._draft_panel.refresh_language()
