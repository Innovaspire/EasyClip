"""AnnotationPage: clip annotation tab with video preview, timeline, and LLM integration."""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import QEvent, QThread, QTimer, Qt, Signal, QUrl
from PySide6.QtGui import QAction, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimedia import QVideoSink
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from easyclip.annotation.project import AnnotationProject, AnnotatedClip, FrameAnnotation
from easyclip.annotation.settings import AnnotationSettings, OMNI_MEDIA_FORMAT_QWEN
from easyclip.annotation.widgets.chat_view import ChatView
from easyclip.annotation.widgets.llm_panel import LLMPanel
from easyclip.annotation.widgets.thumbnail_strip import ThumbnailStrip
from easyclip.core.ffmpeg_util import probe_video, find_ffmpeg, extract_frame_png
from easyclip.core.settings import AppSettings
from easyclip.core.timebase import Timebase
from easyclip.core.subtitle import SubtitleTrack, find_matching_subtitle, parse_subtitle_file
from easyclip.core.theme import Theme, WidgetColors, on_theme_changed, set_theme
from easyclip.i18n.strings import tr
from easyclip.widgets.timeline_widget import TimelineWidget
from easyclip.widgets.video_preview_widget import VideoPreviewWidget


# ── API format dispatch: model-fetching ───────────────────────────────
# Each format has its own URL builder + response parser.
# To add a new format, add an entry in both _build_models_url()
# and _parse_models_response().


def _build_models_url(base_url: str, api_format: str) -> str:
    """Build the GET URL for listing models, based on API format."""
    base = base_url.rstrip("/")
    if api_format == "openai_compatible":
        # Standard OpenAI API prefix: /v1/models
        if not base.endswith("/v1"):
            base += "/v1"
        return f"{base}/models"
    # Future formats:
    # elif api_format == "anthropic":
    #     return f"{base}/v1/models"
    raise NotImplementedError(f"Model fetching not implemented for API format: {api_format}")


def _parse_models_response(data: dict, api_format: str) -> list[str]:
    """Parse a JSON response dict into a list of model name strings.

    Args:
        data: The parsed JSON response body.
        api_format: The API format identifier.

    Returns:
        List of model name strings (may be empty if no models found).

    Raises:
        KeyError: If the expected keys are missing from the response.
    """
    if api_format == "openai_compatible":
        # OpenAI / llama.cpp both use {"data": [{"id": "model-name"}, ...]}
        return [item["id"] for item in data.get("data", [])]
    # Future formats:
    # elif api_format == "anthropic":
    #     return [m["id"] for m in data.get("models", [])]
    raise NotImplementedError(f"Model parsing not implemented for API format: {api_format}")


def _do_fetch_models(base_url: str, api_format: str) -> list[str]:
    """Fetch available model names from the LLM server.

    Args:
        base_url: The API base URL (e.g. ``https://api.openai.com/v1``).
        api_format: The API format identifier (e.g. ``"openai_compatible"``).

    Returns:
        List of model name strings.

    Raises:
        urllib.error.URLError / OSError: On network errors.
        Exception: On HTTP errors or parse failures.
    """
    url = _build_models_url(base_url, api_format)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    return _parse_models_response(data, api_format)


