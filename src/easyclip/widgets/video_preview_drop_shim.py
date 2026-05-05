"""Overlay as *child* of QVideoWidget so drag/drop hits Qt above the internal video surface (Windows/Qt6)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent, QWheelEvent
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QApplication, QWidget


class VideoPreviewDropShim(QWidget):
    """Must be parented to QVideoWidget; handles file drop and forwards pointer events to the video widget."""

    video_dropped = Signal(object)

    def __init__(self, video_widget: QVideoWidget, video_suffixes: frozenset[str]) -> None:
        super().__init__(video_widget)
        self._video = video_widget
        self._suffixes = video_suffixes
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self._video.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._video and event.type() == QEvent.Type.Resize:
            self.setGeometry(0, 0, max(1, self._video.width()), max(1, self._video.height()))
            self.raise_()
        return False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setGeometry(0, 0, max(1, self._video.width()), max(1, self._video.height()))
        self.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.setGeometry(0, 0, max(1, self._video.width()), max(1, self._video.height()))

    def _first_video_path(self, md: QMimeData) -> Path | None:
        if md is None or not md.hasUrls():
            return None
        for u in md.urls():
            if not u.isLocalFile():
                continue
            p = Path(u.toLocalFile())
            if p.is_file() and p.suffix.lower() in self._suffixes:
                return p
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._first_video_path(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if self._first_video_path(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        path = self._first_video_path(event.mimeData())
        if path is not None:
            event.acceptProposedAction()
            self.video_dropped.emit(path)
        else:
            event.ignore()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        QApplication.sendEvent(self._video, event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        QApplication.sendEvent(self._video, event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        QApplication.sendEvent(self._video, event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        QApplication.sendEvent(self._video, event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        QApplication.sendEvent(self._video, event)
