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
from easyclip.annotation.widgets.annotation_editor import AnnotationEditor
from easyclip.annotation.widgets.llm_panel import LLMPanel
from easyclip.core.ffmpeg_util import probe_video, find_ffmpeg
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
        top_layout.addWidget(QLabel(tr("annotation.clip_list")), 0)
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

        # Video preview + side prompt panel (horizontal splitter)
        self._player = QMediaPlayer(self)
        self._audio: QAudioOutput | None = None      # created lazily in on_tab_activated()
        self._video_sink = QVideoSink(self)
        self._player.setVideoSink(self._video_sink)

        self._video_prompt_split = QSplitter(Qt.Orientation.Horizontal, right)

        # Left: video preview
        self._video_preview = VideoPreviewWidget(self._player, self._video_prompt_split)
        self._video_prompt_split.addWidget(self._video_preview)

        # Right: prompt panel
        prompt_panel = QWidget(self._video_prompt_split)
        prompt_layout = QVBoxLayout(prompt_panel)
        prompt_layout.setContentsMargins(4, 0, 0, 0)

        # Prompt label + version navigation row
        prompt_header = QWidget(prompt_panel)
        prompt_header_lay = QHBoxLayout(prompt_header)
        prompt_header_lay.setContentsMargins(0, 0, 0, 0)
        prompt_header_lay.addWidget(QLabel(tr("annotation.prompt_label")))
        prompt_header_lay.addStretch()

        self._btn_version_prev = QPushButton("←")
        self._btn_version_prev.setFixedWidth(28)
        self._btn_version_prev.setToolTip(tr("annotation.version.prev_tip"))
        self._btn_version_prev.setEnabled(False)
        prompt_header_lay.addWidget(self._btn_version_prev)

        self._lbl_version = QLabel("—")
        self._lbl_version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_version.setFixedWidth(40)
        prompt_header_lay.addWidget(self._lbl_version)

        self._btn_version_next = QPushButton("→")
        self._btn_version_next.setFixedWidth(28)
        self._btn_version_next.setToolTip(tr("annotation.version.next_tip"))
        self._btn_version_next.setEnabled(False)
        prompt_header_lay.addWidget(self._btn_version_next)

        self._btn_version_delete = QPushButton("🗑")
        self._btn_version_delete.setFixedWidth(28)
        self._btn_version_delete.setToolTip(tr("annotation.version.delete_tip"))
        self._btn_version_delete.setEnabled(False)
        prompt_header_lay.addWidget(self._btn_version_delete)
        prompt_layout.addWidget(prompt_header)

        # Prompt text edit
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlaceholderText(tr("annotation.prompt_placeholder"))
        prompt_layout.addWidget(self._prompt_edit, 1)

        self._video_prompt_split.addWidget(prompt_panel)
        self._video_prompt_split.setSizes([600, 250])
        self._video_prompt_split.setStretchFactor(0, 3)
        self._video_prompt_split.setStretchFactor(1, 1)
        self._video_prompt_split.setCollapsible(0, False)
        self._video_prompt_split.setCollapsible(1, False)

        # Annotation editor + LLM button (splitter bottom section)
        bottom = QWidget(right)
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 4, 0, 0)

        self._annotation_editor = AnnotationEditor(bottom)
        bottom_layout.addWidget(self._annotation_editor, 1)

        self._llm_panel = LLMPanel(self._settings, self._preset_combo, bottom)
        bottom_layout.addWidget(self._llm_panel, 0)

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

        # Three-section vertical splitter: video+prompt | timeline+transport | bottom
        # Handle A (video+prompt ↔ timeline+transport): resize video area.
        # Handle B (timeline+transport ↔ annotation editor): resize editor area.
        # Timeline+transport group is locked at fixed height (min==max) so it
        # never stretches when either handle is dragged.
        self._main_split = QSplitter(Qt.Orientation.Vertical, right)

        # Top: video + prompt (side-by-side)
        self._main_split.addWidget(self._video_prompt_split)

        # Middle: timeline + transport row (grouped, fixed height)
        tt_group = QWidget(self._main_split)
        tt_layout = QVBoxLayout(tt_group)
        tt_layout.setContentsMargins(0, 0, 0, 0)
        tt_layout.setSpacing(0)
        self._timeline = TimelineWidget(tt_group)
        self._timeline.setMinimumHeight(48)
        self._timeline.setMaximumHeight(48)
        tt_layout.addWidget(self._timeline)
        tt_layout.addLayout(transport_row)
        TT_GROUP_HEIGHT = 82  # 48 (timeline) + 34 (transport buttons + padding)
        tt_group.setMinimumHeight(TT_GROUP_HEIGHT)
        tt_group.setMaximumHeight(TT_GROUP_HEIGHT)
        self._main_split.addWidget(tt_group)

        # Bottom: annotation editor + LLM
        self._main_split.addWidget(bottom)

        self._main_split.setSizes([400, TT_GROUP_HEIGHT, 300])
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
            ("h_video_prompt", self._video_prompt_split),
        ]:
            self._settings.save_splitter_state(name, splitter.saveState().data())

    def _restore_splitter_layout(self) -> None:
        """Restore splitter sizes from QSettings (if previously saved)."""
        for name, splitter in [
            ("h_main", self._h_splitter),
            ("v_left", self._left_splitter),
            ("v_right", self._main_split),
            ("h_video_prompt", self._video_prompt_split),
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

    def _connect_signals(self) -> None:
        self._btn_open_folder.clicked.connect(self._open_project_folder)
        self._btn_version_prev.clicked.connect(lambda: self._go_to_version(-1))
        self._btn_version_next.clicked.connect(lambda: self._go_to_version(1))
        self._btn_version_delete.clicked.connect(self._delete_current_version)
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
        self._prompt_edit.textChanged.connect(self._on_prompt_text_changed)
        self._annotation_editor.frame_removed.connect(self._on_frame_removed)
        self._annotation_editor.frame_selected.connect(self._on_frame_selected)
        self._llm_panel.generate_requested.connect(self._on_llm_generate)
        self._llm_panel.preview_draft_requested.connect(self._on_preview_draft)
        self._btn_manage_presets.clicked.connect(self._show_llm_preset_manager)

        # Quick-access toggles: sync preset <-> checkboxes
        self._preset_combo.currentIndexChanged.connect(
            lambda _i: self._sync_quick_toggles_from_preset()
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
        if key == Qt.Key.Key_Left:
            self._step_frame(-1)
            event.accept()
            return True
        if key == Qt.Key.Key_Right:
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
        self._annotation_editor.cleanup()
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
        self._prompt_edit.setPlaceholderText(tr("annotation.prompt_placeholder"))
        self._annotation_editor.refresh_language()
        self._preset_label.setText(tr("annotation.llm_preset"))
        self._btn_manage_presets.setToolTip(tr("annotation.manage_presets"))
        self._qck_streaming.setText(tr("annotation.quick.streaming"))
        self._qck_thinking.setText(tr("annotation.quick.thinking"))
        self._qck_omni.setText(tr("annotation.quick.omni"))
        self._llm_panel.refresh_language()
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
        clip._sync_prompt_to_version()
        snap = {
            "prompt": clip.prompt,
            "draft_prompt": clip.draft_prompt,
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
        clip._sync_prompt_to_version()
        snap = {
            "prompt": clip.prompt, "draft_prompt": clip.draft_prompt,
            "state": clip.state,
            "annotations": [a.to_dict() for a in clip.annotations],
        }
        snap.update(clip._make_version_snapshot())
        clip.redo_history.append(snap)
        prev = clip.undo_history.pop()
        # Restore version state (handles navigation and deletions)
        if "versions" in prev:
            clip._restore_version_snapshot(prev)
        else:
            # Backward compat: old snapshots without version data
            clip.prompt = prev["prompt"]
        clip.draft_prompt = prev.get("draft_prompt", clip.draft_prompt)
        clip.state = prev.get("state", clip.state)
        if "annotations" in prev:
            clip.annotations = [FrameAnnotation.from_dict(a) for a in prev["annotations"]]
        self._refresh_editor_from_clip()
        self._refresh_version_ui()
        self._refresh_undo_state()
        self._save_project()

    def _redo(self) -> None:
        if not self._active_clip or not self._active_clip.redo_history:
            return
        clip = self._active_clip
        clip._sync_prompt_to_version()
        snap = {
            "prompt": clip.prompt, "draft_prompt": clip.draft_prompt,
            "state": clip.state,
            "annotations": [a.to_dict() for a in clip.annotations],
        }
        snap.update(clip._make_version_snapshot())
        clip.undo_history.append(snap)
        nxt = clip.redo_history.pop()
        if "versions" in nxt:
            clip._restore_version_snapshot(nxt)
        else:
            clip.prompt = nxt["prompt"]
        clip.draft_prompt = nxt.get("draft_prompt", clip.draft_prompt)
        clip.state = nxt.get("state", clip.state)
        if "annotations" in nxt:
            clip.annotations = [FrameAnnotation.from_dict(a) for a in nxt["annotations"]]
        self._refresh_editor_from_clip()
        self._refresh_version_ui()
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
        fa = FrameAnnotation(frame_index=frame, timestamp_sec=ts)
        self._active_clip.annotations.append(fa)
        self._active_clip.annotations.sort(key=lambda a: a.frame_index)
        # Select the newly added annotation (list already sorted in-place above)
        self._selected_annotation_index = next(
            (i for i, a in enumerate(self._active_clip.annotations) if a.frame_index == frame), -1
        )
        self._annotation_editor.set_annotations(self._active_clip.annotations)
        self._annotation_editor.select_annotation_by_frame(frame)
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
        # Remove from clip's annotation list
        for i, a in enumerate(self._active_clip.annotations):
            if a.frame_index == target.frame_index:
                self._active_clip.annotations.pop(i)
                break
        self._selected_annotation_index = -1
        self._annotation_editor.select_annotation_by_frame(None)
        self._annotation_editor.set_annotations(self._active_clip.annotations)
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
        ann.frame_index = new_frame
        ann.timestamp_sec = new_frame / self._tb.fps
        self._active_clip.annotations.sort(key=lambda a: a.frame_index)
        # Update selection index (list already sorted in-place above)
        for i, a in enumerate(self._active_clip.annotations):
            if a is ann:
                self._selected_annotation_index = i
                break
        self._annotation_editor.set_annotations(self._active_clip.annotations)
        self._annotation_editor.select_annotation_by_frame(new_frame)
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
                    "draft_prompt": self._active_clip.draft_prompt,
                    "state": self._active_clip.state,
                    "annotations": [ann.to_dict() for ann in self._active_clip.annotations],
                }
                self._drag_start_frame = frame_index
                self._annotation_editor.select_annotation_by_frame(frame_index)
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
        self._dragged_annotation = None
        if self._active_clip is not None:
            # Push undo if the frame actually moved
            start_snap = getattr(self, '_drag_start_snapshot', None)
            start_frame = getattr(self, '_drag_start_frame', -1)
            if start_snap is not None and start_frame != self._get_selected_marker_frame():
                clip = self._active_clip
                clip.undo_history.append(start_snap)
                clip.redo_history.clear()
                if len(clip.undo_history) > self._max_undo_steps:
                    clip.undo_history.pop(0)
                self._refresh_undo_state()
            self._drag_start_snapshot = None
            self._drag_start_frame = -1
            # Refresh thumbnails: the frame_index changed so old cache
            # entries are stale; set_annotations rebuilds the strip and
            # triggers re-extraction for new frame positions.
            self._annotation_editor.set_annotations(self._active_clip.annotations)
            if self._selected_annotation_index >= 0:
                sf = self._get_selected_marker_frame()
                self._annotation_editor.select_annotation_by_frame(sf)
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
            self._active_clip.prompt = self._prompt_edit.toPlainText()
            self._active_clip.draft_prompt = self._annotation_editor.draft_text()
            self._write_clip_prompt_txt(self._active_clip)
        # Also write .txt for every clip that has a prompt (batch sync)
        for clip in self._project.clips:
            if clip is not self._active_clip and clip.prompt.strip():
                self._write_clip_prompt_txt(clip)
        self._project.save()
        self.status_message.emit(tr("annotation.saved"))

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
            return
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
        self._refresh_version_ui()
        self._settings.set_last_selected_clip_path(clip.clip_path)
        self.window_title_changed.emit(
            f"EasyClip - {self._project.project_name} / {clip.clip_path}"
        )

    def _save_clip_state(self) -> None:
        if self._active_clip is not None:
            self._active_clip.prompt = self._prompt_edit.toPlainText()
            self._annotation_editor.sync_to_clip(self._active_clip)
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

    # ── version snapshots ───────────────────────────────────────────

    # ── version navigation (LLM chat-style) ─────────────────────────

    def _go_to_version(self, delta: int) -> None:
        """Navigate ``delta`` steps in the version history (-1 = prev, +1 = next)."""
        clip = self._active_clip
        if clip is None or clip.version_count == 0:
            return
        new_idx = clip.current_version + delta
        if not (0 <= new_idx < clip.version_count):
            return
        # Save current editor state into the current version
        clip.prompt = self._prompt_edit.toPlainText()
        clip._sync_prompt_to_version()
        # Push undo so navigation itself IS undoable (returns to this version)
        self._push_undo()
        clip.current_version = new_idx
        clip._sync_version_to_prompt()
        self._refresh_editor_from_clip()
        self._refresh_version_ui()
        self._save_project()

    def _on_prompt_text_changed(self) -> None:
        """Auto-create version 1 when the user first types into an empty prompt."""
        self._ensure_first_version()

    def _ensure_first_version(self) -> None:
        """Create the first version from the current prompt if none exist."""
        clip = self._active_clip
        if clip is None or clip.version_count > 0:
            return
        if not self._prompt_edit.toPlainText().strip():
            return  # empty prompt — don't create a version yet
        from datetime import datetime, timezone
        clip.prompt = self._prompt_edit.toPlainText()
        clip.versions.append({
            "prompt": clip.prompt,
            "source": "manual",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        clip.current_version = 0
        clip._sync_version_to_prompt()
        self._refresh_version_ui()
        self._save_project()

    def _delete_current_version(self) -> None:
        """Move current version to deleted_versions (undoable)."""
        clip = self._active_clip
        if clip is None or clip.version_count < 1:
            self.status_message.emit(tr("annotation.version.cant_delete_last"))
            return
        if clip.version_count == 1:
            # Last version: just clear it instead of deleting
            self.status_message.emit(tr("annotation.version.cant_delete_last"))
            return
        self._push_undo()
        removed = clip.versions.pop(clip.current_version)
        clip.deleted_versions.append(removed)
        # Switch to adjacent version
        if clip.current_version >= clip.version_count:
            clip.current_version = clip.version_count - 1
        clip._sync_version_to_prompt()
        self._refresh_editor_from_clip()
        self._refresh_version_ui()
        self._save_project()
        self.status_message.emit(tr("annotation.version.deleted"))

    def _append_llm_version(self, text: str) -> None:
        """Append a new LLM-generated version and switch to it."""
        clip = self._active_clip
        if clip is None:
            return
        from datetime import datetime, timezone
        clip._sync_prompt_to_version()
        clip.versions.append({
            "prompt": text,
            "source": "llm",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        clip.current_version = clip.version_count - 1
        clip._sync_version_to_prompt()
        clip.state = "completed"
        self._refresh_version_ui()
        self._save_project()

    def _refresh_version_ui(self) -> None:
        """Update version label and button enabled states."""
        clip = self._active_clip
        if clip is None:
            self._lbl_version.setText("—")
            self._btn_version_prev.setEnabled(False)
            self._btn_version_next.setEnabled(False)
            self._btn_version_delete.setEnabled(False)
            return
        self._lbl_version.setText(clip.version_label)
        has_versions = clip.version_count > 0
        self._btn_version_prev.setEnabled(has_versions and clip.current_version > 0)
        self._btn_version_next.setEnabled(has_versions and clip.current_version < clip.version_count - 1)
        self._btn_version_delete.setEnabled(has_versions and clip.version_count > 0)

    # ── clip preview ──────────────────────────────────────────────

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
        self._annotation_editor.set_video_source(str(video_path.resolve()))

        self._btn_add_annotation.setEnabled(True)

        # Detect external .txt modifications (like VS Code's "file changed on disk").
        self._check_external_txt_modification(clip)

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
        self._annotation_editor.clear()
        self._btn_add_annotation.setEnabled(False)
        self._lbl_version.setText("—")
        self._btn_version_prev.setEnabled(False)
        self._btn_version_next.setEnabled(False)
        self._btn_version_delete.setEnabled(False)

    def _ensure_ffmpeg(self) -> None:
        if self._ffmpeg is None:
            self._ffmpeg, self._ffprobe = find_ffmpeg()

    # ── editor refresh ────────────────────────────────────────────

    def _refresh_editor_from_clip(self) -> None:
        if self._active_clip is None:
            return
        self._prompt_edit.setPlainText(self._active_clip.prompt)
        self._annotation_editor.sync_from_clip(self._active_clip)
        self._selected_annotation_index = -1
        self._annotation_editor.select_annotation_by_frame(None)
        self._refresh_annotation_nudge_controls()
        if self._tb is not None:
            self._update_timeline_markers()

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
        self._annotation_editor.select_annotation_by_frame(None)
        self._refresh_annotation_nudge_controls()
        self._annotation_editor.set_annotations(self._active_clip.annotations)
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

    def _on_llm_generate(self) -> None:
        if self._active_clip is None or self._project is None:
            self.status_message.emit(tr("annotation.no_clip_selected"))
            return
        preset = self._llm_panel.active_preset()
        if preset is None:
            self.status_message.emit(tr("annotation.no_preset"))
            return

        self._push_undo()

        # Sync draft editors back to annotations before building content
        self._annotation_editor.sync_to_clip(self._active_clip)

        include_images = True
        project_dir = self._project.project_dir
        content = build_llm_content(
            self._active_clip.annotations,
            self._active_clip.draft_prompt,
            self._subtitle_track,
            include_images,
            project_dir,
        )

        # Debug: print raw prompt to console
        _log_llm_prompt(content, self._project.system_prompt)

        system_prompt = self._project.system_prompt

        # Capture the clip that initiated this request.
        # If the user switches clips before the response arrives, the
        # result is discarded to avoid writing to the wrong clip.
        request_clip = self._active_clip

        def _on_llm_ok(text: str) -> None:
            # Only apply if the user hasn't switched to a different clip
            if self._active_clip is request_clip:
                self._on_llm_result(text)

        def _on_llm_fail(error: str) -> None:
            # Always show errors regardless of clip switch
            self._on_llm_error(error)

        # Streaming handlers — append-only; never replace, only grow
        streaming_content: list[str] = []
        _reasoning_parts: list[str] = []          # pending reasoning deltas
        _content_shown: list[int] = [0]           # chars of content already written
        _locked: list[bool] = [False]             # prefix locked = content started
        _header_written: list[bool] = [False]

        def _do_append(text: str) -> tuple[int, int]:
            """Append *text* at end; returns (old_scroll, old_max)."""
            sb = self._prompt_edit.verticalScrollBar()
            saved = (sb.value(), sb.maximum()) if sb is not None else (0, 0)
            cursor = self._prompt_edit.textCursor()
            cursor.beginEditBlock()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(text)
            cursor.endEditBlock()
            return saved

        def _restore_scroll(saved: tuple[int, int], *, at_bottom: bool) -> None:
            """Restore scroll: chase bottom if *at_bottom*, else keep old position."""
            sb = self._prompt_edit.verticalScrollBar()
            if sb is None:
                return
            if at_bottom:
                sb.setValue(sb.maximum())
            else:
                # Clamp: new content may be shorter (e.g. during lock transition)
                sb.setValue(min(saved[0], sb.maximum()))

        def _on_chunk(content: str, reasoning: str) -> None:
            if self._active_clip is not request_clip:
                return
            if content:
                streaming_content.append(content)
            if reasoning:
                _reasoning_parts.append(reasoning)

            self._prompt_edit.setReadOnly(True)
            sb = self._prompt_edit.verticalScrollBar()
            at_bottom = sb is not None and sb.value() >= sb.maximum()

            if not _header_written[0]:
                _header_written[0] = True
                self._prompt_edit.setPlainText("〔思考中…〕\n")

            if not _locked[0]:
                # Append new reasoning deltas as they arrive
                while _reasoning_parts:
                    saved = _do_append(_reasoning_parts.pop(0))
                    _restore_scroll(saved, at_bottom=at_bottom)

                if streaming_content:
                    # Lock: append separator + all content so far
                    _locked[0] = True
                    sep = "\n───\n" if _reasoning_parts or _header_written[0] else ""
                    saved = _do_append(sep + "".join(streaming_content))
                    _content_shown[0] = len(streaming_content)
                    _restore_scroll(saved, at_bottom=at_bottom)
                return

            # Post-lock: append new content only
            c_all = "".join(streaming_content)
            new_c = c_all[_content_shown[0]:]
            if new_c:
                saved = _do_append(new_c)
                _content_shown[0] = len(c_all)
                _restore_scroll(saved, at_bottom=at_bottom)

        def _on_stream_done() -> None:
            final_text = "".join(streaming_content)
            if self._active_clip is request_clip:
                self._on_streaming_complete(final_text)

        # Connect streaming signals — LLMPanel clears any prior connections
        self._llm_panel._connect_streaming(_on_chunk, _on_stream_done)

        self._llm_panel.call_llm(
            system_prompt=system_prompt,
            content=content,
            on_result=_on_llm_ok,
            on_error=_on_llm_fail,
        )

    def _on_preview_draft(self) -> None:
        """Show a popup with the assembled draft text (text-only, no images)."""
        if self._active_clip is None:
            return
        # Sync draft editors back to annotations before building
        self._annotation_editor.sync_to_clip(self._active_clip)
        text = self._annotation_editor.build_text_prompt()
        if not text.strip():
            text = tr("annotation.preview_draft_empty")

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("annotation.preview_draft"))
        dlg.resize(600, 400)
        lay = QVBoxLayout(dlg)
        edit = QPlainTextEdit(dlg)
        edit.setReadOnly(True)
        edit.setPlainText(text)
        lay.addWidget(edit)
        dlg.exec()

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Remove ``<think>...</think>`` blocks from model output."""
        return re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL).strip()

    def _on_llm_result(self, text: str) -> None:
        cleaned = self._strip_thinking(text)
        self._prompt_edit.setPlainText(cleaned)
        self._append_llm_version(cleaned)
        self.status_message.emit(tr("annotation.llm_done"))

    def _on_llm_error(self, error: str) -> None:
        self.status_message.emit(tr("annotation.llm_error", error=error))

    def _on_streaming_complete(self, final_text: str) -> None:
        """Streaming finished — strip thinking, show clean content only."""
        cleaned = self._strip_thinking(final_text)
        self._prompt_edit.setReadOnly(False)
        self._prompt_edit.setPlainText(cleaned)
        self._append_llm_version(cleaned)
        self.status_message.emit(tr("annotation.llm_done"))


# ── Module-level helpers for LLM content building ────────────────────


def build_llm_content(
    annotations: list[FrameAnnotation],
    global_draft: str,
    subtitles: SubtitleTrack | None,
    include_images: bool,
    project_dir: str,
) -> list[dict]:
    """Build an interleaved content array for OpenAI-compatible API calls.

    Order per frame: image → frame_text → transition_text → next image → ...

    Args:
        annotations: Sorted by frame_index.
        global_draft: Clip-level description (skipped if empty/whitespace).
        subtitles: Subtitle track for context (optional).
        include_images: Include base64-encoded PNG images (VLM mode).
        project_dir: Project root for resolving relative image paths.

    Returns:
        A list of ``{"type": "image_url", ...}`` and ``{"type": "text", ...}`` dicts.
    """
    content: list[dict] = []
    proj_path = Path(project_dir)

    for i, fa in enumerate(annotations):
        # 1. Image (before frame text, so VLM sees image then reads description)
        if include_images and fa.image_path:
            img_path = proj_path / fa.image_path
            if img_path.is_file():
                b64 = base64.b64encode(img_path.read_bytes()).decode()
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                })

        # 2. Frame description text
        if fa.draft_text.strip():
            content.append({
                "type": "text",
                "text": f"Frame {fa.frame_index} ({fa.timestamp_sec:.1f}s): {fa.draft_text}",
            })

        # 3. Transition text (to next frame, if not last)
        if i < len(annotations) - 1 and fa.transition_text.strip():
            next_fa = annotations[i + 1]
            content.append({
                "type": "text",
                "text": (
                    f"→ Transition {fa.frame_index}→{next_fa.frame_index}"
                    f" ({fa.timestamp_sec:.1f}s→{next_fa.timestamp_sec:.1f}s):"
                    f" {fa.transition_text}"
                ),
            })

    # 4. Global context (only if non-empty)
    if global_draft.strip():
        content.append({"type": "text", "text": f"Clip context: {global_draft}"})

    # 5. Subtitles
    if subtitles and subtitles.entries:
        sub_text = " ".join(e.text for e in subtitles.entries[:50])
        if sub_text.strip():
            content.append({"type": "text", "text": f"Video subtitles: {sub_text}"})

    # Fallback
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
        if block.get("type") == "image_url":
            print(f"  [{i}] 🖼  [Frame image]")
        elif block.get("type") == "text":
            text = block["text"]
            if len(text) > 500:
                text = text[:500] + "..."
            print(f"  [{i}] 📝 {text}")

    print("=" * 60)