class _FetchModelsWorker(QThread):
    """Worker thread for async model-list fetching.

    Avoids blocking the UI during network requests.
    Safe to abandon: result/error signals are disconnected on dialog close,
    so a late-arriving response won't crash.
    """

    result = Signal(list)   # list[str]
    error = Signal(str)

    def __init__(self, base_url: str, api_format: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base_url = base_url
        self._api_format = api_format

    def run(self) -> None:
        try:
            models = _do_fetch_models(self._base_url, self._api_format)
            self.result.emit(models)
        except Exception as e:
            self.error.emit(str(e))


class AnnotationPage(QWidget):
    """Clip Annotation page — annotate video clips with text-to-video prompts.

    Used as a tab inside MainWindow. Each folder of video clips is one project.
    """

    window_title_changed = Signal(str)
    status_message = Signal(str)
    undo_redo_state_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = AnnotationSettings()

        # Project state
        self._project: AnnotationProject | None = None
        self._active_clip: AnnotatedClip | None = None
        self._active_clip_index: int = -1

        # Video state
        self._tb: Timebase | None = None
        self._current_frame: int = 0
        self._subtitle_track: SubtitleTrack | None = None

        # FFmpeg paths (lazy)
        self._ffmpeg: str | None = None
        self._ffprobe: str | None = None

        # Chat view (replaces old prompt_edit + conversation system)
        self._chat_view: ChatView | None = None

        # Undo/redo stacks live on AnnotatedClip (persisted to annotations.json)
        self._max_undo_steps: int = 50

        # Playback position update throttle
        self._last_pos_ui_update: float = 0.0

        # Annotation frame selection (index in sorted annotations list)
        self._selected_annotation_index: int = -1
        self._dragged_annotation: FrameAnnotation | None = None

        # Menu actions
        self._act_undo: QAction | None = None
        self._act_redo: QAction | None = None

        # Shortcut list
        self._shortcuts: list[QShortcut] = []

        self._build_ui()
        self._connect_signals()
        self._sync_quick_toggles_from_preset()

    # ── UI construction ───────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # -- Toolbar row --
        toolbar = QHBoxLayout()
        self._btn_open_folder = QPushButton(tr("annotation.open_folder"))
        self._btn_open_folder.setEnabled(False)
        toolbar.addWidget(self._btn_open_folder)

        self._project_name_label = QLabel("")
        toolbar.addWidget(self._project_name_label)
        toolbar.addStretch()

        # ── quick-access toggles (in toolbar) ────────────────────
        self._qck_streaming = QCheckBox(tr("annotation.quick.streaming"))
        self._qck_streaming.setToolTip(tr("annotation.quick.streaming_tip"))
        self._qck_thinking = QCheckBox(tr("annotation.quick.thinking"))
        self._qck_thinking.setToolTip(tr("annotation.quick.thinking_tip"))
        self._qck_omni = QCheckBox(tr("annotation.quick.omni"))
        self._qck_omni.setToolTip(tr("annotation.quick.omni_tip"))
        toolbar.addWidget(self._qck_streaming)
        toolbar.addWidget(self._qck_thinking)
        toolbar.addWidget(self._qck_omni)
        root.addLayout(toolbar)

        # -- Main splitter --
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Left panel: project view
        left = QWidget(self._h_splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        # Vertical splitter: clip list | preset row | system prompt
        self._left_splitter = QSplitter(Qt.Orientation.Vertical, left)

        # -- Top: clip list --
        top_widget = QWidget(self._left_splitter)
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        self._clip_list = QListWidget(top_widget)
        self._clip_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._clip_list_title = QLabel(tr("annotation.clip_list"))
        top_layout.addWidget(self._clip_list_title, 0)
        top_layout.addWidget(self._clip_list, 1)

        self._left_splitter.addWidget(top_widget)

        # -- Middle: LLM preset selector row --
        mid_widget = QWidget(self._left_splitter)
        mid_layout = QHBoxLayout(mid_widget)
        mid_layout.setContentsMargins(0, 2, 0, 2)
        self._preset_label = QLabel(tr("annotation.llm_preset"))
        mid_layout.addWidget(self._preset_label)
        self._preset_combo = QComboBox(mid_widget)
        self._preset_combo.setMinimumWidth(100)
        mid_layout.addWidget(self._preset_combo, 1)
        self._btn_manage_presets = QPushButton("⚙")
        self._btn_manage_presets.setToolTip(tr("annotation.manage_presets"))
        self._btn_manage_presets.setFixedWidth(32)
        mid_layout.addWidget(self._btn_manage_presets)

        self._left_splitter.addWidget(mid_widget)

        # Lock middle widget height so QSplitter never stretches it
        # when either handle is dragged away.
        mid_fixed_h = self._preset_combo.sizeHint().height() + 4  # combo + vertical margins
        mid_widget.setMinimumHeight(mid_fixed_h)
        mid_widget.setMaximumHeight(mid_fixed_h)

        # -- Bottom: system prompt --
        bottom_widget = QWidget(self._left_splitter)
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        bottom_layout.addWidget(QLabel(tr("annotation.system_prompt")), 0)
        self._system_prompt_edit = QPlainTextEdit(bottom_widget)
        self._system_prompt_edit.setPlaceholderText(tr("annotation.system_prompt_placeholder"))
        bottom_layout.addWidget(self._system_prompt_edit, 1)

        self._left_splitter.addWidget(bottom_widget)
        self._left_splitter.setStretchFactor(0, 3)
        self._left_splitter.setStretchFactor(1, 0)
        self._left_splitter.setStretchFactor(2, 1)

        left_layout.addWidget(self._left_splitter, 1)

        self._h_splitter.addWidget(left)

        # Right panel: clip detail
        right = QWidget(self._h_splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)

        # Video preview + chat panel (horizontal splitter)
        self._player = QMediaPlayer(self)
        self._audio: QAudioOutput | None = None      # created lazily in on_tab_activated()
        self._video_sink = QVideoSink(self)
        self._player.setVideoSink(self._video_sink)

        self._video_chat_split = QSplitter(Qt.Orientation.Horizontal, right)

        # Left: video preview
        self._video_preview = VideoPreviewWidget(self._player, self._video_chat_split)
        self._video_chat_split.addWidget(self._video_preview)

        # Right: chat view
        self._chat_view = ChatView(self._video_chat_split)
        self._video_chat_split.addWidget(self._chat_view)
        self._video_chat_split.setSizes([600, 350])
        self._video_chat_split.setStretchFactor(0, 3)
        self._video_chat_split.setStretchFactor(1, 1)
        self._video_chat_split.setCollapsible(0, False)
        self._video_chat_split.setCollapsible(1, False)

        # LLM panel (splitter bottom section)
        bottom = QWidget(right)
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 4, 0, 0)

        self._llm_panel = LLMPanel(self._settings, self._preset_combo, bottom)
        bottom_layout.addWidget(self._llm_panel, 0)
        bottom_layout.addStretch(1)

        # Transport row (below timeline): [volume --right]  [< ▶ > --centered]
        transport_row = QHBoxLayout()
        transport_row.addStretch(1)
        self._btn_prev_fr = QPushButton()
        self._btn_prev_fr.setText("")
        self._btn_prev_fr.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekBackward))
        transport_row.addWidget(self._btn_prev_fr)
        self._btn_play = QPushButton()
        self._btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        transport_row.addWidget(self._btn_play)
        self._btn_next_fr = QPushButton()
        self._btn_next_fr.setText("")
        self._btn_next_fr.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekForward))
        transport_row.addWidget(self._btn_next_fr)

        # Add annotation frame button
        self._btn_add_annotation = QPushButton()
        self._btn_add_annotation.setText("+")
        self._btn_add_annotation.setEnabled(False)
        self._btn_add_annotation.setToolTip(tr("annotation.add_frame_btn_tip"))
        self._btn_add_annotation.setFixedWidth(32)
        transport_row.addWidget(self._btn_add_annotation)

        # Annotation frame nudge buttons (match slicing page style)
        self._btn_annot_nudge_left = QPushButton(tr("annotation.btn.nudge_left"))
        self._btn_annot_nudge_left.setEnabled(False)
        self._btn_annot_nudge_left.setFixedSize(50, 30)
        transport_row.addWidget(self._btn_annot_nudge_left)
        self._btn_annot_nudge_right = QPushButton(tr("annotation.btn.nudge_right"))
        self._btn_annot_nudge_right.setEnabled(False)
        self._btn_annot_nudge_right.setFixedSize(50, 30)
        transport_row.addWidget(self._btn_annot_nudge_right)

        transport_row.addStretch(1)

        self._vol_label = QLabel(tr("preview.volume"))
        transport_row.addWidget(self._vol_label)
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(100)   # default volume; synced with audio in on_tab_activated()
        self._vol_slider.setMaximumWidth(120)
        transport_row.addWidget(self._vol_slider)

        # Three-section vertical splitter: video+chat | timeline+transport | bottom
        # Handle A (video+chat ↔ timeline+transport): resize video area.
        # Handle B (timeline+transport ↔ LLM panel): resize panel area.
        # Timeline+transport group is locked at fixed height (min==max) so it
        # never stretches when either handle is dragged.
        self._main_split = QSplitter(Qt.Orientation.Vertical, right)

        # Top: video + chat (side-by-side)
        self._main_split.addWidget(self._video_chat_split)

        # Middle: timeline + transport row (grouped, fixed height)
        tt_group = QWidget(self._main_split)
        tt_layout = QVBoxLayout(tt_group)
        tt_layout.setContentsMargins(0, 0, 0, 0)
        tt_layout.setSpacing(0)
        self._timeline = TimelineWidget(tt_group)
        self._timeline.setMinimumHeight(48)
        self._timeline.setMaximumHeight(48)
        tt_layout.addWidget(self._timeline)

        # Thumbnail strip between timeline and transport
        self._thumbnail_strip = ThumbnailStrip(tt_group)
        self._thumbnail_strip.setMinimumHeight(70)
        self._thumbnail_strip.setMaximumHeight(70)
        tt_layout.addWidget(self._thumbnail_strip)

        tt_layout.addLayout(transport_row)
        TT_GROUP_HEIGHT = 152  # 48 (timeline) + 70 (thumbnails) + 34 (transport)
        tt_group.setMinimumHeight(TT_GROUP_HEIGHT)
        tt_group.setMaximumHeight(TT_GROUP_HEIGHT)
        self._main_split.addWidget(tt_group)

        # Bottom: LLM panel
        self._main_split.addWidget(bottom)

        self._main_split.setSizes([400, 152, 300])
        self._main_split.setStretchFactor(0, 3)
        self._main_split.setStretchFactor(1, 0)
        self._main_split.setStretchFactor(2, 1)
        self._main_split.setCollapsible(0, False)
        self._main_split.setCollapsible(1, False)
        self._main_split.setCollapsible(2, False)

        right_layout.addWidget(self._main_split, 1)

        self._h_splitter.addWidget(right)
        self._h_splitter.setSizes([250, 750])

        root.addWidget(self._h_splitter, 1)

        # Restore saved splitter layout (overrides default setSizes)
        self._restore_splitter_layout()

    # ── splitter layout persistence ───────────────────────────────

    def _save_splitter_layout(self) -> None:
        """Persist all splitter sizes to QSettings."""
        for name, splitter in [
            ("h_main", self._h_splitter),
            ("v_left", self._left_splitter),
            ("v_right", self._main_split),
            ("h_video_chat", self._video_chat_split),
        ]:
            self._settings.save_splitter_state(name, splitter.saveState().data())

    def _restore_splitter_layout(self) -> None:
        """Restore splitter sizes from QSettings (if previously saved)."""
        for name, splitter in [
            ("h_main", self._h_splitter),
            ("v_left", self._left_splitter),
            ("v_right", self._main_split),
            ("h_video_chat", self._video_chat_split),
        ]:
            state = self._settings.restore_splitter_state(name)
            if state is not None:
                splitter.restoreState(state)

    # ── quick-access toggles (sync with active preset) ────────────

    def _sync_quick_toggles_from_preset(self) -> None:
        """Read active preset and update the quick-toggle checkboxes (no signal loop)."""
        preset = self._settings.active_llm_preset()
        if preset is None:
            return
        for chk, value in [
            (self._qck_streaming, preset.streaming),
            (self._qck_thinking, preset.enable_thinking),
            (self._qck_omni, preset.is_omni_model),
        ]:
            chk.blockSignals(True)
            chk.setChecked(value)
            chk.blockSignals(False)

    def _on_quick_toggle_changed(self) -> None:
        """User toggled a quick-access checkbox — persist to active preset."""
        preset = self._settings.active_llm_preset()
        if preset is None:
            return
        preset.streaming = self._qck_streaming.isChecked()
        preset.enable_thinking = self._qck_thinking.isChecked()
        preset.is_omni_model = self._qck_omni.isChecked()
        self._settings.save_llm_preset(preset, set_as_default=False)
        # Keep the LLMPanel's omni checkboxes in sync
        self._llm_panel._sync_omni_checkboxes()
        # Update context handles in chat input (video/subtitles)
        self._update_context_handles()

    def _connect_signals(self) -> None:
        self._btn_open_folder.clicked.connect(self._open_project_folder)
        self._clip_list.currentRowChanged.connect(self._on_clip_selected)
        self._video_sink.videoFrameChanged.connect(self._video_preview.set_video_frame)
        self._player.positionChanged.connect(self._on_player_position)
        self._player.positionChanged.connect(self._video_preview.update_subtitle_position)
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._timeline.seek_frame.connect(self._on_timeline_seek)
        self._timeline.crosshair_hover.connect(self._on_timeline_crosshair_hover)
        self._timeline.marker_select_requested.connect(self._on_marker_selected)
        self._timeline.marker_drag_delta.connect(self._on_marker_drag_delta)
        self._timeline.marker_drag_finished.connect(self._on_marker_drag_finished)
        self._system_prompt_edit.textChanged.connect(self._on_system_prompt_changed)
        self._btn_manage_presets.clicked.connect(self._show_llm_preset_manager)

        # Chat view signals
        self._chat_view.message_sent.connect(self._on_chat_send)
        self._chat_view.regenerate_requested.connect(self._on_regenerate_requested)
        self._chat_view.branch_navigated.connect(self._on_branch_navigated)
        self._chat_view.frame_reference_clicked.connect(self._on_frame_ref_clicked)
        self._chat_view.frame_ref_insert_requested.connect(self._on_frame_ref_insert)
        self._chat_view.add_manual_annotation_requested.connect(self._on_add_manual_annotation)
        self._chat_view.annotation_edit_requested.connect(self._on_annotation_edit)
        self._chat_view.annotation_select_toggled.connect(self._on_annotation_select_toggled)
        self._chat_view.user_message_edit_requested.connect(self._on_user_message_edit)
        self._chat_view.raw_output_requested.connect(self._on_show_raw_output)

        # Thumbnail strip signals
        self._thumbnail_strip.thumbnail_clicked.connect(self._on_thumbnail_clicked)
        self._thumbnail_strip.thumbnail_double_clicked.connect(self._on_thumbnail_double_clicked)
        self._thumbnail_strip.thumbnail_delete_requested.connect(self._on_thumbnail_delete)

        # Quick-access toggles: sync preset <-> checkboxes
        self._preset_combo.currentIndexChanged.connect(
            lambda _i: (self._sync_quick_toggles_from_preset(), self._update_context_handles())
        )
        self._qck_streaming.toggled.connect(lambda _v: self._on_quick_toggle_changed())
        self._qck_thinking.toggled.connect(lambda _v: self._on_quick_toggle_changed())
        self._qck_omni.toggled.connect(lambda _v: self._on_quick_toggle_changed())

        # Transport controls
        self._btn_prev_fr.clicked.connect(lambda: self._step_frame(-1))
        self._btn_play.clicked.connect(self._toggle_play)
        self._btn_next_fr.clicked.connect(lambda: self._step_frame(1))
        self._btn_add_annotation.clicked.connect(self._add_current_frame_as_annotation)
        self._btn_annot_nudge_left.clicked.connect(lambda: self._nudge_selected_annotation(-1))
        self._btn_annot_nudge_right.clicked.connect(lambda: self._nudge_selected_annotation(1))
        self._vol_slider.valueChanged.connect(self._on_volume_changed)

        # Install app-level event filter so transport keys work regardless of focus
        _app = QApplication.instance()
        if _app is not None:
            _app.installEventFilter(self)

        # Enable the open folder button after shell calls setup
        QTimer.singleShot(0, lambda: self._btn_open_folder.setEnabled(True))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        """Intercept transport keys even when a button has focus."""
        if not self.isVisible():
            return super().eventFilter(watched, event)
        if isinstance(event, QKeyEvent) and event.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            focus = QApplication.focusWidget()
            if watched is focus or watched is self:
                if self._handle_transport_keypress(event):
                    return True
        return super().eventFilter(watched, event)

    # ── public API for shell ──────────────────────────────────────

    def setup_menus(self, menu_bar: QMenuBar) -> None:
        m_file = menu_bar.addMenu(tr("annotation.menu.file"))
        act_open = QAction(tr("annotation.open_folder"), self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self._open_project_folder)
        m_file.addAction(act_open)
        act_export = QAction(tr("annotation.export"), self)
        act_export.triggered.connect(self._export_annotations)
        m_file.addAction(act_export)

        m_edit = menu_bar.addMenu(tr("menu.edit"))
        self._act_undo = QAction(tr("menu.undo"), self)
        self._act_undo.setShortcut(QKeySequence.Undo)
        self._act_undo.triggered.connect(self._undo)
        self._act_undo.setEnabled(False)
        m_edit.addAction(self._act_undo)
        self._act_redo = QAction(tr("menu.redo"), self)
        self._act_redo.setShortcut(QKeySequence.Redo)
        self._act_redo.triggered.connect(self._redo)
        self._act_redo.setEnabled(False)
        m_edit.addAction(self._act_redo)

    def teardown_menus(self) -> None:
        self._act_undo = None
        self._act_redo = None

    def install_shortcuts(self) -> None:
        self._shortcuts.clear()

        def _bind(key, fn):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(fn)
            self._shortcuts.append(sc)

        _bind(Qt.Key.Key_Space, self._toggle_play)
        _bind(Qt.Key.Key_M, self._add_current_frame_as_annotation)
        _bind(QKeySequence.Delete, self._delete_selected_annotation)
        _bind(Qt.Key.Key_Backspace, self._delete_selected_annotation)
        _bind(Qt.Key.Key_A, lambda: self._nudge_if_no_text_focus(-1))
        _bind(Qt.Key.Key_D, lambda: self._nudge_if_no_text_focus(1))
        _bind(QKeySequence("Ctrl+Shift+F"), self._insert_frame_ref)

    def uninstall_shortcuts(self) -> None:
        for sc in self._shortcuts:
            sc.setEnabled(False)
        self._shortcuts.clear()

    def on_tab_activated(self) -> None:
        if self._audio is None:
            self._audio = QAudioOutput(self)
            self._player.setAudioOutput(self._audio)
            self._audio.setVolume(self._vol_slider.value() / 100.0)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._handle_transport_keypress(event):
            return
        super().keyPressEvent(event)

    def _handle_transport_keypress(self, event) -> bool:
        """Intercept arrow keys before Qt focus navigation grabs them."""
        if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            return False
        if QApplication.activeModalWidget() is not None:
            return False
        mods = event.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
            return False

        key = event.key()
        if key == Qt.Key.Key_Left and not self._is_text_input_focused():
            self._step_frame(-1)
            event.accept()
            return True
        if key == Qt.Key.Key_Right and not self._is_text_input_focused():
            self._step_frame(1)
            event.accept()
            return True
        if key == Qt.Key.Key_Up:
            self._vol_slider.setValue(min(self._vol_slider.value() + 5, 100))
            event.accept()
            return True
        if key == Qt.Key.Key_Down:
            self._vol_slider.setValue(max(self._vol_slider.value() - 5, 0))
            event.accept()
            return True
        # A/D nudge selected annotation frame (only when no text input focused)
        if key == Qt.Key.Key_A and not self._is_text_input_focused():
            if self._can_nudge_annotation():
                self._nudge_selected_annotation(-1)
                event.accept()
                return True
            return False
        if key == Qt.Key.Key_D and not self._is_text_input_focused():
            if self._can_nudge_annotation():
                self._nudge_selected_annotation(1)
                event.accept()
                return True
            return False
        return False

    @staticmethod
    def _is_text_input_focused() -> bool:
        """Return True if a text-editing widget currently has keyboard focus.

        This prevents letter-key shortcuts (A/D) from stealing input when the
        user is typing in a text field.
        """
        w = QApplication.focusWidget()
        if w is None:
            return False
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QTextEdit
        if isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit)):
            return True
        # QComboBox with editable=True also captures text input
        if isinstance(w, QComboBox) and w.isEditable():
            return True
        return False

    def _nudge_if_no_text_focus(self, delta: int) -> None:
        """Nudge selected annotation, but only if no text input has focus."""
        if not self._is_text_input_focused():
            self._nudge_selected_annotation(delta)

    # ── frame reference handle ────────────────────────────────────

    def _on_frame_ref_clicked(self, frame_index: int, timestamp_sec: float) -> None:
        """Seek player to the clicked frame reference."""
        if self._tb is None:
            return
        self._current_frame = frame_index
        self._player.setPosition(int(frame_index / self._tb.fps * 1000))
        # Highlight the marker on timeline
        sorted_anns = sorted(
            self._active_clip.annotations, key=lambda a: a.frame_index
        ) if self._active_clip else []
        for i, a in enumerate(sorted_anns):
            if a.frame_index == frame_index:
                self._selected_annotation_index = i
                break
        self._update_timeline_markers()

    def _on_frame_ref_insert(self, frame_index: int, timestamp_sec: float) -> None:
        """Insert [Frame N (Ts)] into the chat input field."""
        if self._chat_view is not None:
            self._chat_view.insert_frame_ref_at_cursor(frame_index, timestamp_sec)

    def _insert_frame_ref(self) -> None:
        """Insert [Frame N (Ts)] for the currently selected annotation frame."""
        if self._active_clip is None or self._selected_annotation_index < 0:
            return
        sorted_anns = sorted(self._active_clip.annotations, key=lambda a: a.frame_index)
        if self._selected_annotation_index < len(sorted_anns):
            fa = sorted_anns[self._selected_annotation_index]
            self._on_frame_ref_insert(fa.frame_index, fa.timestamp_sec)

    # ── thumbnail strip handlers ──────────────────────────────────

    def _on_thumbnail_clicked(self, frame_index: int, timestamp_sec: float) -> None:
        """Single-click on thumbnail: seek video to frame and select it."""
        if self._tb is None or self._active_clip is None:
            return
        self._current_frame = frame_index
        self._player.setPosition(int(frame_index / self._tb.fps * 1000))
        sorted_anns = sorted(self._active_clip.annotations, key=lambda a: a.frame_index)
        for i, a in enumerate(sorted_anns):
            if a.frame_index == frame_index:
                self._selected_annotation_index = i
                break
        self._update_timeline_markers()
        self._thumbnail_strip.set_selected(frame_index)

    def _on_thumbnail_double_clicked(self, frame_index: int, timestamp_sec: float) -> None:
        """Double-click on thumbnail: insert frame reference into chat input."""
        self._on_frame_ref_insert(frame_index, timestamp_sec)

    def _on_thumbnail_delete(self, frame_index: int) -> None:
        """Delete a pending (uncommitted) annotation frame from the thumbnail strip."""
        if self._active_clip is None:
            return
        # Find the annotation
        target = None
        for a in self._active_clip.annotations:
            if a.frame_index == frame_index:
                target = a
                break
        if target is None:
            return
        if target.committed:
            self.status_message.emit(
                tr("annotation.cannot_delete_committed", frame=frame_index)
            )
            return
        self._push_undo()
        self._delete_frame_image(target.image_path)
        self._active_clip.annotations.remove(target)
        self._selected_annotation_index = -1
        self._update_timeline_markers()
        self._refresh_annotation_nudge_controls()
        self._save_project()

    def on_tab_deactivated(self) -> None:
        self._save_splitter_layout()
        self._llm_panel.teardown()
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        if self._audio is not None:
            self._player.setAudioOutput(None)
            self._audio.deleteLater()
            self._audio = None

    def cleanup_on_close(self) -> None:
        self._save_project()
        self._save_splitter_layout()
        self._llm_panel.teardown()
        self._player.stop()
        _app = QApplication.instance()
        if _app is not None:
            _app.removeEventFilter(self)

    def apply_theme(self, wc: WidgetColors) -> None:
        self._timeline.update()

    def refresh_language(self) -> None:
        self._btn_open_folder.setText(tr("annotation.open_folder"))
        self._system_prompt_edit.setPlaceholderText(tr("annotation.system_prompt_placeholder"))
        self._vol_label.setText(tr("preview.volume"))
        self._btn_prev_fr.setToolTip(tr("transport.tip.prev_frame"))
        self._btn_play.setToolTip(tr("transport.tip.play_toggle"))
        self._btn_next_fr.setToolTip(tr("transport.tip.next_frame"))
        self._btn_add_annotation.setToolTip(tr("annotation.add_frame_btn_tip"))
        self._btn_annot_nudge_left.setText(tr("annotation.btn.nudge_left"))
        self._btn_annot_nudge_right.setText(tr("annotation.btn.nudge_right"))
        self._refresh_annotation_nudge_controls()
        self._preset_label.setText(tr("annotation.llm_preset"))
        self._btn_manage_presets.setToolTip(tr("annotation.manage_presets"))
        self._qck_streaming.setText(tr("annotation.quick.streaming"))
        self._qck_thinking.setText(tr("annotation.quick.thinking"))
        self._qck_omni.setText(tr("annotation.quick.omni"))
        self._llm_panel.refresh_language()
        self._chat_view.refresh_language()
        self._refresh_clip_list_labels()

    # ── undo / redo ───────────────────────────────────────────────
    # Stored per-clip in AnnotatedClip.undo_history / redo_history
    # and persisted to annotations.json on every save.

    @property
    def _undo_stack(self) -> list[dict]:
        if self._active_clip is None:
            return []
        return self._active_clip.undo_history

    @property
    def _redo_stack(self) -> list[dict]:
        if self._active_clip is None:
            return []
        return self._active_clip.redo_history

    def _push_undo(self) -> None:
        if self._active_clip is None:
            return
        clip = self._active_clip
        snap = {
            "prompt": clip.prompt,
            "state": clip.state,
            "annotations": [a.to_dict() for a in clip.annotations],
        }
        snap.update(clip._make_version_snapshot())
        clip.undo_history.append(snap)
        clip.redo_history.clear()
        if len(clip.undo_history) > self._max_undo_steps:
            clip.undo_history.pop(0)
        self._refresh_undo_state()

    def _undo(self) -> None:
        if not self._active_clip or not self._active_clip.undo_history:
            return
        clip = self._active_clip
        snap = {
            "prompt": clip.prompt,
            "state": clip.state,
            "annotations": [a.to_dict() for a in clip.annotations],
        }
        snap.update(clip._make_version_snapshot())
        clip.redo_history.append(snap)
        prev = clip.undo_history.pop()
        # Restore tree state from snapshot
        clip._restore_version_snapshot(prev)
        clip.state = prev.get("state", clip.state)
        if "annotations" in prev:
            clip.annotations = [FrameAnnotation.from_dict(a) for a in prev["annotations"]]
        self._refresh_editor_from_clip()
        self._refresh_undo_state()
        self._save_project()

    def _redo(self) -> None:
        if not self._active_clip or not self._active_clip.redo_history:
            return
        clip = self._active_clip
        snap = {
            "prompt": clip.prompt,
            "state": clip.state,
            "annotations": [a.to_dict() for a in clip.annotations],
        }
        snap.update(clip._make_version_snapshot())
        clip.undo_history.append(snap)
        nxt = clip.redo_history.pop()
        clip._restore_version_snapshot(nxt)
        clip.state = nxt.get("state", clip.state)
        if "annotations" in nxt:
            clip.annotations = [FrameAnnotation.from_dict(a) for a in nxt["annotations"]]
        self._refresh_editor_from_clip()
        self._refresh_undo_state()
        self._save_project()

    def _refresh_undo_state(self) -> None:
        if self._act_undo is not None:
            self._act_undo.setEnabled(len(self._undo_stack) > 0)
        if self._act_redo is not None:
            self._act_redo.setEnabled(len(self._redo_stack) > 0)
        self.undo_redo_state_changed.emit()

    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    # ── annotation frame management ────────────────────────────────

    def _get_annotation_markers(self) -> list[int]:
        """Return sorted list of annotation frame indices for timeline markers."""
        if self._active_clip is None:
            return []
        return sorted(a.frame_index for a in self._active_clip.annotations)

    def _get_selected_marker_frame(self) -> int | None:
        """Return the frame_index of the currently selected annotation, or None."""
        if self._active_clip is None or self._selected_annotation_index < 0:
            return None
        sorted_anns = sorted(self._active_clip.annotations, key=lambda a: a.frame_index)
        if self._selected_annotation_index >= len(sorted_anns):
            return None
        return sorted_anns[self._selected_annotation_index].frame_index

    def _update_timeline_markers(self) -> None:
        """Sync timeline markers with current annotations (without full set_state)."""
        self._sync_annotation_frames_to_chat()
        if self._tb is None:
            return
        self._timeline.set_state(
            total_frames=self._tb.total_frames,
            current_frame=self._current_frame,
            clips=[],
            view_start=0,
            view_span=self._tb.total_frames,
            fps=self._tb.fps,
            selected_clip_index=-1,
            markers=self._get_annotation_markers(),
            selected_marker_frame=self._get_selected_marker_frame(),
        )

    def _extract_frame_image(self, frame_index: int, timestamp_sec: float) -> str:
        """Extract a PNG screenshot for *frame_index* and return its relative path.
        Returns empty string if extraction fails (no project, no ffmpeg, etc.).
        """
        if self._project is None or self._active_clip is None:
            return ""
        self._ensure_ffmpeg()
        if self._ffmpeg is None:
            return ""
        video_path = self._project.resolve_clip_path(self._active_clip)
        frames_dir = Path(self._project.project_dir) / "_easyclip_frames"
        frames_dir.mkdir(exist_ok=True)
        png_bytes = extract_frame_png(str(video_path), timestamp_sec, self._ffmpeg)
        stem = video_path.stem
        png_filename = f"{stem}_frame_{frame_index:06d}.png"
        (frames_dir / png_filename).write_bytes(png_bytes)
        return f"_easyclip_frames/{png_filename}"

    def _delete_frame_image(self, image_path: str) -> None:
        """Delete the PNG file for an annotation frame, if it exists."""
        if not image_path or self._project is None:
            return
        png = Path(self._project.project_dir) / image_path
        if png.is_file():
            png.unlink()

    def _add_current_frame_as_annotation(self) -> None:
        """Add the current playback frame as a manual annotation frame (M key / + button)."""
        if self._active_clip is None or self._tb is None:
            return
        frame = self._current_frame
        # Check for duplicate
        existing = sorted(self._active_clip.annotations, key=lambda a: a.frame_index)
        if any(a.frame_index == frame for a in existing):
            self.status_message.emit(tr("annotation.duplicate_frame", frame=frame))
            return
        self._push_undo()
        ts = frame / self._tb.fps
        image_path = self._extract_frame_image(frame, ts)
        fa = FrameAnnotation(frame_index=frame, timestamp_sec=ts, image_path=image_path)
        self._active_clip.annotations.append(fa)
        self._active_clip.annotations.sort(key=lambda a: a.frame_index)
        # Select the newly added annotation (list already sorted in-place above)
        self._selected_annotation_index = next(
            (i for i, a in enumerate(self._active_clip.annotations) if a.frame_index == frame), -1
        )
        self._update_timeline_markers()
        self._refresh_annotation_nudge_controls()
        self._save_project()
        self.status_message.emit(
            tr("annotation.frame_item", frame=frame, sec=f"{ts:.1f}")
        )

    def _delete_selected_annotation(self) -> None:
        """Delete the currently selected manual annotation frame (Backspace/Delete)."""
        if self._active_clip is None:
            return
        if self._selected_annotation_index < 0:
            return
        sorted_anns = sorted(self._active_clip.annotations, key=lambda a: a.frame_index)
        idx = self._selected_annotation_index
        if idx >= len(sorted_anns):
            return
        self._push_undo()
        target = sorted_anns[idx]
        # Refuse deletion of committed (sent) frames
        if target.committed:
            self.status_message.emit(
                tr("annotation.cannot_delete_committed", frame=target.frame_index)
            )
            return
        # Delete the PNG file
        self._delete_frame_image(target.image_path)
        # Remove from clip's annotation list
        for i, a in enumerate(self._active_clip.annotations):
            if a.frame_index == target.frame_index:
                self._active_clip.annotations.pop(i)
                break
        self._selected_annotation_index = -1
        self._update_timeline_markers()
        self._refresh_annotation_nudge_controls()
        self._save_project()
        self.status_message.emit(tr("annotation.remove_frame"))

    # ── annotation nudge (A/D keys + buttons) ─────────────────────

    def _can_nudge_annotation(self) -> bool:
        """True when a manual annotation frame is selected and can be nudged."""
        if self._active_clip is None or self._tb is None:
            return False
        return self._selected_annotation_index >= 0

    def _nudge_selected_annotation(self, delta_frames: int) -> None:
        """Move the selected annotation frame by *delta_frames* (A/D keys or buttons)."""
        if not self._can_nudge_annotation():
            return
        sorted_anns = sorted(self._active_clip.annotations, key=lambda a: a.frame_index)
        idx = self._selected_annotation_index
        if idx >= len(sorted_anns):
            return
        ann = sorted_anns[idx]
        new_frame = ann.frame_index + delta_frames
        new_frame = max(0, min(self._tb.total_frames - 1, new_frame))
        if new_frame == ann.frame_index:
            return
        # Check for collision with another annotation
        for other in self._active_clip.annotations:
            if other is not ann and other.frame_index == new_frame:
                self.status_message.emit(tr("annotation.duplicate_frame", frame=new_frame))
                return
        self._push_undo()
        # Delete old PNG and re-extract at new position
        self._delete_frame_image(ann.image_path)
        ann.frame_index = new_frame
        ann.timestamp_sec = new_frame / self._tb.fps
        ann.image_path = self._extract_frame_image(new_frame, ann.timestamp_sec)
        self._active_clip.annotations.sort(key=lambda a: a.frame_index)
        # Update selection index (list already sorted in-place above)
        for i, a in enumerate(self._active_clip.annotations):
            if a is ann:
                self._selected_annotation_index = i
                break
        self._update_timeline_markers()
        self._refresh_annotation_nudge_controls()
        self._save_project()

    def _refresh_annotation_nudge_controls(self) -> None:
        """Enable/disable nudge buttons and update tooltips."""
        ok = self._can_nudge_annotation()
        self._btn_annot_nudge_left.setEnabled(ok)
        self._btn_annot_nudge_right.setEnabled(ok)
        if ok:
            self._btn_annot_nudge_left.setToolTip(tr("annotation.tip.nudge_left"))
            self._btn_annot_nudge_right.setToolTip(tr("annotation.tip.nudge_right"))
        else:
            tip = tr("annotation.tip.nudge_disabled")
            self._btn_annot_nudge_left.setToolTip(tip)
            self._btn_annot_nudge_right.setToolTip(tip)

    # ── marker interaction on timeline ─────────────────────────────

    def _on_marker_selected(self, frame_index: int) -> None:
        """Right-click on a timeline marker → select that annotation."""
        if self._active_clip is None:
            return
        sorted_anns = sorted(self._active_clip.annotations, key=lambda a: a.frame_index)
        for i, a in enumerate(sorted_anns):
            if a.frame_index == frame_index:
                self._selected_annotation_index = i
                self._dragged_annotation = a
                # Save pre-drag snapshot for undo (pushed on drag finish if moved)
                self._drag_start_snapshot = {
                    "prompt": self._active_clip.prompt,
                    "state": self._active_clip.state,
                    "annotations": [ann.to_dict() for ann in self._active_clip.annotations],
                }
                self._drag_start_frame = frame_index
                self._update_timeline_markers()
                self._refresh_annotation_nudge_controls()
                return

    def _on_marker_drag_delta(self, orig_frame: int, new_frame: int) -> None:
        """Right-drag on a timeline marker → lightweight position update.

        Constrains the dragged frame so it cannot cross its neighboring
        annotation frames.  Neighbours are determined once by the dragged
        annotation's *index* in the sorted list — they do not change during
        the drag, so the dragged frame is blocked rather than pushing others.
        """
        if self._dragged_annotation is None or self._active_clip is None or self._tb is None:
            return
        new_frame = max(0, min(self._tb.total_frames - 1, new_frame))
        if new_frame == self._dragged_annotation.frame_index:
            return

        # Find neighbours by position in the sorted list (not by frame_index).
        sorted_anns = sorted(self._active_clip.annotations, key=lambda a: a.frame_index)
        drag_idx = next(
            i for i, a in enumerate(sorted_anns) if a is self._dragged_annotation
        )
        left_bound = sorted_anns[drag_idx - 1].frame_index + 1 if drag_idx > 0 else 0
        right_bound = (
            sorted_anns[drag_idx + 1].frame_index - 1
            if drag_idx < len(sorted_anns) - 1
            else self._tb.total_frames - 1
        )
        new_frame = max(left_bound, min(right_bound, new_frame))
        if new_frame == self._dragged_annotation.frame_index:
            return

        self._dragged_annotation.frame_index = new_frame
        self._dragged_annotation.timestamp_sec = new_frame / self._tb.fps
        # Re-sort in-place, then find the annotation's new position
        self._active_clip.annotations.sort(key=lambda a: a.frame_index)
        for i, a in enumerate(self._active_clip.annotations):
            if a is self._dragged_annotation:
                self._selected_annotation_index = i
                break

    def _on_marker_drag_finished(self) -> None:
        """Right-drag finished on a timeline marker — full UI refresh."""
        dragged = self._dragged_annotation
        self._dragged_annotation = None
        if self._active_clip is not None:
            # Push undo if the frame actually moved
            start_snap = getattr(self, '_drag_start_snapshot', None)
            start_frame = getattr(self, '_drag_start_frame', -1)
            moved = start_snap is not None and start_frame != self._get_selected_marker_frame()
            if moved:
                clip = self._active_clip
                clip.undo_history.append(start_snap)
                clip.redo_history.clear()
                if len(clip.undo_history) > self._max_undo_steps:
                    clip.undo_history.pop(0)
                self._refresh_undo_state()
                # Re-extract PNG at new position for the dragged frame
                if dragged is not None and self._tb is not None:
                    old_path = start_snap.get("annotations", [])
                    for a_dict in old_path:
                        if a_dict.get("frame_index") == start_frame:
                            self._delete_frame_image(a_dict.get("image_path", ""))
                            break
                    dragged.image_path = self._extract_frame_image(
                        dragged.frame_index, dragged.timestamp_sec
                    )
            self._drag_start_snapshot = None
            self._drag_start_frame = -1
            # Refresh thumbnails: the frame_index changed so old cache
            # entries are stale; set_annotations rebuilds the strip and
            # triggers re-extraction for new frame positions.
            if self._selected_annotation_index >= 0:
                sf = self._get_selected_marker_frame()
            self._update_timeline_markers()
            self._save_project()

    # ── project management ────────────────────────────────────────

    def _open_project_folder(self) -> None:
        start = self._settings.last_open_dir() or self._settings.last_open_project_dir() or ""
        folder = QFileDialog.getExistingDirectory(self, tr("annotation.open_folder"), start)
        if not folder:
            return
        self._settings.set_last_open_dir(folder)
        self._settings.set_last_open_project_dir(folder)
        self._open_project_folder_at(Path(folder))

    def _open_project_folder_at(self, folder: Path) -> None:
        """Load / create an annotation project at *folder*.
        After loading, try to re-select the last selected clip.
        """
        proj = AnnotationProject.load(str(folder))
        if proj is None:
            try:
                proj = AnnotationProject.create(str(folder))
            except Exception as e:
                QMessageBox.warning(self, tr("annotation.error"), str(e))
                return

        if not proj.system_prompt:
            default_sp = self._settings.default_system_prompt()
            if default_sp:
                proj.system_prompt = default_sp

        self._project = proj
        self._project_name_label.setText(proj.project_dir)
        self._system_prompt_edit.setPlainText(proj.system_prompt or "")

        self._refresh_clip_list()

        # Try to re-select the last selected clip
        last_clip = self._settings.last_selected_clip_path()
        row_to_select = -1
        if last_clip:
            for i, c in enumerate(proj.clips):
                if c.clip_path == last_clip:
                    # Verify the file still exists
                    if proj.resolve_clip_path(c).is_file():
                        row_to_select = i
                    else:
                        QMessageBox.warning(
                            self,
                            tr("annotation.missing_clip_title"),
                            tr("annotation.missing_clip_body", path=c.clip_path),
                        )
                        self._settings.set_last_selected_clip_path("")
                    break

        if row_to_select >= 0:
            self._clip_list.setCurrentRow(row_to_select)
        else:
            self._active_clip = None
            self._active_clip_index = -1
            self._clear_clip_detail()

        self.status_message.emit(tr("annotation.project_loaded", name=proj.project_name))

    def _save_project(self) -> None:
        if self._project is None:
            return
        # Sync current clip state
        if self._active_clip is not None:
            self._write_clip_prompt_txt(self._active_clip)
        # Also write .txt for every clip that has a prompt (batch sync)
        for clip in self._project.clips:
            if clip is not self._active_clip and clip.prompt.strip():
                self._write_clip_prompt_txt(clip)
        self._cleanup_orphaned_frames()
        self._project.save()
        self.status_message.emit(tr("annotation.saved"))

    def _cleanup_orphaned_frames(self) -> None:
        """Remove frame PNGs in _easyclip_frames/ not referenced by any annotation."""
        if self._project is None:
            return
        frames_dir = Path(self._project.project_dir) / "_easyclip_frames"
        if not frames_dir.is_dir():
            return
        # Collect all referenced image paths
        referenced: set[str] = set()
        for clip in self._project.clips:
            for fa in clip.annotations:
                if fa.image_path:
                    referenced.add(fa.image_path)
        # Remove unreferenced PNGs
        for png in list(frames_dir.iterdir()):
            rel = f"_easyclip_frames/{png.name}"
            if rel not in referenced and png.is_file():
                png.unlink()

    def _export_annotations(self) -> None:
        self._save_project()
        if self._project is None:
            return
        # Export as a simple JSON alongside annotations.json
        out = Path(self._project.project_dir) / "annotations_export.json"
        import json
        out.write_text(json.dumps(self._project.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.status_message.emit(tr("annotation.exported", path=str(out)))

    # ── clip list ─────────────────────────────────────────────────

    def _refresh_clip_list(self) -> None:
        self._clip_list.clear()
        if self._project is None:
            self._clip_list_title.setText(tr("annotation.clip_list"))
            return
        count = len(self._project.clips)
        self._clip_list_title.setText(f"{tr('annotation.clip_list')} ({count})")
        for clip in self._project.clips:
            item = QListWidgetItem(clip.clip_path)
            item.setData(Qt.ItemDataRole.UserRole, clip.clip_path)
            self._clip_list.addItem(item)

    def _refresh_clip_list_labels(self) -> None:
        pass  # labels are file paths, not translatable

    def _on_clip_selected(self, row: int) -> None:
        if row < 0 or self._project is None:
            return
        self._save_clip_state()
        clip = self._project.clips[row]
        self._active_clip = clip
        self._active_clip_index = row
        self._load_clip_preview(clip)
        self._refresh_editor_from_clip()
        self._refresh_undo_state()
        self._settings.set_last_selected_clip_path(clip.clip_path)
        self.window_title_changed.emit(
            f"EasyClip - {self._project.project_name} / {clip.clip_path}"
        )

    def _save_clip_state(self) -> None:
        if self._active_clip is not None:
            self._write_clip_prompt_txt(self._active_clip)

    def _clip_prompt_txt_path(self, clip: AnnotatedClip) -> Path:
        """Return the .txt file path alongside the clip's video file."""
        if self._project is None:
            return Path(clip.clip_path).with_suffix(".txt")
        video = self._project.resolve_clip_path(clip)
        return video.with_suffix(".txt")

    def _write_clip_prompt_txt(self, clip: AnnotatedClip) -> None:
        """Write the clip's prompt to a .txt file next to the video."""
        if clip.prompt.strip():
            self._clip_prompt_txt_path(clip).write_text(clip.prompt, encoding="utf-8")

    def _read_clip_prompt_txt(self, clip: AnnotatedClip) -> str | None:
        """Read an existing .txt file alongside the video, if present."""
        p = self._clip_prompt_txt_path(clip)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
        return None

    def _check_external_txt_modification(self, clip: AnnotatedClip) -> None:
        """Detect if .txt was modified externally (content comparison).

        - If clip.prompt is empty and .txt exists → first-time import from .txt.
        - If both have content and differ → external modification dialog.
        """
        txt_content = self._read_clip_prompt_txt(clip)
        json_prompt = clip.prompt.strip()

        if txt_content is None:
            return  # no .txt, nothing to check

        if not json_prompt and txt_content:
            # First-time import: .txt exists but annotations.json has no prompt
            clip.prompt = txt_content
            return

        if json_prompt and txt_content and txt_content != json_prompt:
            self._show_external_modification_dialog(clip, txt_content)

    def _show_external_modification_dialog(
        self, clip: AnnotatedClip, txt_content: str
    ) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(tr("annotation.external_change_title"))
        box.setText(tr("annotation.external_change_body", filename=self._clip_prompt_txt_path(clip).name))
        box.setInformativeText(tr("annotation.external_change_info"))
        btn_load = box.addButton(tr("annotation.external_change_load"), QMessageBox.ButtonRole.AcceptRole)
        btn_keep = box.addButton(tr("annotation.external_change_keep"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_load)
        box.exec()

        if box.clickedButton() == btn_load:
            self._push_undo()
            clip.prompt = txt_content
            self._refresh_editor_from_clip()
            self._save_project()
            self.status_message.emit(tr("annotation.external_loaded"))
        else:
            # Keep software version, overwrite .txt
            self._write_clip_prompt_txt(clip)

    # ── clip preview / load ──────────────────────────────────────────

    def _load_clip_preview(self, clip: AnnotatedClip) -> None:
        if self._project is None:
            return
        video_path = self._project.resolve_clip_path(clip)
        if not video_path.is_file():
            self.status_message.emit(tr("annotation.video_not_found", path=str(video_path)))
            return

        self._ensure_ffmpeg()
        try:
            probe = probe_video(str(video_path), self._ffprobe)
        except Exception as e:
            self.status_message.emit(str(e))
            return
        self._tb = Timebase.from_probe(probe)
        self._current_frame = 0
        self._selected_annotation_index = -1
        self._dragged_annotation = None

        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(video_path.resolve())))
        self._player.pause()

        self._timeline.set_state(
            total_frames=self._tb.total_frames,
            current_frame=0,
            clips=[],
            view_start=0,
            view_span=self._tb.total_frames,
            fps=self._tb.fps,
            selected_clip_index=-1,
            markers=self._get_annotation_markers(),
            selected_marker_frame=None,
        )

        # Set video source for thumbnail extraction in frame selector

        self._btn_add_annotation.setEnabled(True)

        # Detect external .txt modifications (like VS Code's "file changed on disk").
        self._check_external_txt_modification(clip)

        # Rebuild annotations from the conversation tree path
        clip.rebuild_annotations_from_path()

        # Auto-load subtitle
        self._subtitle_track = None
        match = find_matching_subtitle(video_path)
        if match is not None:
            try:
                self._subtitle_track = parse_subtitle_file(match)
            except Exception:
                pass
        self._video_preview.set_subtitle_track(self._subtitle_track)

    def _clear_clip_detail(self) -> None:
        self._tb = None
        self._current_frame = 0
        self._subtitle_track = None
        self._selected_annotation_index = -1
        self._dragged_annotation = None
        self._refresh_annotation_nudge_controls()
        self._player.stop()
        self._timeline.set_state(
            total_frames=0, current_frame=0, clips=[],
            view_start=0, view_span=1, fps=30.0, selected_clip_index=-1,
            markers=[], selected_marker_frame=None,
        )
        self._video_preview.set_subtitle_track(None)
        self._btn_add_annotation.setEnabled(False)
        self._chat_view.clear()
        self._chat_view.set_input_enabled(False)
        self._thumbnail_strip.clear()

    def _ensure_ffmpeg(self) -> None:
        if self._ffmpeg is None:
            self._ffmpeg, self._ffprobe = find_ffmpeg()

    # ── editor refresh ────────────────────────────────────────────

    def _refresh_editor_from_clip(self) -> None:
        if self._active_clip is None:
            return
        self._selected_annotation_index = -1
        self._refresh_annotation_nudge_controls()
        self._chat_view.load_clip(
            self._active_clip,
            self._project.system_prompt if self._project else "",
        )
        self._sync_annotation_frames_to_chat()
        self._update_context_handles()
        self._chat_view.set_input_enabled(True)

    def _update_context_handles(self) -> None:
        """Set chat input context handles based on current mode and subtitle state."""
        handles: list[str] = []
        preset = self._llm_panel.active_preset()
        if preset is not None and preset.is_omni_model:
            handles.append("[Video Clip]")
        if self._subtitle_track is not None and self._subtitle_track.entries:
            handles.append("[Subtitles]")
        if self._chat_view is not None:
            self._chat_view.set_context_handles(handles)

    def _sync_annotation_frames_to_chat(self) -> None:
        """Push current annotation frame list to the chat view frame picker and thumbnail strip."""
        if self._chat_view is None or self._active_clip is None:
            return
        frames = [
            {"frame_index": fa.frame_index, "timestamp_sec": fa.timestamp_sec}
            for fa in sorted(self._active_clip.annotations, key=lambda a: a.frame_index)
        ]
        self._chat_view.set_annotation_frames(frames)
        # Also refresh the thumbnail strip
        sel = self._get_selected_marker_frame()
        proj_dir = self._project.project_dir if self._project else ""
        self._thumbnail_strip.set_annotations(
            sorted(self._active_clip.annotations, key=lambda a: a.frame_index),
            proj_dir,
            sel,
        )

    # ── startup restore ────────────────────────────────────────────

    def maybe_restore_last_session(self) -> None:
        """Called by the shell on app startup.  Checks startup behavior
        and restores the last project folder / last selected clip if
        configured to do so.
        """
        last_dir = self._settings.last_open_project_dir().strip()
        if not last_dir:
            return
        folder = Path(last_dir).resolve()
        if not folder.is_dir():
            self._settings.set_last_open_project_dir("")
            self._settings.set_last_selected_clip_path("")
            return

        behavior = self._settings.startup_behavior()
        if behavior == "do_nothing":
            return

        should_load = behavior == "auto_load"
        if behavior == "ask":
            should_load, remember_choice = self._ask_load_last_project_at_startup(folder)
            if remember_choice:
                next_behavior = "auto_load" if should_load else "do_nothing"
                self._settings.set_startup_behavior(next_behavior)
        if not should_load:
            return

        try:
            self._open_project_folder_at(folder)
        except Exception as e:
            QMessageBox.warning(
                self,
                tr("startup.restore.title"),
                tr("startup.restore.failed", detail=str(e)),
            )

    def _ask_load_last_project_at_startup(self, folder: Path) -> tuple[bool, bool]:
        box = QMessageBox(self)
        box.setWindowTitle(tr("startup.restore.title"))
        box.setText(tr("annotation.startup.restore_body", name=folder.name))
        cb = QCheckBox(tr("startup.restore.remember"), box)
        box.setCheckBox(cb)
        btn_load = box.addButton(tr("startup.restore.load"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("startup.restore.skip"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_load)
        box.exec()
        return box.clickedButton() == btn_load, cb.isChecked()

    # ── LLM preset form (shared by preferences tab and gear-button dialog) ─

    def _build_llm_preset_form(self, parent: QWidget) -> dict:
        """Build the LLM preset management form into *parent*.

        Returns a dict with keys:
          - ``widget`` : the container QWidget
          - ``accept`` : callable () -> bool (True = can proceed / close)
          - ``refresh_combo`` : callable () to reload the preset combo after
            external changes (e.g. presets saved from another dialog).
        """
        import uuid
        from easyclip.annotation.settings import LLMPreset as _LLMPreset

        container = QWidget(parent)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── preset selector row ──────────────────────────────────
        row_preset = QWidget(container)
        row_preset_lay = QHBoxLayout(row_preset)
        row_preset_lay.setContentsMargins(0, 0, 0, 0)
        row_preset_lay.setSpacing(8)
        cb_preset = QComboBox(row_preset)
        edit_preset_name = QLineEdit(row_preset)
        edit_preset_name.setPlaceholderText(tr("settings.llm_preset.name_placeholder"))
        row_preset_lay.addWidget(cb_preset, stretch=2)
        row_preset_lay.addWidget(edit_preset_name, stretch=3)
        layout.addWidget(row_preset)

        # ── form fields ──────────────────────────────────────────
        form = QFormLayout()
        edit_base_url = QLineEdit(container)
        form.addRow(tr("annotation.preset_url"), edit_base_url)
        edit_api_key = QLineEdit(container)
        edit_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr("annotation.preset_key"), edit_api_key)

        # API format — currently only OpenAI, but extensible
        combo_format = QComboBox(container)
        combo_format.addItem(tr("annotation.preset_format_openai"), "openai_compatible")
        form.addRow(tr("annotation.preset_format"), combo_format)

        # Model: editable combo + fetch button
        row_model = QWidget(container)
        row_model_lay = QHBoxLayout(row_model)
        row_model_lay.setContentsMargins(0, 0, 0, 0)
        row_model_lay.setSpacing(6)
        combo_model = QComboBox(row_model)
        combo_model.setEditable(True)
        combo_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        row_model_lay.addWidget(combo_model, stretch=1)
        btn_fetch = QPushButton(tr("annotation.preset_fetch_models"), row_model)
        btn_fetch.setEnabled(False)
        btn_fetch.setToolTip(tr("annotation.preset_fetch_models_tip"))
        row_model_lay.addWidget(btn_fetch)
        form.addRow(tr("annotation.preset_model"), row_model)
        layout.addLayout(form)

        # Streaming toggle
        chk_streaming = QCheckBox(tr("annotation.preset_streaming"), container)
        chk_streaming.setChecked(True)
        layout.addWidget(chk_streaming)

        # ── Omni model toggle ─────────────────────────────────────
        chk_is_omni = QCheckBox(tr("annotation.preset_is_omni"), container)
        layout.addWidget(chk_is_omni)

        # ── Omni media format selector (hidden when not omni) ─────
        row_omni_fmt = QWidget(container)
        row_omni_fmt_lay = QHBoxLayout(row_omni_fmt)
        row_omni_fmt_lay.setContentsMargins(20, 0, 0, 0)  # indent to show hierarchy
        row_omni_fmt_lay.setSpacing(8)
        lbl_omni_fmt = QLabel(tr("annotation.preset_omni_media_format"), row_omni_fmt)
        combo_omni_media_format = QComboBox(row_omni_fmt)
        combo_omni_media_format.addItem(
            tr("annotation.preset_omni_media_qwen"), OMNI_MEDIA_FORMAT_QWEN,
        )
        # Future: add more formats here, e.g.:
        # combo_omni_media_format.addItem("Gemini Multimodal", "gemini_multimodal")
        row_omni_fmt_lay.addWidget(lbl_omni_fmt)
        row_omni_fmt_lay.addWidget(combo_omni_media_format, stretch=1)
        layout.addWidget(row_omni_fmt)

        def _sync_omni_ui() -> None:
            """Show/hide omni media format row based on checkbox state."""
            row_omni_fmt.setVisible(chk_is_omni.isChecked())

        chk_is_omni.toggled.connect(lambda _checked: _sync_omni_ui())
        _sync_omni_ui()  # initial state

        # ── thinking mode ────────────────────────────────────────
        chk_thinking = QCheckBox(tr("annotation.preset_thinking"), container)
        chk_thinking.setChecked(True)
        layout.addWidget(chk_thinking)

        # ── save row ─────────────────────────────────────────────
        row_save = QWidget(container)
        row_save_lay = QHBoxLayout(row_save)
        row_save_lay.setContentsMargins(0, 0, 0, 0)
        row_save_lay.setSpacing(8)
        btn_save = QPushButton(tr("settings.llm_preset.save"), row_save)
        lb_saved = QLabel("", row_save)
        row_save_lay.addWidget(btn_save)
        row_save_lay.addWidget(lb_saved, stretch=1)
        layout.addWidget(row_save)
        layout.addStretch()

        # ── state ────────────────────────────────────────────────
        presets: list[_LLMPreset] = self._settings.llm_presets()
        active_id: str = self._settings.active_llm_preset_id()
        preset_new_tag = "__new__"
        sync_ui = [False]

        # ── helpers ──────────────────────────────────────────────
        def _current_form_state() -> dict:
            return {
                "name": edit_preset_name.text().strip(),
                "base_url": edit_base_url.text().strip(),
                "api_key": edit_api_key.text(),
                "api_format": str(combo_format.currentData() or "openai_compatible"),
                "model": combo_model.currentText().strip(),
                "streaming": chk_streaming.isChecked(),
                "enable_thinking": chk_thinking.isChecked(),
                "cached_models": [combo_model.itemText(i)
                                  for i in range(combo_model.count())],
                "is_omni_model": chk_is_omni.isChecked(),
                "omni_media_format": str(combo_omni_media_format.currentData()
                                         or OMNI_MEDIA_FORMAT_QWEN),
            }

        def _preset_to_form_state(p: _LLMPreset) -> dict:
            return {"name": p.name, "base_url": p.base_url,
                    "api_key": p.api_key, "api_format": p.api_format,
                    "model": p.model, "streaming": p.streaming,
                    "enable_thinking": p.enable_thinking,
                    "cached_models": p.cached_models,
                    "is_omni_model": p.is_omni_model,
                    "omni_media_format": p.omni_media_format}

        def _new_template_state() -> dict:
            return {"name": "", "base_url": "", "api_key": "",
                    "api_format": "openai_compatible", "model": "",
                    "streaming": True, "enable_thinking": True,
                    "cached_models": [],
                    "is_omni_model": False,
                    "omni_media_format": OMNI_MEDIA_FORMAT_QWEN}

        def _next_default_name() -> str:
            base = "LLM Preset"
            existing = {p.name for p in presets}
            if base not in existing:
                return base
            n = 2
            while f"{base} {n}" in existing:
                n += 1
            return f"{base} {n}"

        def _apply_to_form(p: _LLMPreset, *, is_new: bool) -> None:
            sync_ui[0] = True
            try:
                edit_preset_name.setText(p.name)
                edit_base_url.setText(p.base_url)
                edit_api_key.setText(p.api_key)
                # API format combo
                fmt_idx = combo_format.findData(p.api_format)
                if fmt_idx < 0:
                    fmt_idx = 0
                combo_format.setCurrentIndex(fmt_idx)
                # Model combo: populate from cached models, then set text
                combo_model.clear()
                if p.cached_models:
                    combo_model.addItems(p.cached_models)
                combo_model.setCurrentText(p.model)
                # Streaming checkbox
                chk_streaming.setChecked(p.streaming)
                # Thinking checkbox
                chk_thinking.setChecked(getattr(p, "enable_thinking", True))
                # Omni model toggle + media format
                chk_is_omni.setChecked(getattr(p, "is_omni_model", False))
                omni_fmt = getattr(p, "omni_media_format", OMNI_MEDIA_FORMAT_QWEN)
                omni_idx = combo_omni_media_format.findData(omni_fmt)
                if omni_idx >= 0:
                    combo_omni_media_format.setCurrentIndex(omni_idx)
                _sync_omni_ui()
            finally:
                sync_ui[0] = False
            if is_new and not edit_preset_name.text().strip():
                edit_preset_name.setText(_next_default_name())
            _update_fetch_button()

        def _reload_combo(select_id: str | None = None) -> None:
            cb_preset.blockSignals(True)
            cb_preset.clear()
            cb_preset.addItem(tr("settings.llm_preset.new"), preset_new_tag)
            for p in presets:
                cb_preset.addItem(p.name, p.id)
            target = select_id or active_id
            idx = -1
            if target:
                idx = cb_preset.findData(target)
            if idx < 0:
                idx = 1 if cb_preset.count() > 1 else 0
            cb_preset.setCurrentIndex(idx)
            cb_preset.blockSignals(False)
            _on_preset_changed()

        def _on_preset_changed() -> None:
            if sync_ui[0]:
                return
            selected = str(cb_preset.currentData() or preset_new_tag)
            if selected == preset_new_tag:
                tmpl = self._settings.new_llm_preset_template()
                _apply_to_form(tmpl, is_new=True)
                return
            for p in presets:
                if p.id == selected:
                    _apply_to_form(p, is_new=False)
                    break

        def _is_dirty() -> bool:
            selected = str(cb_preset.currentData() or preset_new_tag)
            current = _current_form_state()
            if selected == preset_new_tag:
                return current != _new_template_state()
            for p in presets:
                if p.id == selected:
                    return current != _preset_to_form_state(p)
            return current != _new_template_state()

        def _save_current() -> bool:
            nonlocal presets, active_id
            name = edit_preset_name.text().strip()
            if not name:
                QMessageBox.warning(
                    container,
                    tr("settings.llm_preset.name_required.title"),
                    tr("settings.llm_preset.name_required.body"),
                )
                edit_preset_name.setFocus()
                return False
            selected = str(cb_preset.currentData() or preset_new_tag)
            current_id = selected if selected and selected != preset_new_tag else None
            # Duplicate name check
            same_name_ids = [
                p.id for p in presets
                if p.name.strip() == name and p.id != (current_id or "")
            ]
            target_id = current_id
            if same_name_ids:
                ans = QMessageBox.question(
                    container,
                    tr("settings.llm_preset.duplicate.title"),
                    tr("settings.llm_preset.duplicate.body", name=name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ans != QMessageBox.StandardButton.Yes:
                    edit_preset_name.setFocus()
                    return False
                target_id = same_name_ids[0]
            st = _current_form_state()
            preset = _LLMPreset(
                id=target_id or str(uuid.uuid4()),
                name=st["name"],
                base_url=st["base_url"],
                api_key=st["api_key"],
                api_format=st["api_format"],
                model=st["model"],
                streaming=st.get("streaming", True),
                enable_thinking=st.get("enable_thinking", True),
                cached_models=st.get("cached_models", []),
                is_omni_model=st.get("is_omni_model", False),
                omni_media_format=st.get("omni_media_format", OMNI_MEDIA_FORMAT_QWEN),
            )
            saved_id = self._settings.save_llm_preset(preset, set_as_default=True)
            presets = self._settings.llm_presets()
            active_id = saved_id
            lb_saved.setText(tr("settings.llm_preset.saved"))
            _reload_combo(saved_id)
            return True

        def _accept() -> bool:
            if _is_dirty():
                ans = QMessageBox.question(
                    container,
                    tr("settings.llm_preset.unsaved.title"),
                    tr("settings.llm_preset.unsaved.body"),
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                if ans == QMessageBox.StandardButton.Cancel:
                    return False
                if ans == QMessageBox.StandardButton.Yes and not _save_current():
                    return False
            _teardown()
            return True

        def _teardown() -> None:
            """Clean up background work; safe to call multiple times."""
            _form_closed[0] = True
            if _fetch_success_timer is not None:
                _fetch_success_timer.stop()
            if _fetch_worker is not None and _fetch_worker.isRunning():
                # Disconnect signals so late-arriving results don't crash
                try:
                    _fetch_worker.result.disconnect()
                    _fetch_worker.error.disconnect()
                except (RuntimeError, TypeError):
                    pass  # Already disconnected or no connections
                _fetch_worker.requestInterruption()
                _fetch_worker.wait(3000)

        # ── fetch models helpers ──────────────────────────────
        _fetch_worker: _FetchModelsWorker | None = None
        _fetch_success_timer: QTimer | None = None
        _fetch_state: str = "idle"   # "idle" | "loading"
        _form_closed: list[bool] = [False]  # mutable flag for dialog-close guard

        _FETCH_BTN_IDLE = tr("annotation.preset_fetch_models")
        _FETCH_BTN_LOADING = tr("annotation.preset_fetching")
        _FETCH_BTN_SUCCESS = "✓"

        def _can_fetch() -> bool:
            return (
                _fetch_state == "idle"
                and bool(edit_base_url.text().strip())
                and bool(combo_format.currentData())
            )

        def _update_fetch_button() -> None:
            btn_fetch.setEnabled(_can_fetch())

        def _set_fetch_loading() -> None:
            nonlocal _fetch_state
            _fetch_state = "loading"
            btn_fetch.setText(_FETCH_BTN_LOADING)
            btn_fetch.setEnabled(False)

        def _set_fetch_idle() -> None:
            nonlocal _fetch_state
            _fetch_state = "idle"
            btn_fetch.setText(_FETCH_BTN_IDLE)
            _update_fetch_button()

        def _set_fetch_success() -> None:
            nonlocal _fetch_state, _fetch_success_timer
            _fetch_state = "idle"
            btn_fetch.setText(_FETCH_BTN_SUCCESS)
            btn_fetch.setEnabled(False)
            btn_fetch.setStyleSheet("color: #27ae60; font-weight: bold;")
            # Revert after 1.5 seconds
            if _fetch_success_timer is not None:
                _fetch_success_timer.stop()
            t = QTimer(btn_fetch)
            t.setSingleShot(True)
            t.timeout.connect(_revert_fetch_success)
            t.start(1500)
            _fetch_success_timer = t

        def _revert_fetch_success() -> None:
            btn_fetch.setStyleSheet("")
            btn_fetch.setText(_FETCH_BTN_IDLE)
            _update_fetch_button()

        def _fetch_models() -> None:
            if not _can_fetch():
                return
            _set_fetch_loading()
            base = edit_base_url.text().strip().rstrip("/")
            fmt = str(combo_format.currentData() or "openai_compatible")

            worker = _FetchModelsWorker(base, fmt, container)
            nonlocal _fetch_worker
            _fetch_worker = worker

            def _on_result(models: list[str]) -> None:
                if _form_closed[0]:
                    return
                _populate_model_combo(models)
                # Persist model list to the current preset so it survives
                # across dialog reopens, but only if editing an existing preset
                selected = str(cb_preset.currentData() or preset_new_tag)
                if selected and selected != preset_new_tag:
                    for p in presets:
                        if p.id == selected:
                            p.cached_models = list(models)
                            self._settings.save_llm_preset(p, set_as_default=False)
                            break
                _set_fetch_success()

            def _on_error(error_msg: str) -> None:
                if _form_closed[0]:
                    return
                _set_fetch_idle()
                _show_fetch_error(error_msg)

            worker.result.connect(_on_result)
            worker.error.connect(_on_error)
            worker.start()

        def _populate_model_combo(models: list[str]) -> None:
            current_text = combo_model.currentText().strip()
            combo_model.clear()
            combo_model.setEnabled(True)
            for m in models:
                combo_model.addItem(m)
            if current_text:
                combo_model.setCurrentText(current_text)
            # Auto-open dropdown for quick selection
            combo_model.showPopup()

        def _show_fetch_error(error_msg: str) -> None:
            box = QMessageBox(container)
            box.setWindowTitle(tr("annotation.preset_fetch_error_title"))
            box.setText(tr("annotation.preset_fetch_error_body", error=error_msg))
            box.setIcon(QMessageBox.Icon.Warning)
            # Make the error text selectable/copyable
            box.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            box.exec()

        # ── connections ──────────────────────────────────────────
        cb_preset.currentIndexChanged.connect(lambda _i: _on_preset_changed())
        btn_save.clicked.connect(lambda: _save_current())
        edit_base_url.textChanged.connect(lambda _t: _update_fetch_button())
        combo_format.currentIndexChanged.connect(lambda _i: _update_fetch_button())
        btn_fetch.clicked.connect(lambda: _fetch_models())

        _reload_combo(active_id)

        return {
            "widget": container,
            "accept": _accept,
            "teardown": _teardown,
            "refresh_combo": lambda: _reload_combo(active_id),
        }

    def _show_llm_preset_manager(self) -> None:
        """Open the LLM preset management as a standalone dialog (gear button)."""
        self._player.pause()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("settings.tab.llm_presets"))
        dlg.resize(500, 350)
        lay = QVBoxLayout(dlg)

        form_info = self._build_llm_preset_form(dlg)
        lay.addWidget(form_info["widget"])

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(lambda: form_info["accept"]() and dlg.accept())
        buttons.rejected.connect(lambda: (form_info["teardown"](), dlg.reject()))
        lay.addWidget(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Refresh the left-panel combo to reflect changes
            self._llm_panel._refresh_presets()

    # ── settings dialog ────────────────────────────────────────────

    def show_settings_dialog(self) -> None:
        """Show the annotation preferences dialog."""
        self._player.pause()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("settings.title"))
        lay = QVBoxLayout(dlg)
        tabs = QTabWidget(dlg)
        lay.addWidget(tabs)

        # Shared settings (language & theme — same QSettings as slicing)
        shared_qs = AppSettings()

        # ── General tab ──────────────────────────────────────────
        tab_general = QWidget(dlg)
        general_form = QFormLayout(tab_general)

        # Language (shared with slicing)
        lang = QComboBox(tab_general)
        lang.addItem(tr("settings.lang.zh_CN"), "zh_CN")
        lang.addItem(tr("settings.lang.en_US"), "en_US")
        idx = lang.findData(shared_qs.language())
        lang.setCurrentIndex(idx if idx >= 0 else 0)
        general_form.addRow(tr("settings.lang"), lang)

        # Theme (shared with slicing)
        theme_combo = QComboBox(tab_general)
        theme_combo.addItem(tr("settings.theme.system"), Theme.SYSTEM)
        theme_combo.addItem(tr("settings.theme.light"), Theme.LIGHT)
        theme_combo.addItem(tr("settings.theme.dark"), Theme.DARK)
        theme_idx = theme_combo.findData(shared_qs.theme())
        theme_combo.setCurrentIndex(theme_idx if theme_idx >= 0 else 0)
        general_form.addRow(tr("settings.theme"), theme_combo)

        # Project directory mode (annotation-specific)
        mode = QComboBox(tab_general)
        mode.addItem(tr("settings.mode.home"), "home_default")
        mode.addItem(tr("settings.mode.source"), "next_to_source")
        mode.addItem(tr("settings.mode.exe"), "next_to_executable")
        mode.addItem(tr("settings.mode.custom"), "custom")
        cur_mode = self._settings.project_dir_mode()
        for i in range(mode.count()):
            if mode.itemData(i) == cur_mode:
                mode.setCurrentIndex(i)
                break
        general_form.addRow(tr("settings.project_dir"), mode)

        # Startup behavior (annotation-specific)
        startup_behavior = QComboBox(tab_general)
        startup_behavior.addItem(tr("settings.startup.ask"), "ask")
        startup_behavior.addItem(tr("settings.startup.do_nothing"), "do_nothing")
        startup_behavior.addItem(tr("settings.startup.auto_load"), "auto_load")
        cur_behavior = self._settings.startup_behavior()
        for i in range(startup_behavior.count()):
            if startup_behavior.itemData(i) == cur_behavior:
                startup_behavior.setCurrentIndex(i)
                break
        general_form.addRow(tr("settings.startup_behavior"), startup_behavior)

        # Max undo steps (annotation-specific)
        sb_undo_steps = QSpinBox(tab_general)
        sb_undo_steps.setRange(10, 500)
        sb_undo_steps.setValue(self._settings.undo_max_steps())
        general_form.addRow(tr("settings.undo_max_steps"), sb_undo_steps)

        tabs.addTab(tab_general, tr("settings.tab.general"))

        # ── LLM API presets tab ──────────────────────────────────
        tab_llm = QWidget(dlg)
        tab_llm_layout = QVBoxLayout(tab_llm)
        llm_form_info = self._build_llm_preset_form(tab_llm)
        tab_llm_layout.addWidget(llm_form_info["widget"])
        tabs.addTab(tab_llm, tr("settings.tab.llm_presets"))

        # ── OK / Cancel ──────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )

        def _on_settings_accept() -> None:
            if not llm_form_info["accept"]():
                return
            dlg.accept()

        buttons.accepted.connect(_on_settings_accept)
        buttons.rejected.connect(lambda: (llm_form_info["teardown"](), dlg.reject()))
        lay.addWidget(buttons)

        result = dlg.exec()
        llm_form_info["teardown"]()
        if result != QDialog.DialogCode.Accepted:
            return

        # After dialog closes, refresh the left-panel combo
        self._llm_panel._refresh_presets()

        # Save language and theme (shared)
        shared_qs.set_language(lang.currentData())
        shared_qs.set_theme(theme_combo.currentData())
        set_theme(shared_qs.theme())

        # Save annotation-specific settings
        self._settings.set_project_dir_mode(mode.currentData())
        self._settings.set_startup_behavior(startup_behavior.currentData())
        self._settings.set_undo_max_steps(sb_undo_steps.value())
        self._max_undo_steps = sb_undo_steps.value()

    # ── playback ──────────────────────────────────────────────────

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        st = self.style()
        self._btn_play.setText("")
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._btn_play.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self._btn_play.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _step_frame(self, delta: int) -> None:
        was_playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if self._tb is not None:
            new_frame = max(0, min(self._tb.total_frames - 1, self._current_frame + delta))
            self._current_frame = new_frame
            self._player.setPosition(int(new_frame / self._tb.fps * 1000))
            self._timeline.set_state(
                total_frames=self._tb.total_frames,
                current_frame=new_frame,
                clips=[], view_start=0, view_span=self._tb.total_frames,
                fps=self._tb.fps, selected_clip_index=-1,
                markers=self._get_annotation_markers(),
                selected_marker_frame=self._get_selected_marker_frame(),
            )
        if was_playing:
            self._player.play()

    def _on_volume_changed(self, value: int) -> None:
        if self._audio is not None:
            self._audio.setVolume(value / 100.0)

    # ── signal handlers ───────────────────────────────────────────

    def _on_player_position(self, position_ms: int) -> None:
        """Update timeline playhead as playback progresses (throttled to 20 fps)."""
        if self._tb is None:
            return
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        now = time.monotonic()
        if now - self._last_pos_ui_update < 0.05:
            return
        self._last_pos_ui_update = now
        self._current_frame = round(position_ms / 1000.0 * self._tb.fps)
        self._timeline.set_state(
            total_frames=self._tb.total_frames,
            current_frame=self._current_frame,
            clips=[],
            view_start=0,
            view_span=self._tb.total_frames,
            fps=self._tb.fps,
            selected_clip_index=-1,
            markers=self._get_annotation_markers(),
            selected_marker_frame=self._get_selected_marker_frame(),
        )

    def _on_timeline_crosshair_hover(self, frame: object) -> None:
        """Feed hover frame back as vertical sync crosshair."""
        self._timeline.set_sync_crosshair_frame(
            max(0, min(int(frame), self._tb.total_frames - 1))
            if frame is not None and self._tb is not None else None
        )

    def _on_timeline_seek(self, frame: int) -> None:
        self._current_frame = frame
        if self._tb is not None:
            self._player.setPosition(int(frame / self._tb.fps * 1000))
            self._timeline.set_state(
                total_frames=self._tb.total_frames,
                current_frame=frame,
                clips=[],
                view_start=0,
                view_span=self._tb.total_frames,
                fps=self._tb.fps,
                selected_clip_index=-1,
                markers=self._get_annotation_markers(),
                selected_marker_frame=self._get_selected_marker_frame(),
            )

    def _on_system_prompt_changed(self) -> None:
        if self._project is not None:
            self._project.system_prompt = self._system_prompt_edit.toPlainText()

    def _on_frame_removed(self, index: int) -> None:
        self._push_undo()
        if self._active_clip is None:
            return
        sorted_anns = sorted(self._active_clip.annotations, key=lambda a: a.frame_index)
        if 0 <= index < len(sorted_anns):
            target = sorted_anns[index]
            for i, a in enumerate(self._active_clip.annotations):
                if a.frame_index == target.frame_index:
                    self._active_clip.annotations.pop(i)
                    break
        self._selected_annotation_index = -1
        self._refresh_annotation_nudge_controls()
        self._update_timeline_markers()

    def _on_frame_selected(self, frame_index: int) -> None:
        # Update selection index
        if self._active_clip is not None:
            sorted_anns = sorted(self._active_clip.annotations, key=lambda a: a.frame_index)
            for i, a in enumerate(sorted_anns):
                if a.frame_index == frame_index:
                    self._selected_annotation_index = i
                    break
        self._update_timeline_markers()
        self._refresh_annotation_nudge_controls()
        if self._tb is not None:
            self._current_frame = frame_index
            self._player.setPosition(int(frame_index / self._tb.fps * 1000))

    def _on_llm_error(self, error: str) -> None:
        self.status_message.emit(tr("annotation.llm_error", error=error))

    # ── Tree-based conversation (replaces old flat conversation) ────

    def _on_user_message_edit(self, node_id: str) -> None:
        """Open dialog to edit a user message, creating a new sibling branch."""
        clip = self._active_clip
        if clip is None or node_id not in clip.tree_nodes:
            return
        node = clip.tree_nodes[node_id]
        if node.role != "user":
            return
        is_root = (node.parent_id is None)

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Message")
        dlg.resize(500, 300)
        lay = QVBoxLayout(dlg)
        edit = QPlainTextEdit(dlg)
        edit.setPlainText(node.content)
        lay.addWidget(edit)
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_text = edit.toPlainText().strip()
        # Root messages have handles (frames/video), so empty text is allowed
        if not new_text and not is_root:
            return

        self._push_undo()

        import uuid
        from datetime import datetime, timezone
        from easyclip.annotation.project import ConversationNode

        # Build content_parts for root (first message needs frames)
        content_parts = node.content_parts
        if is_root:
            preset = self._llm_panel.active_preset()
            if preset is not None and self._project is not None:
                is_omni = preset.is_omni_model
                clip_video_path = str(self._project.resolve_clip_path(clip))
                frame_content = build_llm_content(
                    clip.annotations, self._subtitle_track,
                    include_images=not is_omni,
                    project_dir=self._project.project_dir,
                    omni_mode=is_omni, clip_video_path=clip_video_path,
                    omni_media_format=preset.omni_media_format,
                )
                if new_text:
                    frame_content.append({"type": "text", "text": new_text})
                content_parts = frame_content

        # Create sibling user node
        new_user = ConversationNode(
            id=str(uuid.uuid4()), role="user", content=new_text,
            content_parts=content_parts,
            parent_id=node.parent_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            annotation_snapshot=clip.commit_annotations_snapshot(),
        )
        parent_id = node.parent_id
        if parent_id is not None:
            clip.add_child_node(parent_id, new_user)
        else:
            # Root-level sibling: keep original root, add new root alongside
            clip.tree_nodes[new_user.id] = new_user
            clip.current_node_id = new_user.id

        # Create assistant child and send to LLM
        new_assistant = ConversationNode(
            id=str(uuid.uuid4()), role="assistant", content="",
            reasoning="", parent_id=new_user.id, source="llm",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        clip.add_child_node(new_user.id, new_assistant)

        self._chat_view.load_clip(clip, self._project.system_prompt if self._project else "")
        saved = clip.current_node_id
        clip.current_node_id = new_user.id
        messages = self._build_messages_from_tree(clip)
        clip.current_node_id = saved

        new_bubble = self._chat_view.get_last_assistant_bubble()
        request_clip = clip

        streaming_content: list[str] = []
        thinking_start: float | None = None
        import time as _time

        def _on_chunk(content: str, reasoning: str) -> None:
            nonlocal thinking_start
            if self._active_clip is not request_clip:
                return
            if reasoning:
                if thinking_start is None:
                    thinking_start = _time.monotonic()
                if new_bubble:
                    new_bubble.append_thinking(reasoning)
            if content and new_bubble:
                new_bubble.append_content(content)
            self._chat_view.scroll_to_bottom()

        def _on_stream_done() -> None:
            final_text = new_bubble.finalize() if new_bubble else ""
            if self._active_clip is request_clip and new_bubble:
                thinking_text = new_bubble.get_thinking_text()
                duration = (_time.monotonic() - thinking_start) if thinking_start else 0.0
                new_assistant.content = final_text
                new_assistant.reasoning = thinking_text
                new_assistant.thinking_duration = duration
                annotations, discussion = _parse_annotation_tags(final_text)
                new_assistant.annotations = annotations
                new_assistant.annotation_selected = [False] * len(annotations)
                new_bubble.set_annotations(annotations, discussion, new_assistant.annotation_selected)
                if thinking_text:
                    new_bubble.collapse_thinking(duration)
                clip.prompt = final_text
                self._chat_view.set_streaming_mode(False)
                self._chat_view.load_clip(clip, self._project.system_prompt if self._project else "")
                self._save_project()
                self.status_message.emit(tr("annotation.llm_done"))

        def _on_llm_ok(text: str) -> None:
            if self._active_clip is request_clip and new_bubble:
                new_assistant.content = text
                new_bubble._content_label.setText(text)
                annotations, discussion = _parse_annotation_tags(text)
                new_assistant.annotations = annotations
                new_assistant.annotation_selected = [False] * len(annotations)
                new_bubble.set_annotations(annotations, discussion, new_assistant.annotation_selected)
                clip.prompt = text
                self._chat_view.set_streaming_mode(False)
                self._chat_view.load_clip(clip, self._project.system_prompt if self._project else "")
                self._save_project()
                self.status_message.emit(tr("annotation.llm_done"))

        def _on_llm_fail(error: str) -> None:
            self._chat_view.set_streaming_mode(False)
            self.status_message.emit(tr("annotation.llm_error", error=error))

        self._chat_view.set_streaming_mode(True)
        self._llm_panel._connect_streaming(_on_chunk, _on_stream_done)
        self._llm_panel.call_llm(messages=messages, on_result=_on_llm_ok, on_error=_on_llm_fail)

    def _on_show_raw_output(self, node_id: str) -> None:
        """Open a dialog showing the raw LLM output for debugging."""
        clip = self._active_clip
        if clip is None or node_id not in clip.tree_nodes:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Raw LLM Output")
        dlg.resize(600, 450)
        lay = QVBoxLayout(dlg)
        edit = QPlainTextEdit(dlg)
        edit.setReadOnly(True)
        edit.setPlainText(clip.tree_nodes[node_id].content)
        lay.addWidget(edit)
        dlg.exec()

    def _on_add_manual_annotation(self) -> None:
        """Open a dialog for writing a manual annotation, inserted as an assistant node."""
        if self._active_clip is None or self._project is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("annotation.add_manual_title"))
        dlg.resize(500, 350)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(tr("annotation.add_manual_prompt")))
        edit = QPlainTextEdit(dlg)
        edit.setPlaceholderText(tr("annotation.add_manual_placeholder"))
        lay.addWidget(edit)
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = edit.toPlainText().strip()
        if not text:
            return

        import uuid
        from datetime import datetime, timezone
        from easyclip.annotation.project import ConversationNode

        clip = self._active_clip
        self._push_undo()

        manual_node = ConversationNode(
            id=str(uuid.uuid4()),
            role="assistant",
            content=text,
            source="manual",
            parent_id=clip.current_node_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        clip.add_child_node(clip.current_node_id, manual_node)

        self._save_project()
        self._chat_view.load_clip(
            clip, self._project.system_prompt if self._project else ""
        )
        self._chat_view._show_add_manual_button()

    def _on_annotation_edit(self, node_id: str, index: int) -> None:
        clip = self._active_clip
        if clip is None or node_id not in clip.tree_nodes:
            return
        node = clip.tree_nodes[node_id]
        if index >= len(node.annotations):
            return
        old_text = node.annotations[index]
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Annotation")
        dlg.resize(500, 300)
        lay = QVBoxLayout(dlg)
        edit = QPlainTextEdit(dlg)
        edit.setPlainText(old_text)
        lay.addWidget(edit)
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_text = edit.toPlainText().strip()
        if not new_text or new_text == old_text:
            return
        self._push_undo()
        # Update annotation text
        node.annotations[index] = new_text
        # Update the <annotation> block in raw content
        old_block = f"<annotation>{old_text}</annotation>"
        new_block = f"<annotation>{new_text}</annotation>"
        node.content = node.content.replace(old_block, new_block, 1)
        self._save_project()
        # Refresh chat display
        self._chat_view.load_clip(clip, self._project.system_prompt if self._project else "")
        self._chat_view._show_add_manual_button() if not node.annotations else self._chat_view._hide_add_manual_button()

    def _on_annotation_select_toggled(self, node_id: str, index: int, selected: bool) -> None:
        clip = self._active_clip
        if clip is None or node_id not in clip.tree_nodes:
            return
        node = clip.tree_nodes[node_id]
        if index >= len(node.annotation_selected):
            return
        self._push_undo()
        if selected:
            # Deselect previous
            if clip.selected_annotation_node_id and clip.selected_annotation_node_id in clip.tree_nodes:
                prev_node = clip.tree_nodes[clip.selected_annotation_node_id]
                pi = clip.selected_annotation_index
                if 0 <= pi < len(prev_node.annotation_selected):
                    prev_node.annotation_selected[pi] = False
            # Select current
            node.annotation_selected[index] = True
            clip.selected_annotation_node_id = node_id
            clip.selected_annotation_index = index
            clip.prompt = node.annotations[index]
            self._write_clip_prompt_txt(clip)
        else:
            node.annotation_selected[index] = False
            clip.selected_annotation_node_id = None
            clip.selected_annotation_index = -1
            # Clear .txt
            txt_path = self._clip_prompt_txt_path(clip)
            if txt_path.is_file():
                txt_path.unlink()
        self._save_project()
        # Refresh chat display to show gold border
        self._chat_view.load_clip(clip, self._project.system_prompt if self._project else "")

    def _on_chat_send(self, text: str) -> None:
        """User typed a message and clicked Send."""
        if self._active_clip is None or self._project is None:
            return
        preset = self._llm_panel.active_preset()
        if preset is None:
            self.status_message.emit(tr("annotation.no_preset"))
            return

        self._push_undo()

        # Sync draft editors to annotations

        is_omni = preset.is_omni_model
        clip_video_path = str(self._project.resolve_clip_path(self._active_clip))
        project_dir = self._project.project_dir

        # Build user content: frame images/video + subtitles
        frame_content = build_llm_content(
            self._active_clip.annotations,
            self._subtitle_track,
            include_images=not is_omni,
            project_dir=project_dir,
            omni_mode=is_omni,
            clip_video_path=clip_video_path,
            omni_media_format=preset.omni_media_format,
        )
        _log_llm_prompt(frame_content, self._project.system_prompt)

        # Append user text to content parts if provided
        if text.strip():
            if isinstance(frame_content, list):
                frame_content.append({"type": "text", "text": text})

        import uuid
        from datetime import datetime, timezone
        from easyclip.annotation.project import ConversationNode

        clip = self._active_clip

        # Commit current annotations as a snapshot for this message
        annotation_snapshot = clip.commit_annotations_snapshot()

        # Create user node
        user_node = ConversationNode(
            id=str(uuid.uuid4()),
            role="user",
            content=text,
            content_parts=frame_content,
            timestamp=datetime.now(timezone.utc).isoformat(),
            annotation_snapshot=annotation_snapshot,
        )

        clip = self._active_clip
        if clip.root_node_id is None:
            # First message — becomes root
            clip.tree_nodes[user_node.id] = user_node
            clip.root_node_id = user_node.id
            clip.current_node_id = user_node.id
        else:
            # Append to tree
            clip.add_child_node(clip.current_node_id, user_node)

        self._chat_view.add_user_bubble(user_node)

        # Build messages from conversation path
        messages = self._build_messages_from_tree(clip)

        # Create pending assistant node
        assistant_node = ConversationNode(
            id=str(uuid.uuid4()),
            role="assistant",
            content="",
            reasoning="",
            parent_id=user_node.id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="llm",
        )
        clip.add_child_node(user_node.id, assistant_node)

        # Add empty assistant bubble for streaming
        assistant_bubble = self._chat_view.add_assistant_bubble(assistant_node)

        request_clip = clip

        # Streaming state
        streaming_content: list[str] = []
        _reasoning_parts: list[str] = []
        thinking_start: float | None = None
        import time as _time

        def _on_chunk(content: str, reasoning: str) -> None:
            nonlocal thinking_start
            if self._active_clip is not request_clip:
                return
            if reasoning:
                if thinking_start is None:
                    thinking_start = _time.monotonic()
                assistant_bubble.append_thinking(reasoning)
            if content:
                assistant_bubble.append_content(content)
            self._chat_view.scroll_to_bottom()

        def _on_stream_done() -> None:
            final_text = assistant_bubble.finalize()
            if self._active_clip is request_clip:
                thinking_text = assistant_bubble.get_thinking_text()
                duration = (_time.monotonic() - thinking_start) if thinking_start else 0.0
                assistant_node.content = final_text
                annotations, discussion = _parse_annotation_tags(final_text)
                assistant_node.annotations = annotations
                assistant_node.annotation_selected = [False] * len(annotations)
                assistant_bubble.set_annotations(annotations, discussion, assistant_node.annotation_selected)
                if annotations:
                    self._chat_view._hide_add_manual_button()
                else:
                    self._chat_view._show_add_manual_button()
                assistant_node.reasoning = thinking_text
                assistant_node.thinking_duration = duration
                if thinking_text:
                    assistant_bubble.collapse_thinking(duration)
                # Update prompt from final assistant content
                clip.prompt = final_text
                self._chat_view.set_streaming_mode(False)
                self._chat_view.set_context_handles([])
                self._save_project()
                self.status_message.emit(tr("annotation.llm_done"))

        def _on_llm_ok(text: str) -> None:
            if self._active_clip is request_clip:
                assistant_node.content = text
                annotations, discussion = _parse_annotation_tags(text)
                assistant_node.annotations = annotations
                assistant_node.annotation_selected = [False] * len(annotations)
                assistant_bubble.set_annotations(annotations, discussion, assistant_node.annotation_selected)
                if annotations:
                    self._chat_view._hide_add_manual_button()
                else:
                    self._chat_view._show_add_manual_button()
                clip.prompt = text
                self._chat_view.set_streaming_mode(False)
                self._chat_view.set_context_handles([])
                self._save_project()
                self.status_message.emit(tr("annotation.llm_done"))

        def _on_llm_fail(error: str) -> None:
            self._chat_view.set_streaming_mode(False)
            self.status_message.emit(tr("annotation.llm_error", error=error))

        self._chat_view.set_streaming_mode(True)
        self._llm_panel._connect_streaming(_on_chunk, _on_stream_done)
        self._llm_panel.call_llm(
            messages=messages,
            on_result=_on_llm_ok,
            on_error=_on_llm_fail,
        )

    def _build_messages_from_tree(self, clip: AnnotatedClip) -> list[dict]:
        messages: list[dict] = []
        if self._project and self._project.system_prompt:
            sp = self._project.system_prompt + "\n\nWrap each annotation output in <annotation>...</annotation> tags. Any text outside the tags is discussion."
            messages.append({"role": "system", "content": sp})
        elif self._project:
            messages.append({"role": "system", "content": "Wrap each annotation output in <annotation>...</annotation> tags."})
        for node_id in clip.conversation_path():
            node = clip.tree_nodes.get(node_id)
            if node is None:
                continue
            if node.role == "user":
                if node.content_parts:
                    messages.append({"role": "user", "content": node.content_parts})
                else:
                    messages.append({"role": "user", "content": node.content})
            elif node.role == "assistant" and node.content:
                messages.append({"role": "assistant", "content": node.content})
        return messages

    def _on_regenerate_requested(self, node_id: str) -> None:
        clip = self._active_clip
        if clip is None or self._project is None:
            return
        node = clip.tree_nodes.get(node_id)
        if node is None or node.role != "assistant":
            return

        # Confirmation
        box = QMessageBox(self)
        box.setWindowTitle(tr("annotation.chat.regenerate_confirm_title"))
        box.setText(tr("annotation.chat.regenerate_confirm_body"))
        btn_ok = box.addButton(tr("annotation.chat.regenerate"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(btn_ok)
        box.exec()
        if box.clickedButton() != btn_ok:
            return

        self._push_undo()

        import uuid
        from datetime import datetime, timezone
        from easyclip.annotation.project import ConversationNode

        # Build messages up to the parent user node (exclude current assistant)
        parent_id = node.parent_id
        # Temporarily set current_node_id to parent to build correct messages
        saved_current = clip.current_node_id
        clip.current_node_id = parent_id
        messages = self._build_messages_from_tree(clip)
        clip.current_node_id = saved_current  # restore

        # Create new assistant node as sibling
        new_assistant = ConversationNode(
            id=str(uuid.uuid4()),
            role="assistant",
            content="",
            reasoning="",
            parent_id=parent_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="llm",
        )
        clip.add_child_node(parent_id, new_assistant)

        # Rebuild chat view to show the new branch
        self._chat_view.load_clip(clip, self._project.system_prompt if self._project else "")

        # Find the newly added assistant bubble
        new_bubble = self._chat_view.get_last_assistant_bubble()
        if new_bubble is None:
            return

        request_clip = clip
        thinking_start: float | None = None
        import time as _time

        def _on_chunk(content: str, reasoning: str) -> None:
            nonlocal thinking_start
            if self._active_clip is not request_clip:
                return
            if reasoning:
                if thinking_start is None:
                    thinking_start = _time.monotonic()
                new_bubble.append_thinking(reasoning)
            if content:
                new_bubble.append_content(content)
            self._chat_view.scroll_to_bottom()

        def _on_stream_done() -> None:
            final_text = new_bubble.finalize()
            if self._active_clip is request_clip:
                thinking_text = new_bubble.get_thinking_text()
                duration = (_time.monotonic() - thinking_start) if thinking_start else 0.0
                new_assistant.content = final_text
                annotations, discussion = _parse_annotation_tags(final_text)
                new_assistant.annotations = annotations
                new_assistant.annotation_selected = [False] * len(annotations)
                new_assistant.reasoning = thinking_text
                new_assistant.thinking_duration = duration
                if thinking_text:
                    new_bubble.collapse_thinking(duration)
                clip.prompt = final_text
                self._chat_view.set_streaming_mode(False)
                # Reload to update branch nav on the sibling bubbles
                self._chat_view.load_clip(clip, self._project.system_prompt if self._project else "")
                if annotations:
                    self._chat_view._hide_add_manual_button()
                else:
                    self._chat_view._show_add_manual_button()
                self._save_project()
                self.status_message.emit(tr("annotation.llm_done"))

        def _on_llm_ok(text: str) -> None:
            if self._active_clip is request_clip:
                new_assistant.content = text
                annotations, discussion = _parse_annotation_tags(text)
                new_assistant.annotations = annotations
                new_assistant.annotation_selected = [False] * len(annotations)
                clip.prompt = text
                self._chat_view.set_streaming_mode(False)
                self._chat_view.load_clip(clip, self._project.system_prompt if self._project else "")
                if annotations:
                    self._chat_view._hide_add_manual_button()
                else:
                    self._chat_view._show_add_manual_button()
                self._save_project()
                self.status_message.emit(tr("annotation.llm_done"))

        def _on_llm_fail(error: str) -> None:
            self._chat_view.set_streaming_mode(False)
            self.status_message.emit(tr("annotation.llm_error", error=error))

        self._chat_view.set_streaming_mode(True)
        self._llm_panel._connect_streaming(_on_chunk, _on_stream_done)
        self._llm_panel.call_llm(
            messages=messages,
            on_result=_on_llm_ok,
            on_error=_on_llm_fail,
        )

    def _on_branch_navigated(self, node_id: str, direction: int) -> None:
        clip = self._active_clip
        if clip is None:
            return
        # Try navigating among own children first, then siblings
        node = clip.tree_nodes.get(node_id)
        if node and len(node.children_ids) > 1:
            clip.navigate_children(node_id, direction)
        else:
            clip.navigate_sibling(node_id, direction)
        clip.rebuild_annotations_from_path()
        self._refresh_editor_from_clip()
        self._save_project()


# ── Module-level helpers for LLM content building ────────────────────


def _parse_annotation_tags(text: str) -> tuple[list[str], str]:
    # Strip <think> blocks first — they may contain <annotation> literals
    clean = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    # Match <annotation> only at line start (avoids false matches on mentions like "use <annotation>")
    annotations = re.findall(r'(?m)^\s*<annotation>\s*(.*?)\s*</annotation>', clean, re.DOTALL)
    annotations = [a.strip() for a in annotations]
    discussion = re.sub(r'(?m)^\s*<annotation>.*?</annotation>\s*', '', clean, flags=re.DOTALL).strip()
    return annotations, discussion


def build_llm_content(
    annotations: list[FrameAnnotation],
    subtitles: SubtitleTrack | None,
    include_images: bool,
    project_dir: str,
    *,
    omni_mode: bool = False,
    clip_video_path: str | None = None,
    omni_media_format: str = "qwen_omni",
) -> list[dict]:
    """Build a content array for OpenAI-compatible API calls.

    VLM mode (omni_mode=False): frame images only.
    Omni mode (omni_mode=True):  full clip video+audio only.
    Subtitles are appended as text context when available.
    The user's chat text is appended separately by the caller.
    """
    content: list[dict] = []
    proj_path = Path(project_dir)

    # ── Omni mode: prepend video as the first content part ──
    if omni_mode and clip_video_path:
        vpath = Path(clip_video_path)
        if vpath.is_file():
            vid_b64 = base64.b64encode(vpath.read_bytes()).decode()
            content.append({
                "type": "video_url",
                "video_url": {"url": f"data:video/mp4;base64,{vid_b64}"},
            })

    # ── VLM mode: interleaved frame labels + images ──
    if not omni_mode:
        for fa in annotations:
            if include_images and fa.image_path:
                img_path = proj_path / fa.image_path
                if img_path.is_file():
                    # Frame label before image helps the VLM correlate references
                    content.append({
                        "type": "text",
                        "text": f"[Frame {fa.frame_index} ({fa.timestamp_sec:.1f}s)]",
                    })
                    b64 = base64.b64encode(img_path.read_bytes()).decode()
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    })

    # ── Subtitles as context ──
    if subtitles and subtitles.entries:
        sub_text = " ".join(e.text for e in subtitles.entries[:50])
        if sub_text.strip():
            content.append({"type": "text", "text": f"Video subtitles: {sub_text}"})

    # Fallback instruction
    if not content:
        content.append({
            "type": "text",
            "text": "Please describe this video clip in detail for text-to-video generation.",
        })

    return content


def _log_llm_prompt(content: list[dict], system_prompt: str = "") -> None:
    """Print the raw LLM prompt to console for debugging.

    Images are replaced with 🖼 placeholders to keep output readable.
    """
    print("=" * 60)
    print("LLM PROMPT DEBUG")
    print("=" * 60)

    if system_prompt:
        print(f"\n[SYSTEM]\n{system_prompt}\n")

    print("[USER CONTENT]")
    for i, block in enumerate(content):
        t = block.get("type", "")
        if t == "video_url":
            b64_len = len(block.get("video_url", {}).get("url", ""))
            kb = (b64_len - 37) * 3 // 4 // 1024  # approximate decoded size
            print(f"  [{i}] 🎬 [Video clip, ~{kb} KB]")
        elif t == "image_url":
            print(f"  [{i}] 🖼  [Frame image]")
        elif t == "text":
            text = block["text"]
            if len(text) > 500:
                text = text[:500] + "..."
            print(f"  [{i}] 📝 {text}")

    print("=" * 60)
