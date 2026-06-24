"""Chat bubble conversation view with collapsible thinking and branch navigation.

Provides three widget classes:
- CollapsibleSection : reusable expand/collapse QFrame (system prompt, thinking)
- ChatBubble : single message bubble with custom paintEvent for rounded-rect background
- ChatView : scrollable conversation display with input bar
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt, QSize, Signal, QRectF, QPoint, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import re as _re_module

from easyclip.annotation.project import AnnotatedClip, ConversationNode
from easyclip.i18n.strings import tr


# ── theme-aware colors ──────────────────────────────────────────────

def _is_dark_theme() -> bool:
    """Detect whether the application palette is dark."""
    app = QApplication.instance()
    if app is None:
        return True
    bg = app.palette().color(app.palette().ColorRole.Window)
    return bg.lightnessF() < 0.5


def _bubble_colors() -> dict:
    """Return theme-appropriate bubble colors."""
    if _is_dark_theme():
        return {
            "user_bg": QColor(59, 130, 246, 200),
            "user_fg": QColor(255, 255, 255),
            "assistant_bg": QColor(46, 46, 54, 220),
            "assistant_fg": QColor(230, 230, 235),
            "manual_bg": QColor(56, 56, 64, 220),
            "manual_fg": QColor(200, 200, 210),
            "system_bg": QColor(40, 40, 48, 200),
            "system_fg": QColor(200, 200, 210),
            "thinking_bg": QColor(55, 55, 70, 200),
            "thinking_fg": QColor(160, 160, 175),
            "attachment_bg": QColor(30, 30, 40, 200),
        }
    else:
        return {
            "user_bg": QColor(59, 130, 246, 200),
            "user_fg": QColor(255, 255, 255),
            "assistant_bg": QColor(240, 240, 245),
            "assistant_fg": QColor(30, 30, 35),
            "manual_bg": QColor(250, 255, 250),
            "manual_fg": QColor(30, 30, 35),
            "system_bg": QColor(245, 245, 250),
            "system_fg": QColor(80, 80, 90),
            "thinking_bg": QColor(250, 250, 255),
            "thinking_fg": QColor(120, 120, 135),
            "attachment_bg": QColor(235, 235, 245),
        }


BUBBLE_RADIUS = 10.0
BUBBLE_PAD_H = 14
BUBBLE_PAD_V = 8


# ══════════════════════════════════════════════════════════════════════
# CollapsibleSection
# ══════════════════════════════════════════════════════════════════════

class CollapsibleSection(QFrame):
    """A header + collapsible content section.

    Used for system prompt display and thinking/reasoning content.
    """

    toggled = Signal(bool)  # True = expanded

    def __init__(
        self,
        title: str = "",
        parent: QWidget | None = None,
        *,
        initially_expanded: bool = True,
    ) -> None:
        super().__init__(parent)
        self._expanded = initially_expanded

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Header button with arrow indicator
        self._header_btn = QPushButton(self._arrow() + " " + title, self)
        self._header_btn.setFlat(True)
        self._header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_btn.clicked.connect(self._toggle)
        layout.addWidget(self._header_btn)

        # Content label
        self._content = QLabel(self)
        self._content.setWordWrap(True)
        self._content.setTextFormat(Qt.TextFormat.PlainText)
        self._content.setVisible(initially_expanded)
        layout.addWidget(self._content)

    def _arrow(self) -> str:
        return "▼" if self._expanded else "▶"

    def _toggle(self) -> None:
        if self._expanded:
            self.collapse()
        else:
            self.expand()

    def expand(self) -> None:
        self._expanded = True
        self._content.setVisible(True)
        self._update_header()
        self.toggled.emit(True)

    def collapse(self) -> None:
        self._expanded = False
        self._content.setVisible(False)
        self._update_header()
        self.toggled.emit(False)

    def set_title(self, title: str) -> None:
        self._header_btn.setText(self._arrow() + " " + title)

    def set_text(self, text: str) -> None:
        self._content.setText(text)

    def append_text(self, text: str) -> None:
        current = self._content.text()
        self._content.setText(current + text)

    def text(self) -> str:
        return self._content.text()

    def is_expanded(self) -> bool:
        return self._expanded

    def _update_header(self) -> None:
        """Refresh the arrow indicator without changing the title text."""
        current = self._header_btn.text()
        # Replace the arrow prefix (first 2 chars: "▼ " or "▶ ")
        rest = current[2:] if len(current) >= 2 and current[1] == " " else current
        self._header_btn.setText(self._arrow() + " " + rest)


# ══════════════════════════════════════════════════════════════════════
# ChatContentLabel — QTextBrowser-based label with correct sizing
# ══════════════════════════════════════════════════════════════════════

class ChatContentLabel(QTextBrowser):
    """Read-only text widget that correctly sizes for rich text content.

    Replaces QLabel to fix the well-known Qt bug where QLabel + RichText +
    wordWrap returns inflated sizeHint/heightForWidth, causing blank space
    in chat bubbles. QTextBrowser wraps QTextDocument natively and handles
    width→height calculation correctly.

    Provides a QLabel-compatible API subset used by ChatBubble.
    """

    # Bridge signal: QTextBrowser.anchorClicked(QUrl) → linkActivated(str)
    linkActivated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setCursorWidth(0)  # Hide blinking cursor for clean label appearance
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Transparent background — parent ChatBubble paints its own background
        pal = self.palette()
        pal.setColor(pal.ColorRole.Base, QColor(0, 0, 0, 0))
        self.setPalette(pal)

        # Remove document margins for tight sizing
        self.document().setDocumentMargin(0)

        # Track text format for QLabel API compatibility
        self._text_format = Qt.TextFormat.PlainText

        # Bridge anchor clicks to linkActivated(str)
        self.anchorClicked.connect(
            lambda url: self.linkActivated.emit(url.toString())
        )

        # Auto-resize when document reflows
        self.document().documentLayout().documentSizeChanged.connect(
            self._on_doc_size_changed
        )

    def _on_doc_size_changed(self, size) -> None:
        """Adjust widget height to match document content."""
        h = max(int(size.height()), 1)
        if h != self.minimumHeight():
            self.setFixedHeight(h)
            self.updateGeometry()

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Recalculate height when width changes."""
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self._recalc_height()

    def _recalc_height(self) -> None:
        doc = self.document()
        w = self.viewport().width()
        if w > 1:
            doc.setTextWidth(w)
            h = max(int(doc.size().height()), 1)
            if h != self.height():
                self.setFixedHeight(h)

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Pass wheel events to parent scroll area."""
        event.ignore()

    # ── QLabel-compatible API ──────────────────────────────────────

    def setWordWrap(self, wrap: bool) -> None:  # noqa: N802
        if wrap:
            self.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        else:
            self.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)

    def setTextFormat(self, fmt) -> None:  # noqa: N802
        self._text_format = fmt

    def textFormat(self) -> Qt.TextFormat:  # noqa: N802
        return self._text_format

    def setText(self, text: str) -> None:  # noqa: N802
        if self._text_format == Qt.TextFormat.RichText:
            self.setHtml(text)
        else:
            self.setPlainText(text)

    def text(self) -> str:
        return self.toPlainText()

    def setAlignment(self, alignment) -> None:  # noqa: N802
        """Set default text alignment (horizontal component used)."""
        option = self.document().defaultTextOption()
        option.setAlignment(alignment)
        self.document().setDefaultTextOption(option)

    def sizeHint(self) -> QSize:  # noqa: N802
        # When fixedHeight is set, return it directly to prevent the parent
        # layout from over-allocating space based on a stale document height.
        min_h = self.minimumHeight()
        max_h = self.maximumHeight()
        if min_h == max_h and min_h > 0:
            return QSize(super().sizeHint().width(), min_h)
        doc = self.document()
        w = self.viewport().width()
        if w > 1:
            doc.setTextWidth(w)
        h = max(int(doc.size().height()), 1)
        return QSize(super().sizeHint().width(), h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()


# ══════════════════════════════════════════════════════════════════════
# ChatBubble
# ══════════════════════════════════════════════════════════════════════

class ChatBubble(QFrame):
    """A single chat message bubble with rounded-rect background.

    - User bubbles: right-aligned, blue background
    - Assistant bubbles: left-aligned, dark gray background
    - Optional thinking section (assistant only, collapsible)
    - Optional attachment indicator (user only)
    - Optional branch navigation row (when siblings exist)
    """

    branch_navigated = Signal(str, int)   # node_id, direction (-1/+1)
    regenerate_requested = Signal(str)     # node_id
    frame_reference_clicked = Signal(int, float)  # frame_index, timestamp_sec
    annotation_edit_requested = Signal(str, int)  # node_id, index
    annotation_select_toggled = Signal(str, int, bool)  # node_id, index, selected
    user_message_edit_requested = Signal(str)  # node_id
    raw_output_requested = Signal(str)  # node_id

    def __init__(
        self,
        node: ConversationNode,
        parent: QWidget | None = None,
        *,
        show_branch_nav: bool = False,
        branch_label: str = "",
        can_navigate_prev: bool = False,
        can_navigate_next: bool = False,
    ) -> None:
        super().__init__(parent)
        self._node = node
        self._colors = _bubble_colors()
        self._is_user = node.role == "user"

        # ── Build internal layout ──
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 4, 10, 4)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Role label
        role_text = tr("annotation.chat.send") if self._is_user else "Assistant"
        if not self._is_user:
            # Use a label instead of tr key for assistant (not user-visible as bubble label)
            pass

        # Thinking section (assistant only, collapsible)
        self._thinking_section: CollapsibleSection | None = None
        if not self._is_user and node.reasoning:
            dur = node.thinking_duration
            if dur > 0:
                title = tr("annotation.chat.thinking_done", time=dur)
            else:
                title = tr("annotation.chat.thinking_label")
            self._thinking_section = CollapsibleSection(
                title, self, initially_expanded=False
            )
            self._thinking_section.set_text(node.reasoning)
            self._style_thinking_section()
            root.addWidget(self._thinking_section)
        elif not self._is_user:
            # Create a hidden thinking section for streaming (not in layout yet)
            self._thinking_section = CollapsibleSection(
                tr("annotation.chat.thinking_label"), self, initially_expanded=True
            )
            self._style_thinking_section()
            self._thinking_section.setVisible(False)

        # Content label — render [Frame N (Ts)] as clickable links
        self._content_label = ChatContentLabel(self)
        self._content_label.setContentsMargins(0, 0, 0, 0)
        self._content_label.linkActivated.connect(self._on_frame_link_clicked)
        # Set foreground color via palette (plain text) + document stylesheet (HTML)
        is_manual_node = not self._is_user and node.source == "manual"
        if self._is_user:
            fg = self._colors["user_fg"]
        elif is_manual_node:
            fg = self._colors["manual_fg"]
        else:
            fg = self._colors["assistant_fg"]
        pal = self._content_label.palette()
        pal.setColor(pal.ColorRole.Text, fg)
        pal.setColor(pal.ColorRole.WindowText, fg)
        self._content_label.setPalette(pal)
        self._content_label.document().setDefaultStyleSheet(
            f"* {{ color: {fg.name()}; margin: 0; padding: 0; }}"
        )
        if node.content:
            self._set_content_text(node.content)
        root.addWidget(self._content_label)

        # Attachment indicator (user only)
        if self._is_user and node.content_parts:
            img_count = sum(
                1 for p in node.content_parts
                if p.get("type") in ("image_url", "video_url")
            )
            if img_count:
                attach = QLabel(self)
                if any(p.get("type") == "video_url" for p in node.content_parts):
                    attach.setText(tr("annotation.chat.attachment_video"))
                else:
                    attach.setText(tr("annotation.chat.attachment_images", n=str(img_count)))
                attach.setStyleSheet(
                    f"color: {self._colors['user_fg'].name()}; font-size: 11px;"
                )
                root.addWidget(attach)

        # Action buttons (edit for user, regenerate for assistant)
        self._build_action_buttons(root)

        # Branch navigation (when siblings exist)
        self._branch_row: QWidget | None = None
        if show_branch_nav:
            self._build_branch_nav(root, branch_label, can_navigate_prev, can_navigate_next)

        # Context menu for assistant bubbles (keep for discoverability)
        if not self._is_user:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(self._show_context_menu)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def _build_action_buttons(self, root: QVBoxLayout) -> None:
        """Add visible action buttons (✏️ for user, 🔄/📋 for assistant)."""
        action_widget = QWidget(self)
        action_widget.setFixedHeight(20)
        btn_row = QHBoxLayout(action_widget)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(4)
        btn_style = (
            "QPushButton { border: none; background: transparent; font-size: 13px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.1); border-radius: 4px; }"
        )
        if self._is_user:
            btn_edit = QPushButton("✏️", self)
            btn_edit.setFixedHeight(20)
            btn_edit.setToolTip("Edit message")
            btn_edit.setStyleSheet(btn_style)
            btn_edit.clicked.connect(lambda: self.user_message_edit_requested.emit(self._node.id))
            btn_row.addWidget(btn_edit)
        else:
            btn_regen = QPushButton("🔄", self)
            btn_regen.setFixedHeight(20)
            btn_regen.setToolTip(tr("annotation.chat.regenerate"))
            btn_regen.setStyleSheet(btn_style)
            btn_regen.clicked.connect(lambda: self.regenerate_requested.emit(self._node.id))
            btn_row.addWidget(btn_regen)
            btn_raw = QPushButton("📋", self)
            btn_raw.setFixedHeight(20)
            btn_raw.setToolTip("View raw output")
            btn_raw.setStyleSheet(btn_style)
            btn_raw.clicked.connect(lambda: self.raw_output_requested.emit(self._node.id))
            btn_row.addWidget(btn_raw)
        btn_row.addStretch()
        root.addWidget(action_widget)

    def _build_branch_nav(
        self,
        root: QVBoxLayout,
        label: str,
        can_prev: bool,
        can_next: bool,
    ) -> None:
        row = QWidget(self)
        row.setFixedHeight(24)
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(4)

        row_lay.addStretch()

        btn_prev = QPushButton("←", row)
        btn_prev.setFixedSize(24, 24)
        btn_prev.setEnabled(can_prev)
        btn_prev.setToolTip(tr("annotation.chat.branch_prev_tip"))
        btn_prev.clicked.connect(lambda: self.branch_navigated.emit(self._node.id, -1))
        row_lay.addWidget(btn_prev)

        lbl = QLabel(label, row)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row_lay.addWidget(lbl)

        btn_next = QPushButton("→", row)
        btn_next.setFixedSize(24, 24)
        btn_next.setEnabled(can_next)
        btn_next.setToolTip(tr("annotation.chat.branch_next_tip"))
        btn_next.clicked.connect(lambda: self.branch_navigated.emit(self._node.id, 1))
        row_lay.addWidget(btn_next)

        row_lay.addStretch()
        root.addWidget(row)
        self._branch_row = row

    def _to_rich_text(self, text: str) -> str:
        """Convert [Frame N (Ts)] patterns to clickable HTML links.
        Uses light color on dark user bubbles, blue on assistant bubbles."""
        import re as _re
        link_color = "#fde68a" if self._is_user else "#60a5fa"
        def _replace(match):
            fi = match.group(1)
            ts = match.group(2)
            return f'<a href="frame:{fi}:{ts}" style="color:{link_color};text-decoration:none;">[Frame {fi} ({ts}s)]</a>'
        body = _re.sub(r'\[Frame (\d+) \(([\d.]+)s\)\]', _replace, text)
        body = body.replace('\n', '<br>')
        return f'<span>{body}</span>'

    def _style_thinking_section(self) -> None:
        """Apply thinking-specific background and text colors."""
        if self._thinking_section is not None:
            self._thinking_section._content.setStyleSheet(
                f"background-color: {self._colors['thinking_bg'].name()}; "
                f"color: {self._colors['thinking_fg'].name()}; "
                f"padding: 6px; border-radius: 4px;"
            )

    def _on_frame_link_clicked(self, link: str) -> None:
        """Handle clicks on [Frame N (Ts)] links."""
        if link.startswith("frame:"):
            parts = link.split(":")
            if len(parts) == 3:
                fi = int(parts[1])
                ts = float(parts[2])
                self.frame_reference_clicked.emit(fi, ts)

    def _show_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        regen = menu.addAction(tr("annotation.chat.regenerate"))
        regen.triggered.connect(lambda: self.regenerate_requested.emit(self._node.id))
        menu.exec(self.mapToGlobal(pos))

    # ── paint background ──────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        is_manual = not self._is_user and self._node.source == "manual"
        if self._is_user:
            bg = self._colors["user_bg"]
        elif is_manual:
            bg = self._colors["manual_bg"]
        else:
            bg = self._colors["assistant_bg"]
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, BUBBLE_RADIUS, BUBBLE_RADIUS)
        painter.fillPath(path, bg)

        super().paintEvent(event)

    # ── streaming helpers ─────────────────────────────────────────

    def append_thinking(self, text: str) -> None:
        """Append reasoning text during streaming."""
        if self._thinking_section is not None:
            if not self._thinking_section.isVisible():
                lay = self.layout()
                if lay is not None and lay.indexOf(self._thinking_section) == -1:
                    lay.insertWidget(0, self._thinking_section)
                self._thinking_section.setVisible(True)
            self._thinking_section.append_text(text)

    def _set_content_text(self, text: str) -> None:
        """Set content label text, using RichText only when frame references present."""
        if "[Frame " in text:
            self._content_label.setTextFormat(Qt.TextFormat.RichText)
            self._content_label.setText(self._to_rich_text(text))
        else:
            self._content_label.setTextFormat(Qt.TextFormat.PlainText)
            self._content_label.setText(text)
        # Ensure visible when content is set (may have been hidden by set_annotations)
        if not self._content_label.isVisible():
            self._content_label.setVisible(True)

    def append_content(self, text: str) -> None:
        """Append content text during streaming (plain text only, links processed by finalize)."""
        self._content_label.setTextFormat(Qt.TextFormat.PlainText)
        self._content_label.setText(self._content_label.text() + text)

    def set_content(self, text: str) -> None:
        """Set the final content text with clickable frame references."""
        self._set_content_text(text)

    def finalize(self) -> str:
        """Mark streaming complete. Returns the full accumulated content."""
        return self._content_label.text()

    def get_thinking_text(self) -> str:
        """Return accumulated thinking text."""
        if self._thinking_section is not None:
            return self._thinking_section.text()
        return ""

    def collapse_thinking(self, duration: float = 0.0) -> None:
        """Auto-collapse thinking section and set final title."""
        if self._thinking_section is not None:
            if duration > 0:
                self._thinking_section.set_title(
                    tr("annotation.chat.thinking_done", time=duration)
                )
            self._thinking_section.collapse()

    def update_branch_nav(
        self,
        label: str,
        can_prev: bool,
        can_next: bool,
    ) -> None:
        """Show or update branch navigation on this bubble."""
        if self._branch_row is None:
            # Build it now
            self._build_branch_nav(
                self.layout() if self.layout() else QVBoxLayout(),
                label, can_prev, can_next,
            )

    def node(self) -> ConversationNode:
        return self._node

    def set_annotations(self, annotations: list[str], discussion: str, selected_list: list[bool]) -> None:
        """Show discussion text + annotation frames below."""
        if discussion:
            self._set_content_text(discussion)
        else:
            # Hide empty content label to avoid blank space above annotation frames
            self._content_label.setPlainText("")
            self._content_label.setFixedHeight(0)
            self._content_label.setVisible(False)
        # Remove old annotation frames
        if hasattr(self, '_annotation_frames'):
            for f in self._annotation_frames:
                self.layout().removeWidget(f)
                f.deleteLater()
        self._annotation_frames = []
        # Add annotation frames
        if annotations:
            sep = QFrame(self)
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("QFrame { color: #555; }")
            sep.setFixedHeight(1)
            self.layout().addWidget(sep)
            for i, txt in enumerate(annotations):
                frame = self._build_annotation_frame(txt, i, selected_list[i] if i < len(selected_list) else False)
                self.layout().addWidget(frame)
                self._annotation_frames.append(frame)

    def _build_annotation_frame(self, text: str, index: int, selected: bool) -> QFrame:
        frame = QFrame(self)
        dark = _is_dark_theme()
        bg = "#2d5240" if dark else "#e8f5e9"
        bd = "#f0a500" if selected else ("#49805a" if dark else "#a5d6a7")
        fg = "#c8e6d0" if dark else "#1b5e20"
        btn_color = "#81c784" if dark else "#2e7d32"
        hover_bg = "#3d6a50" if dark else "#c8e6c9"

        frame.setStyleSheet(
            f"QFrame {{ background: {bg}; border-radius: 6px; "
            f"border: {'2px' if selected else '1px'} solid {bd}; }}"
        )
        flay = QVBoxLayout(frame)
        flay.setContentsMargins(8, 4, 8, 4)
        flay.setSpacing(0)
        flay.setAlignment(Qt.AlignmentFlag.AlignTop)
        label = ChatContentLabel(frame)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setText(text)
        label.setStyleSheet(
            f"QTextBrowser {{ background: transparent; border: none; color: {fg}; }}"
        )
        pal = label.palette()
        pal.setColor(pal.ColorRole.Text, QColor(fg))
        pal.setColor(pal.ColorRole.WindowText, QColor(fg))
        label.setPalette(pal)
        label.document().setDefaultStyleSheet(f"* {{ color: {fg}; margin: 0; padding: 0; }}")
        flay.addWidget(label)
        action_widget = QWidget(frame)
        action_widget.setFixedHeight(28)
        btn_row = QHBoxLayout(action_widget)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_style = (
            f"QPushButton {{ border: none; background: transparent; font-size: 14px; color: {btn_color}; }}"
            f"QPushButton:hover {{ background: {hover_bg}; border-radius: 4px; }}"
        )
        btn_edit = QPushButton("✏️", frame)
        btn_edit.setFixedSize(28, 28)
        btn_edit.setToolTip(tr("annotation.chat.annotation_edit_tip"))
        btn_edit.setStyleSheet(btn_style)
        btn_edit.clicked.connect(lambda: self.annotation_edit_requested.emit(self._node.id, index))
        btn_row.addWidget(btn_edit)
        btn_select = QPushButton("☑" if selected else "☐", frame)
        btn_select.setFixedSize(28, 28)
        btn_select.setToolTip(tr("annotation.chat.annotation_select_tip"))
        btn_select.setStyleSheet(btn_style)
        btn_select.clicked.connect(lambda: self.annotation_select_toggled.emit(self._node.id, index, not selected))
        btn_row.addWidget(btn_select)
        btn_row.addStretch()
        flay.addWidget(action_widget)
        return frame


# ══════════════════════════════════════════════════════════════════════
# ChatView
# ══════════════════════════════════════════════════════════════════════

# ── syntax highlighter for [Frame N (Ts)] references in chat input ──

class FrameRefHighlighter(QSyntaxHighlighter):
    """Highlight [Frame N (Ts)] patterns in blue to distinguish from normal text."""

    _PATTERN = _re_module.compile(r'\[Frame \d+ \([\d.]+s\)\]|\[Video Clip\]|\[Subtitles\]')

    def highlightBlock(self, text: str) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#60a5fa"))  # blue-400
        for match in self._PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), fmt)


# ══════════════════════════════════════════════════════════════════════

class ChatView(QWidget):
    """Scrollable conversation display with input bar.

    Signals:
        message_sent(text) — user typed a message and clicked Send
        regenerate_requested(node_id) — user requested regeneration
        branch_navigated(node_id, direction) — user clicked branch arrows
        frame_reference_clicked(frame_index, timestamp_sec) — user clicked frame ref
        frame_ref_insert_requested(frame_index, timestamp_sec) — user wants to insert ref
    """

    message_sent = Signal(str)
    regenerate_requested = Signal(str)
    branch_navigated = Signal(str, int)
    frame_reference_clicked = Signal(int, float)
    frame_ref_insert_requested = Signal(int, float)
    add_manual_annotation_requested = Signal()
    annotation_edit_requested = Signal(str, int)
    annotation_select_toggled = Signal(str, int, bool)
    user_message_edit_requested = Signal(str)
    raw_output_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bubbles: list[ChatBubble] = []
        self._system_section: CollapsibleSection | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── System prompt section (collapsible, initially expanded) ──
        self._system_section = CollapsibleSection(
            tr("annotation.chat.system_header"), self, initially_expanded=False
        )
        self._system_section.setVisible(False)
        root.addWidget(self._system_section)

        # ── Scroll area for bubbles ──
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._bubble_container = QWidget(self._scroll)
        self._bubble_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._bubble_layout = QVBoxLayout(self._bubble_container)
        self._bubble_layout.setContentsMargins(8, 4, 8, 4)
        self._bubble_layout.setSpacing(8)

        self._scroll.setWidget(self._bubble_container)
        root.addWidget(self._scroll, 1)

        # ── Input row ──
        input_row = QWidget(self)
        input_lay = QHBoxLayout(input_row)
        input_lay.setContentsMargins(8, 4, 8, 4)
        input_lay.setSpacing(6)

        self._btn_frame_picker = QPushButton("@+")
        self._btn_frame_picker.setToolTip(tr("annotation.chat.frame_picker_tip"))
        self._btn_frame_picker.setEnabled(False)
        self._btn_frame_picker.clicked.connect(self._show_frame_picker)
        self._btn_frame_picker.setFixedWidth(32)
        input_lay.addWidget(self._btn_frame_picker)

        self._input = QTextEdit()
        self._input.setAcceptRichText(False)
        self._input.setPlaceholderText(tr("annotation.chat.input_placeholder"))
        self._input.setReadOnly(True)
        self._input.setFixedHeight(30)
        self._input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._input.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._input.installEventFilter(self)
        input_lay.addWidget(self._input, 1)

        # Syntax highlighter for frame references
        self._input_highlighter = FrameRefHighlighter(self._input.document())

        self._btn_send = QPushButton(tr("annotation.chat.send"))
        self._btn_send.setEnabled(False)
        self._btn_send.clicked.connect(self._on_send)
        input_lay.addWidget(self._btn_send)

        self._annotation_frames: list[dict] = []
        self._context_handles: list[str] = []  # [Video Clip], [Subtitles], etc.
        self._add_manual_btn: QPushButton | None = None

        root.addWidget(input_row)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._input and event.type() == event.Type.KeyPress:
            ke = QKeyEvent(event)
            if ke.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if ke.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    return False
                else:
                    self._on_send()
                    return True
        return super().eventFilter(obj, event)

    def _on_send(self) -> None:
        text = self._input.toPlainText().strip()
        has_handles = bool(self._context_handles)
        # Strip handle markers so they don't leak into LLM content
        for h in self._context_handles:
            text = text.replace(h, "").strip()
        if text or has_handles:
            self.message_sent.emit(text)
        self._input.clear()

    # ── public API ─────────────────────────────────────────────────

    def set_system_prompt(self, text: str) -> None:
        if not text.strip():
            self._system_section.setVisible(False)
            return
        self._system_section.set_text(text)
        self._system_section.setVisible(True)

    def set_input_enabled(self, enabled: bool) -> None:
        self._input.setReadOnly(not enabled)
        self._btn_send.setEnabled(enabled)
        self._btn_frame_picker.setEnabled(enabled and len(self._annotation_frames) > 0)

    def set_annotation_frames(self, frames: list[dict]) -> None:
        """Update the frame picker's data source. Each dict has frame_index, timestamp_sec."""
        self._annotation_frames = frames
        self._btn_frame_picker.setEnabled(len(frames) > 0 and not self._input.isReadOnly())

    def set_context_handles(self, handles: list[str]) -> None:
        """Set undeletable context handles like [Video Clip] or [Subtitles]."""
        self._context_handles = list(handles)
        self._insert_handles()

    def clear_context_handles(self) -> None:
        """Remove all context handles."""
        self._context_handles.clear()
        # Remove handle text from the input
        full_text = self._input.toPlainText()
        for h in ["[Video Clip]", "[Subtitles]"]:
            full_text = full_text.replace(h, "")
        self._input.setPlainText(full_text.strip())

    def _insert_handles(self) -> None:
        """Insert context handles at the start of the input field."""
        if not self._context_handles:
            return
        current = self._input.toPlainText()
        # Remove any existing handle text to avoid duplication
        for h in ["[Video Clip]", "[Subtitles]"]:
            current = current.replace(h, "")
        # Build handle prefix
        prefix = "".join(self._context_handles) + (" " if current.strip() else "")
        self._input.setPlainText(prefix + current.strip())

    def _show_frame_picker(self) -> None:
        """Show a popup menu listing all annotation frames for quick reference insertion."""
        menu = QMenu(self)
        for fa in sorted(self._annotation_frames, key=lambda f: f["frame_index"]):
            fi = fa["frame_index"]
            ts = fa["timestamp_sec"]
            label = f"[Frame {fi} ({ts:.1f}s)]"
            action = menu.addAction(label)
            action.triggered.connect(
                lambda checked=False, fi=fi, ts=ts: self.frame_ref_insert_requested.emit(fi, ts)
            )
        menu.exec(self._btn_frame_picker.mapToGlobal(
            self._btn_frame_picker.rect().bottomLeft()
        ))

    def set_streaming_mode(self, active: bool) -> None:
        """Disable input during streaming; re-enable after."""
        self._input.setReadOnly(active)
        self._btn_send.setEnabled(not active)

    def input_text(self) -> str:
        return self._input.toPlainText().strip()

    def load_clip(self, clip: AnnotatedClip, system_prompt: str = "") -> None:
        """Rebuild the entire chat display from clip's conversation tree."""
        self._hide_add_manual_button()
        self.clear()
        self.set_system_prompt(system_prompt)

        if clip.root_node_id is None or not clip.tree_nodes:
            # Show empty hint
            hint = QLabel(tr("annotation.chat.empty_hint"))
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setStyleSheet("color: #888; padding: 40px;")
            hint.setWordWrap(True)
            self._bubble_layout.addWidget(hint)
            self._hint_label = hint
            return
        self._hint_label = None

        path = clip.conversation_path()
        for node_id in path:
            node = clip.tree_nodes.get(node_id)
            if node is None:
                continue
            self._add_bubble_for_node(clip, node)

        QTimer.singleShot(0, self._after_load_settle)

    def _add_bubble_for_node(self, clip: AnnotatedClip, node: ConversationNode) -> ChatBubble:
        bc = clip.branch_count(node.id)
        si = clip.sibling_index(node.id)
        can_prev = si > 0
        can_next = si < bc - 1
        label = ""
        if bc > 1:
            label = tr("annotation.chat.branch_label", current=str(si + 1), total=str(bc))

        bubble = ChatBubble(
            node, self._bubble_container,
            show_branch_nav=(bc > 1 or len(node.children_ids) > 1),
            branch_label=label,
            can_navigate_prev=can_prev,
            can_navigate_next=can_next,
        )

        # Align user bubbles right, assistant left
        bubble.setMaximumWidth(int(self.width() * 0.85))
        if node.role == "user":
            bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
            self._bubble_layout.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight)
        else:
            bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            self._bubble_layout.addWidget(bubble)

        # Connect signals
        bubble.branch_navigated.connect(self.branch_navigated.emit)
        bubble.regenerate_requested.connect(self.regenerate_requested.emit)
        bubble.frame_reference_clicked.connect(self.frame_reference_clicked.emit)
        bubble.annotation_edit_requested.connect(self.annotation_edit_requested.emit)
        bubble.annotation_select_toggled.connect(self.annotation_select_toggled.emit)
        bubble.user_message_edit_requested.connect(self.user_message_edit_requested.emit)
        bubble.raw_output_requested.connect(self.raw_output_requested.emit)

        # Restore annotation frames for loaded assistant nodes
        if node.role == "assistant" and node.annotations:
            disc = node.content
            for a in node.annotations:
                disc = disc.replace(f"<annotation>{a}</annotation>", "").strip()
            bubble.set_annotations(node.annotations, disc, node.annotation_selected)

        self._bubbles.append(bubble)
        return bubble

    def add_user_bubble(self, node: ConversationNode) -> ChatBubble:
        """Append a user message bubble."""
        self._hide_add_manual_button()
        # Remove hint if present
        if getattr(self, '_hint_label', None) and self._hint_label:
            self._bubble_layout.removeWidget(self._hint_label)
            self._hint_label.deleteLater()
            self._hint_label = None

        bubble = ChatBubble(node, self._bubble_container)

        # Right-align user bubble
        bubble.setMaximumWidth(int(self.width() * 0.85))
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self._bubble_layout.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight)

        bubble.branch_navigated.connect(self.branch_navigated.emit)
        bubble.regenerate_requested.connect(self.regenerate_requested.emit)
        bubble.frame_reference_clicked.connect(self.frame_reference_clicked.emit)
        bubble.annotation_edit_requested.connect(self.annotation_edit_requested.emit)
        bubble.annotation_select_toggled.connect(self.annotation_select_toggled.emit)
        bubble.user_message_edit_requested.connect(self.user_message_edit_requested.emit)
        bubble.raw_output_requested.connect(self.raw_output_requested.emit)
        self._bubbles.append(bubble)
        self.scroll_to_bottom()
        return bubble

    def add_assistant_bubble(self, node: ConversationNode) -> ChatBubble:
        """Append an (initially empty) assistant bubble for streaming."""
        self._hide_add_manual_button()
        # Remove hint if present
        if getattr(self, '_hint_label', None) and self._hint_label:
            self._bubble_layout.removeWidget(self._hint_label)
            self._hint_label.deleteLater()
            self._hint_label = None

        bubble = ChatBubble(node, self._bubble_container)
        bubble.setMaximumWidth(int(self.width() * 0.85))
        bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._bubble_layout.addWidget(bubble)
        bubble.branch_navigated.connect(self.branch_navigated.emit)
        bubble.regenerate_requested.connect(self.regenerate_requested.emit)
        bubble.frame_reference_clicked.connect(self.frame_reference_clicked.emit)
        bubble.annotation_edit_requested.connect(self.annotation_edit_requested.emit)
        bubble.annotation_select_toggled.connect(self.annotation_select_toggled.emit)
        bubble.user_message_edit_requested.connect(self.user_message_edit_requested.emit)
        bubble.raw_output_requested.connect(self.raw_output_requested.emit)
        self._bubbles.append(bubble)
        self.scroll_to_bottom()
        return bubble

    def get_last_assistant_bubble(self) -> ChatBubble | None:
        for b in reversed(self._bubbles):
            if not b._is_user:
                return b
        return None

    def clear(self) -> None:
        """Remove all bubbles."""
        self._hide_add_manual_button()
        for b in self._bubbles:
            self._bubble_layout.removeWidget(b)
            b.hide()
            b.deleteLater()
        self._bubbles.clear()
        if getattr(self, '_hint_label', None) and self._hint_label:
            self._bubble_layout.removeWidget(self._hint_label)
            self._hint_label.hide()
            self._hint_label.deleteLater()
            self._hint_label = None
        self._bubble_layout.invalidate()
        self._bubble_container.updateGeometry()

    def rebuild_from_position(self, clip: AnnotatedClip, node_id: str) -> None:
        """Rebuild the chat display starting from the branch point at node_id."""
        self.load_clip(clip, "")  # system prompt unchanged via caller
        self.scroll_to_bottom()

    def scroll_to_bottom(self) -> None:
        QTimer.singleShot(20, self._do_scroll)

    def _do_scroll(self) -> None:
        sb = self._scroll.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_bubble_max_widths()

    def _update_bubble_max_widths(self) -> None:
        new_max = int(self.width() * 0.85)
        if new_max > 0:
            for b in self._bubbles:
                if b.maximumWidth() != new_max:
                    b.setMaximumWidth(new_max)

    def _after_load_settle(self) -> None:
        """Deferred after load_clip: recalculate heights and scroll.

        At this point the layout has assigned final widths to all widgets,
        so content labels can recalculate their heights accurately.
        """
        self._update_bubble_max_widths()
        for b in self._bubbles:
            b._content_label._recalc_height()
            b.updateGeometry()
        self._bubble_layout.invalidate()
        self._bubble_container.updateGeometry()
        self.scroll_to_bottom()

    def _show_add_manual_button(self) -> None:
        """Show a '+' button below the last assistant bubble to add a manual annotation."""
        self._hide_add_manual_button()
        btn = QPushButton("+", self._bubble_container)
        btn.setFixedSize(28, 28)
        btn.setToolTip(tr("annotation.chat.add_manual_tip"))
        btn.setStyleSheet(
            "QPushButton { border: 1px solid #888; border-radius: 14px; "
            "background: #3a3a44; color: #ccc; font-size: 16px; font-weight: bold; }"
            "QPushButton:hover { background: #4a4a55; color: #fff; }"
        )
        btn.clicked.connect(self.add_manual_annotation_requested.emit)
        self._bubble_layout.addWidget(btn)
        self._add_manual_btn = btn
        self.scroll_to_bottom()

    def _hide_add_manual_button(self) -> None:
        """Remove the '+' button if present."""
        if self._add_manual_btn is not None:
            self._bubble_layout.removeWidget(self._add_manual_btn)
            self._add_manual_btn.deleteLater()
            self._add_manual_btn = None

    def refresh_language(self) -> None:
        """Update translatable strings after language change."""
        self._btn_send.setText(tr("annotation.chat.send"))
        self._btn_frame_picker.setToolTip(tr("annotation.chat.frame_picker_tip"))
        self._input.setPlaceholderText(tr("annotation.chat.input_placeholder"))
        if self._system_section is not None:
            self._system_section.set_title(tr("annotation.chat.system_header"))

    def insert_frame_ref_at_cursor(self, frame_index: int, timestamp_sec: float) -> None:
        """Insert [Frame N (Ts)] at the current cursor position in the input field."""
        ref = f"[Frame {frame_index} ({timestamp_sec:.1f}s)]"
        self._input.textCursor().insertText(ref)
        self._input.setFocus()
