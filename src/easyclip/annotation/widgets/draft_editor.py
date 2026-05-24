"""Draft editor: dynamic UI that switches between single-text and per-frame+transition modes."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from easyclip.annotation.project import AnnotatedClip, FrameAnnotation
from easyclip.i18n.strings import tr


class DraftEditor(QWidget):
    """Draft editing widget that adapts to the presence of manual annotation frames.

    Two modes, managed via QStackedWidget:
    - Page 0 (no frames): single QPlainTextEdit for clip-level description.
    - Page 1 (with frames): collapsible global context + per-frame descriptions
      + transition descriptions, inside a QScrollArea.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._annotations: list[FrameAnnotation] = []
        self._global_expanded: bool = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget(self)
        layout.addWidget(self._stack)

        # ── Page 0: Simple mode (no annotation frames) ──────────────
        self._simple_edit = QPlainTextEdit()
        self._simple_edit.setPlaceholderText(tr("annotation.draft_placeholder"))
        self._stack.addWidget(self._simple_edit)  # index 0

        # ── Page 1: Per-frame mode (inside a QScrollArea) ───────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._per_frame_container = QWidget()
        self._per_frame_layout = QVBoxLayout(self._per_frame_container)
        self._per_frame_layout.setContentsMargins(0, 0, 0, 0)
        self._per_frame_layout.setSpacing(4)

        # Global context section (collapsible)
        self._global_toggle = QPushButton()
        self._global_toggle.setFlat(True)
        self._global_toggle.setStyleSheet("text-align: left; font-weight: bold; padding: 2px 0;")
        self._global_toggle.clicked.connect(self._toggle_global_context)
        self._per_frame_layout.addWidget(self._global_toggle)

        self._global_edit = QPlainTextEdit()
        self._global_edit.setMaximumHeight(50)
        self._global_edit.setPlaceholderText(tr("annotation.draft.global_context_placeholder"))
        self._global_edit.hide()  # default collapsed
        self._per_frame_layout.addWidget(self._global_edit)

        # Separator after global context
        self._global_sep = QWidget()
        self._global_sep.setFixedHeight(4)
        self._global_sep.hide()
        self._per_frame_layout.addWidget(self._global_sep)

        # Dynamic frame/transition editors will be added here
        self._frame_widgets: list[dict] = []

        self._per_frame_layout.addStretch()

        self._scroll.setWidget(self._per_frame_container)
        self._stack.addWidget(self._scroll)  # index 1

        self._update_global_toggle_text()
        self._stack.setCurrentIndex(0)

    # ── Public API ──────────────────────────────────────────────────

    def set_annotations(self, annotations: list[FrameAnnotation]) -> None:
        """Switch between simple mode (empty list) and per-frame mode."""
        # Sync current text back before switching
        if self._annotations:
            self._sync_editors_to_annotations()

        self._annotations = list(annotations)

        # Rebuild
        self._clear_frame_widgets()

        if self._annotations:
            self._build_per_frame_editors()
            self._load_annotations_to_editors()
            self._stack.setCurrentIndex(1)
        else:
            self._stack.setCurrentIndex(0)

    def global_draft_text(self) -> str:
        """Get the global/clip-level draft text."""
        if self._stack.currentIndex() == 1:
            return self._global_edit.toPlainText()
        return self._simple_edit.toPlainText()

    def set_global_draft_text(self, text: str) -> None:
        """Set the global/clip-level draft text."""
        self._simple_edit.setPlainText(text)
        self._global_edit.setPlainText(text)

    def sync_from_clip(self, clip: AnnotatedClip) -> None:
        """Load all draft data from an AnnotatedClip into the editor."""
        # Sync annotations first (rebuilds UI if needed)
        self.set_annotations(list(clip.annotations))

        # Sync global draft
        self._simple_edit.setPlainText(clip.draft_prompt)
        self._global_edit.setPlainText(clip.draft_prompt)

        # Sync per-frame draft_text and transition_text
        if self._annotations:
            self._load_annotations_to_editors()

    def sync_to_clip(self, clip: AnnotatedClip) -> None:
        """Write all editor content back to an AnnotatedClip."""
        clip.draft_prompt = self.global_draft_text()

        if self._annotations:
            self._sync_editors_to_annotations()
            clip.annotations = list(self._annotations)

    def build_text_prompt(self) -> str:
        """Build the formatted text-only prompt string (no images)."""
        if not self._annotations:
            text = self._simple_edit.toPlainText().strip()
            return text if text else ""

        # Sync editors to annotations first
        self._sync_editors_to_annotations()

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
        """Clear all text areas."""
        self._simple_edit.clear()
        self._global_edit.clear()
        self._clear_frame_widgets()
        self._annotations = []
        self._stack.setCurrentIndex(0)

    def refresh_language(self) -> None:
        """Refresh all translatable strings."""
        self._simple_edit.setPlaceholderText(tr("annotation.draft_placeholder"))
        self._global_edit.setPlaceholderText(tr("annotation.draft.global_context_placeholder"))
        self._update_global_toggle_text()
        # Rebuild per-frame labels
        if self._annotations:
            self._sync_editors_to_annotations()
            self._clear_frame_widgets()
            self._build_per_frame_editors()
            self._load_annotations_to_editors()

    # ── Private: build / clear per-frame editors ─────────────────────

    def _clear_frame_widgets(self) -> None:
        """Remove all dynamic frame/transition widgets from the layout."""
        for fw in self._frame_widgets:
            if fw.get("label"):
                fw["label"].deleteLater()
            if fw.get("edit"):
                fw["edit"].deleteLater()
        self._frame_widgets = []

    def _build_per_frame_editors(self) -> None:
        """Create labeled QPlainTextEdit widgets for each frame and transition."""
        # Insert frame widgets before the stretch at the end
        insert_pos = self._per_frame_layout.count() - 1  # before stretch

        for i, fa in enumerate(self._annotations):
            # Frame label + editor
            frame_label_text = tr("annotation.draft.frame_label").format(
                frame=fa.frame_index, sec=fa.timestamp_sec
            )
            label = QPushButton(frame_label_text)
            label.setFlat(True)
            label.setStyleSheet("text-align: left; font-weight: bold; padding: 2px 0; color: #6496f0;")
            label.setCursor(Qt.CursorShape.PointingHandCursor)
            label.clicked.connect(lambda checked, idx=fa.frame_index: self._on_frame_label_clicked(idx))
            self._per_frame_layout.insertWidget(insert_pos, label)
            insert_pos += 1

            edit = QPlainTextEdit()
            edit.setMaximumHeight(60)
            edit.setPlaceholderText(tr("annotation.draft.frame_placeholder"))
            edit.textChanged.connect(lambda fa_ref=fa, ed=edit: self._on_frame_draft_changed(fa_ref, ed))
            self._per_frame_layout.insertWidget(insert_pos, edit)
            insert_pos += 1

            self._frame_widgets.append({"kind": "frame", "label": label, "edit": edit, "fa": fa})

            # Transition (not for the last frame)
            if i < len(self._annotations) - 1:
                next_fa = self._annotations[i + 1]
                trans_label_text = tr("annotation.draft.transition_label").format(
                    f0=fa.frame_index, f1=next_fa.frame_index,
                    s0=fa.timestamp_sec, s1=next_fa.timestamp_sec,
                )
                t_label = QPushButton(trans_label_text)
                t_label.setFlat(True)
                t_label.setStyleSheet("text-align: left; font-weight: bold; padding: 2px 0; color: #888888;")
                t_label.setCursor(Qt.CursorShape.PointingHandCursor)
                from_idx = fa.frame_index
                to_idx = next_fa.frame_index
                t_label.clicked.connect(
                    lambda checked, fi=from_idx, ti=to_idx: self._on_transition_label_clicked(fi, ti)
                )
                self._per_frame_layout.insertWidget(insert_pos, t_label)
                insert_pos += 1

                t_edit = QPlainTextEdit()
                t_edit.setMaximumHeight(50)
                t_edit.setPlaceholderText(tr("annotation.draft.transition_placeholder"))
                t_edit.textChanged.connect(lambda fa_ref=fa, ed=t_edit: self._on_transition_draft_changed(fa_ref, ed))
                self._per_frame_layout.insertWidget(insert_pos, t_edit)
                insert_pos += 1

                self._frame_widgets.append({"kind": "transition", "label": t_label, "edit": t_edit, "fa": fa})

    def _load_annotations_to_editors(self) -> None:
        """Populate editor widgets from the current annotations data."""
        frame_editors = [fw for fw in self._frame_widgets if fw["kind"] == "frame"]
        trans_editors = [fw for fw in self._frame_widgets if fw["kind"] == "transition"]

        for fw in frame_editors:
            fw["edit"].blockSignals(True)
            fw["edit"].setPlainText(fw["fa"].draft_text)
            fw["edit"].blockSignals(False)

        for fw in trans_editors:
            fw["edit"].blockSignals(True)
            fw["edit"].setPlainText(fw["fa"].transition_text)
            fw["edit"].blockSignals(False)

    def _sync_editors_to_annotations(self) -> None:
        """Write editor content back to FrameAnnotation objects."""
        frame_editors = [fw for fw in self._frame_widgets if fw["kind"] == "frame"]
        trans_editors = [fw for fw in self._frame_widgets if fw["kind"] == "transition"]

        for fw in frame_editors:
            fw["fa"].draft_text = fw["edit"].toPlainText()

        for fw in trans_editors:
            fw["fa"].transition_text = fw["edit"].toPlainText()

    # ── Private: collapsible global context ──────────────────────────

    def _toggle_global_context(self) -> None:
        """Expand or collapse the global context editor."""
        self._global_expanded = not self._global_expanded
        self._global_edit.setVisible(self._global_expanded)
        self._global_sep.setVisible(self._global_expanded)
        self._update_global_toggle_text()

    def _update_global_toggle_text(self) -> None:
        """Update the toggle button text based on expanded state."""
        if self._global_expanded:
            arrow = "▼"
        else:
            arrow = "▶"
        label = tr("annotation.draft.global_context_optional")
        self._global_toggle.setText(f"{arrow} {label}")

    # ── Private: live sync on text change ────────────────────────────

    def _on_frame_draft_changed(self, fa: FrameAnnotation, edit: QPlainTextEdit) -> None:
        """Live-sync frame draft text back to the FrameAnnotation."""
        fa.draft_text = edit.toPlainText()

    def _on_transition_draft_changed(self, fa: FrameAnnotation, edit: QPlainTextEdit) -> None:
        """Live-sync transition text back to the FrameAnnotation."""
        fa.transition_text = edit.toPlainText()

    # ── Private: label click → emit signals for jump ─────────────────

    def _on_frame_label_clicked(self, frame_index: int) -> None:
        """Placeholder: clicking a frame label could jump the video.
        The parent (AnnotationEditor or AnnotationPage) can connect if needed."""
        pass

    def _on_transition_label_clicked(self, from_idx: int, to_idx: int) -> None:
        """Placeholder: clicking a transition label could play the segment."""
        pass
