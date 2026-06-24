"""Thumbnail strip widget — horizontal scrollable bar of annotation frame previews.

ThumbnailItem: single frame thumbnail with committed/pending color coding.
ThumbnailStrip: QScrollArea container managing the horizontal layout.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from easyclip.i18n.strings import tr

THUMB_W = 80
THUMB_H = 65
THUMB_IMG_H = 50
THUMB_LABEL_H = 12

PENDING_COLOR = QColor(34, 197, 94)      # green
COMMITTED_COLOR = QColor(90, 120, 200)    # blue (matches timeline markers)


class ThumbnailItem(QFrame):
    """A single annotation frame thumbnail with click/double-click support."""

    clicked = Signal(int, float)         # frame_index, timestamp_sec
    double_clicked = Signal(int, float)  # frame_index, timestamp_sec

    def __init__(
        self,
        frame_index: int,
        timestamp_sec: float,
        image_path: str,
        committed: bool,
        project_dir: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._frame_index = frame_index
        self._timestamp_sec = timestamp_sec
        self._committed = committed
        self._selected = False

        self.setFixedSize(THUMB_W, THUMB_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)

        # Image label
        self._img_label = QLabel(self)
        self._img_label.setFixedSize(THUMB_W - 4, THUMB_IMG_H)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setScaledContents(True)

        # Load and scale image
        from pathlib import Path
        img_path = Path(project_dir) / image_path
        if img_path.is_file():
            pix = QPixmap(str(img_path))
            if not pix.isNull():
                self._img_label.setPixmap(
                    pix.scaled(THUMB_W - 4, THUMB_IMG_H, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
        layout.addWidget(self._img_label)

        # Frame number label
        self._label = QLabel(str(frame_index), self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self._label.font()
        font.setPointSize(8)
        self._label.setFont(font)
        layout.addWidget(self._label, 0)

        self._update_style()

    def _update_style(self) -> None:
        color = COMMITTED_COLOR if self._committed else PENDING_COLOR
        alpha = 230 if self._selected else 160
        border_w = 2 if self._selected else 1
        self.setStyleSheet(
            f"ThumbnailItem {{ border: {border_w}px solid {color.name()}; "
            f"background: transparent; border-radius: 3px; }}"
        )

    def frame_index(self) -> int:
        return self._frame_index

    def is_committed(self) -> bool:
        return self._committed

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._update_style()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self._frame_index, self._timestamp_sec)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit(self._frame_index, self._timestamp_sec)
        super().mouseDoubleClickEvent(event)


class ThumbnailStrip(QWidget):
    """Horizontal scrollable strip of ThumbnailItem widgets.

    Signals:
        thumbnail_clicked(frame_index, ts) — single click on a thumbnail
        thumbnail_double_clicked(frame_index, ts) — double click (insert reference)
        thumbnail_delete_requested(frame_index) — delete from context menu
    """

    thumbnail_clicked = Signal(int, float)
    thumbnail_double_clicked = Signal(int, float)
    thumbnail_delete_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[ThumbnailItem] = []
        self._project_dir: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea(self)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidgetResizable(True)

        self._container = QWidget(self._scroll)
        self._container_layout = QHBoxLayout(self._container)
        self._container_layout.setContentsMargins(4, 2, 4, 2)
        self._container_layout.setSpacing(4)

        self._empty_label = QLabel(tr("annotation.thumbnail_empty"), self._container)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self._empty_label.font()
        font.setPointSize(9)
        self._empty_label.setFont(font)
        self._empty_label.setStyleSheet("color: #888;")
        self._container_layout.addWidget(self._empty_label, 1)

        self._container_layout.addStretch(1)

        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll)

    def set_annotations(
        self,
        annotations: list,
        project_dir: str,
        selected_frame_index: int | None = None,
    ) -> None:
        """Rebuild all thumbnails from the given annotation list.

        Args:
            annotations: list of FrameAnnotation objects
            project_dir: project root directory for resolving image paths
            selected_frame_index: the frame_index to highlight as selected
        """
        self.clear()
        self._project_dir = project_dir

        for fa in annotations:
            item = ThumbnailItem(
                fa.frame_index,
                fa.timestamp_sec,
                fa.image_path,
                fa.committed,
                project_dir,
                self._container,
            )
            if selected_frame_index is not None and fa.frame_index == selected_frame_index:
                item.set_selected(True)
            item.clicked.connect(self.thumbnail_clicked.emit)
            item.double_clicked.connect(self.thumbnail_double_clicked.emit)
            item.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            item.customContextMenuRequested.connect(
                lambda pos, it=item: self._show_context_menu(it, pos)
            )
            self._items.append(item)
            self._container_layout.insertWidget(self._container_layout.count() - 1, item)

        self._empty_label.setVisible(len(annotations) == 0)

    def clear(self) -> None:
        """Remove all thumbnails."""
        for item in self._items:
            self._container_layout.removeWidget(item)
            item.deleteLater()
        self._items.clear()
        self._empty_label.setVisible(True)

    def set_selected(self, frame_index: int | None) -> None:
        """Highlight the thumbnail with the given frame_index."""
        for item in self._items:
            item.set_selected(item.frame_index() == frame_index)

    def _show_context_menu(self, item: ThumbnailItem, pos) -> None:
        menu = QMenu(self)
        fi = item.frame_index()
        ts = item._timestamp_sec

        seek = menu.addAction(f"Seek to Frame {fi}")
        seek.triggered.connect(lambda: self.thumbnail_clicked.emit(fi, ts))

        insert = menu.addAction("Insert Reference")
        insert.triggered.connect(lambda: self.thumbnail_double_clicked.emit(fi, ts))

        if not item.is_committed():
            menu.addSeparator()
            delete = menu.addAction("Delete Frame")
            delete.triggered.connect(lambda: self.thumbnail_delete_requested.emit(fi))

        menu.exec(item.mapToGlobal(pos))
