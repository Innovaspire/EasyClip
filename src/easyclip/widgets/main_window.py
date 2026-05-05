"""Main application window: preview, clips, timeline, transport."""

from __future__ import annotations

import bisect
import sys
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PySide6.QtCore import (
    QEvent,
    QEventLoop,
    QPoint,
    QSize,
    QMimeData,
    QObject,
    Qt,
    QCoreApplication,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QCursor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
    QResizeEvent,
    QShowEvent,
    QWheelEvent,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPlainTextEdit,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QDialog,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QGridLayout,
    QSizePolicy,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
    QSplitter,
    QTabWidget,
    QToolButton,
    QDialogButtonBox,
)

from easyclip.core.align_io import (
    AlignConstraint,
    is_valid_length,
    output_frame_count,
    snap_moving_end,
    snap_moving_start,
)
from easyclip.core.export import DEFAULT_EXPORT_FILENAME_TEMPLATE, export_all_clips
from easyclip.core.export_debug import export_debug_log, export_debug_log_path
from easyclip.core.ffmpeg_util import (
    VideoProbe,
    build_proxy_mp4,
    extract_frame_png,
    find_ffmpeg,
    list_video_encoders,
    list_keyframe_times,
    probe_has_audio,
    probe_video_bitrate_kbps,
    probe_video,
    quantizer_symbol_for_codec,
    resolve_video_codec,
    stderr_encoding_stats_tick,
    video_codec_args_with_rate_control,
)
from easyclip.core.project import Clip, ClipState, ProjectData, ProjectStore, UndoState, resolve_project_root
from easyclip.core.settings import (
    AppSettings,
    ProjectDirMode,
    StartupBehavior,
    default_projects_root,
)
from easyclip.core.timebase import Timebase
from easyclip.core.waveform_gen import (
    STREAM_SAMPLE_RATE,
    WAVE_NPZ_VERSION,
    compute_waveform_peaks,
    recommended_peak_bins,
)
from easyclip.i18n.strings import tr
from easyclip.widgets.timeline_widget import TimelineWidget
from easyclip.widgets.video_preview_drop_shim import VideoPreviewDropShim
from easyclip.widgets.waveform_widget import WaveformWidget


VIDEO_OPEN_SUFFIXES = frozenset(
    {
        ".mp4",
        ".mkv",
        ".mov",
        ".avi",
        ".webm",
        ".m4v",
        ".ts",
        ".flv",
        ".wmv",
        ".mpeg",
        ".mpg",
    }
)

# For very long/high-bitrate sources, eager full keyframe scan can heavily contend with
# preview decode/waveform generation and make UI appear frozen on slower disks.
KEYFRAME_AUTOSCAN_MAX_DURATION_SEC = 60.0 * 60.0  # 1h
KEYFRAME_AUTOSCAN_MAX_BITRATE_KBPS = 20_000


class ProxyBuildThread(QThread):
    progress_sec = Signal(float)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, src: Path, dst: Path) -> None:
        super().__init__()
        self.src = src
        self.dst = dst

    def run(self) -> None:
        try:
            build_proxy_mp4(
                self.src,
                self.dst,
                on_progress=lambda t: self.progress_sec.emit(float(t)),
                cancel_check=self.isInterruptionRequested,
            )
            if self.isInterruptionRequested():
                return
            self.succeeded.emit()
        except InterruptedError:
            return
        except Exception as e:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(str(e))


class WaveformThread(QThread):
    succeeded = Signal(object, object, object)
    failed = Signal(str)

    def __init__(
        self,
        video: Path,
        bins: int,
        duration: float,
        out_npz: Path,
        *,
        has_audio: bool,
        sample_rate: int | None = None,
    ) -> None:
        super().__init__()
        self.video = video
        self.bins = bins
        self.duration = duration
        self.out_npz = out_npz
        self.has_audio = has_audio
        self.sample_rate = int(sample_rate) if sample_rate is not None else STREAM_SAMPLE_RATE

    def run(self) -> None:
        try:
            mins, maxs, rms = compute_waveform_peaks(
                self.video,
                self.bins,
                self.duration,
                self.sample_rate,
                has_audio=self.has_audio,
                interrupt_check=self.isInterruptionRequested,
            )
            if self.isInterruptionRequested():
                return
            np.savez_compressed(
                self.out_npz,
                mins=mins,
                maxs=maxs,
                rms=rms,
                meta_bins=np.array([self.bins], dtype=np.int32),
                meta_sr=np.array([self.sample_rate], dtype=np.int32),
                meta_ver=np.array([WAVE_NPZ_VERSION], dtype=np.int32),
            )
            if self.isInterruptionRequested():
                return
            self.succeeded.emit(mins, maxs, rms)
        except InterruptedError:
            return
        except Exception as e:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(str(e))


class KeyframeThread(QThread):
    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def run(self) -> None:
        try:
            times = list_keyframe_times(self.path, cancel_check=self.isInterruptionRequested)
            if self.isInterruptionRequested():
                return
            self.succeeded.emit(times)
        except Exception as e:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(str(e))


class ClipThumbThread(QThread):
    succeeded = Signal(int, int, str, object, object)
    failed = Signal(int, int, str, str)

    def __init__(
        self,
        *,
        src: Path,
        row: int,
        clip_id: str,
        request_id: int,
        start_time_sec: float | None,
        end_time_sec: float | None,
    ) -> None:
        super().__init__()
        self.src = src
        self.row = row
        self.clip_id = clip_id
        self.request_id = request_id
        self.start_time_sec = start_time_sec
        self.end_time_sec = end_time_sec

    def run(self) -> None:
        try:
            start_png: bytes | None = None
            end_png: bytes | None = None
            if self.start_time_sec is not None:
                start_png = extract_frame_png(self.src, self.start_time_sec)
                if self.isInterruptionRequested():
                    return
            if self.end_time_sec is not None:
                end_png = extract_frame_png(self.src, self.end_time_sec)
                if self.isInterruptionRequested():
                    return
            self.succeeded.emit(self.request_id, self.row, self.clip_id, start_png, end_png)
        except Exception as e:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(self.request_id, self.row, self.clip_id, str(e))


class _ExportUIBridge(QObject):
    """Cross-thread bridge: stderr drainer runs on a plain ``threading`` thread (not ``QThread``)."""

    clip_meta = Signal(int, int, int, float)
    stderr_line = Signal(str)


@dataclass(frozen=True)
class ExportTaskOptions:
    export_fps: int
    export_video_codec: str
    export_video_bitrate_kbps: int
    export_video_rate_mode: str
    export_video_quality: int
    export_filename_template: str
    video_filter: str
    align_enabled: bool = True
    align_x: int = 8
    align_y: int = 1
    align_round: str = "ceil"
    align_apply: str = "tail"


class ExportWorker(QObject):
    """Runs ``export_all_clips`` on a worker thread (``moveToThread``); emits progress from this object.

    Emitting ``progress`` from ``QThread.run()`` is unreliable: the ``QThread`` instance lives on the
    GUI thread, so high-frequency emits during ``run()`` can be coalesced or applied late. Signals
    must originate from a ``QObject`` that has been ``moveToThread`` to the worker ``QThread``.
    """

    progress = Signal(float, int, int, int, int)
    succeeded = Signal(list)
    failed = Signal(str)
    cancelled = Signal()
    finished_done = Signal()

    def __init__(
        self,
        data: ProjectData,
        tb: Timebase,
        out_dir: Path,
        warn_non_8n1: bool,
        export_fps: int,
        export_video_codec: str,
        export_video_bitrate_kbps: int,
        export_video_rate_mode: str,
        export_video_quality: int,
        export_filename_template: str,
        video_filter: str,
        align_enabled: bool,
        align_x: int,
        align_y: int,
        align_round: str,
        align_apply: str,
        strong_av_sync: bool,
        cancel_flag: list[bool],
        host_thread: QThread,
        ui_bridge: _ExportUIBridge,
    ) -> None:
        super().__init__()
        self._proj = data
        self._tb = tb
        self._out_dir = out_dir
        self._warn_non_8n1 = bool(warn_non_8n1)
        self._export_fps = max(1, min(240, int(export_fps)))
        self._export_video_codec = str(export_video_codec or "auto").strip().lower()
        self._export_video_bitrate_kbps = max(300, min(200000, int(export_video_bitrate_kbps)))
        self._export_video_rate_mode = str(export_video_rate_mode or "bitrate").strip().lower()
        self._export_video_quality = max(0, min(51, int(export_video_quality)))
        self._export_filename_template = str(export_filename_template or "").strip()
        self._video_filter = str(video_filter)
        self._align_enabled = bool(align_enabled)
        ax = max(1, min(1024, int(align_x)))
        self._align_x = ax
        self._align_y = int(align_y) % ax
        ar = str(align_round or "ceil").strip().lower()
        self._align_round = ar if ar in {"ceil", "floor"} else "ceil"
        aa = str(align_apply or "tail").strip().lower()
        self._align_apply = aa if aa in {"tail", "head", "symmetric"} else "tail"
        self._strong_av_sync = bool(strong_av_sync)
        self._cancel_flag = cancel_flag
        self._host_thread = host_thread
        self._ui_bridge = ui_bridge

    def _cancel_check(self) -> bool:
        return bool(self._cancel_flag[0]) or self._host_thread.isInterruptionRequested()

    @Slot()
    def run_export(self) -> None:
        try:
            export_debug_log("worker.export.run_start")
            try:
                align_c = (
                    AlignConstraint(
                        enabled=True,
                        x=self._align_x,
                        y=self._align_y,
                        round=self._align_round,
                        apply=self._align_apply,
                    )
                    if self._align_enabled
                    else None
                )
                msgs = export_all_clips(
                    self._proj,
                    self._tb,
                    self._out_dir,
                    warn_non_8n1=self._warn_non_8n1,
                    warn_pattern_x=self._align_x,
                    warn_pattern_y=self._align_y,
                    format_predicted_pattern_warn=lambda n, f, x, y: tr(
                        "export.warn_predicted_pattern", length=n, fps=f, x=x, y=y
                    ),
                    export_fps=self._export_fps,
                    export_video_codec=self._export_video_codec,
                    export_video_bitrate_kbps=self._export_video_bitrate_kbps,
                    export_video_rate_mode=self._export_video_rate_mode,
                    export_video_quality=self._export_video_quality,
                    export_filename_template=self._export_filename_template,
                    strong_av_sync=self._strong_av_sync,
                    video_filter=self._video_filter,
                    align_constraint=align_c,
                    format_align_warning=lambda w: tr(w.key),
                    on_progress=lambda o, c, t, f, ft: self.progress.emit(
                        float(o), int(c), int(t), int(f), int(ft)
                    ),
                    cancel_check=self._cancel_check,
                    on_clip_begin=lambda i, n_clips, l, d: self._ui_bridge.clip_meta.emit(
                        int(i + 1), int(n_clips), int(l), float(d)
                    ),
                    stderr_line_hook=lambda ln: self._ui_bridge.stderr_line.emit(ln),
                )
            except InterruptedError:
                self.cancelled.emit()
                return
            except Exception as e:  # noqa: BLE001
                if self._cancel_check():
                    self.cancelled.emit()
                else:
                    self.failed.emit(str(e))
                return
            if self._host_thread.isInterruptionRequested():
                return
            self.succeeded.emit(msgs)
        finally:
            export_debug_log("worker.export.run_finish")
            self.finished_done.emit()


class _ClipThumbLabel(QLabel):
    """首尾帧预览：可点击聚焦；连续两次点同一缩略图或一次双击等效于两次按下 → 跳转到对应帧。"""

    tapped = Signal(str)

    def __init__(self, role: Literal["start", "end"], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._role = role
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.tapped.emit(self._role)
        super().mousePressEvent(event)


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(tr("app.title"))
        self.resize(1440, 880)

        self._settings = AppSettings()
        self._data = ProjectData()
        self._tb = Timebase.from_probe(
            VideoProbe(1.0, 30.0, 2, 2, 30, None, None, 0)
        )
        self._store: ProjectStore | None = None
        self._current_frame = 0
        self._keyframe_times: list[float] = []
        self._keyframes_loaded = False
        self._keyframes_loading = False
        self._keyframes_deferred = False
        self._proxy_thread: ProxyBuildThread | None = None
        self._wave_thread: WaveformThread | None = None
        self._keyframe_thread: KeyframeThread | None = None
        self._thumb_thread: ClipThumbThread | None = None
        self._export_thread: QThread | None = None
        self._export_worker: ExportWorker | None = None
        self._export_progress: QProgressDialog | None = None
        self._export_progress_bridge = _ExportUIBridge(self)
        self._export_progress_bridge.clip_meta.connect(
            self._on_export_clip_meta,
            Qt.ConnectionType.QueuedConnection,
        )
        self._export_progress_bridge.stderr_line.connect(
            self._on_export_stderr_line,
            Qt.ConnectionType.QueuedConnection,
        )
        self._export_clip_ui: tuple[int, int, int, float] | None = None
        self._export_stderr_last_ff: int | None = None
        self._export_debug_last_ui_tick_mono = 0.0
        self._export_ui_last_current = 0
        self._export_ui_last_frame = 0
        self._export_ui_last_overall = 0.0
        self._preroll_seek_token = 0
        self._clip_loop_clip_id: str | None = None
        self._act_export: QAction | None = None
        self._act_export_strong: QAction | None = None
        self._export_action_title = tr("menu.export")
        # True：主预览由 QMediaPlayer+QVideoWidget 解码（源文件或代理）
        self._preview_uses_qt = False
        self._session_gen = 0
        self._last_pos_ui_update_mono = 0.0
        self._last_seek_setpos_mono = 0.0
        self._last_seek_target_ms: int | None = None
        self._pending_seek_frame: int | None = None
        self._seek_after_source_ready = False
        self._view_start = 0
        self._view_span = max(1, self._tb.total_frames)
        self._timeline_clip_drag_dirty = False
        self._thumb_request_id = 0
        self._thumb_cache_max = 256
        self._thumb_pix_cache: OrderedDict[tuple[str, int], QPixmap] = OrderedDict()
        self._thumb_pending_job: tuple[str, int, str, int | None, int | None] | None = None
        self._startup_restore_checked = False

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._audio.setVolume(float(self._settings.preview_volume()))
        # QAudioOutput 会绑定启动时的默认设备；系统切换默认输出时主动对齐（列表未变时未必有 audioOutputsChanged）。
        # 轮询仅在前台或播放中运行，见 _refresh_audio_default_poll_timer。
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(self._sync_audio_output_to_system_default)
        app_inst = QApplication.instance()
        if app_inst is not None:
            app_inst.applicationStateChanged.connect(self._on_application_state_changed)
        self._audio_default_poll = QTimer(self)
        self._audio_default_poll.setTimerType(Qt.TimerType.CoarseTimer)
        self._audio_default_poll.setInterval(2000)
        self._audio_default_poll.timeout.connect(self._sync_audio_output_to_system_default)
        self._video_widget = QVideoWidget(self)
        self._player.setVideoOutput(self._video_widget)
        self._preview_label = QLabel(self)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setStyleSheet("background:#111;color:#888;")
        self._preview_label.hide()
        # QVideoWidget 内部有原生视频层，兄弟 QWidget 会被压在下面；拖放/鼠标由「子控件」接（见 VideoPreviewDropShim）
        self._video_drop_host = QWidget(self)
        _vdh = QGridLayout(self._video_drop_host)
        _vdh.setContentsMargins(0, 0, 0, 0)
        _vdh.addWidget(self._video_widget, 0, 0)
        self._video_preview_shim = VideoPreviewDropShim(self._video_widget, VIDEO_OPEN_SUFFIXES)
        self._video_preview_shim.video_dropped.connect(self._request_load_video_path)
        self._player.positionChanged.connect(self._on_player_position)
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._seek_apply_timer = QTimer(self)
        self._seek_apply_timer.setSingleShot(True)
        self._seek_apply_timer.setInterval(22)
        self._seek_apply_timer.timeout.connect(self._flush_pending_seek)
        self._refresh_audio_default_poll_timer()

        self._clip_list = QListWidget(self)
        self._clip_list.setMinimumWidth(160)
        self._clip_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._clip_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self._clip_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._clip_list.customContextMenuRequested.connect(self._clip_list_context_menu)
        self._clip_list.currentRowChanged.connect(self._on_clip_row)
        self._clip_list.itemPressed.connect(self._on_clip_item_pressed)
        self._clip_list.itemClicked.connect(self._on_clip_item_clicked)

        self._thumb_repeat_target: Literal["start", "end"] | None = None
        self._clip_row_reclick_candidate: int | None = None
        self._source_path_show_full = False
        self._thumb_start = _ClipThumbLabel("start", self)
        self._thumb_end = _ClipThumbLabel("end", self)
        for lb in (self._thumb_start, self._thumb_end):
            lb.setMinimumSize(160, 120)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_start.setText(tr("no_clip"))
        self._thumb_end.setText(tr("no_clip"))
        self._thumb_start.tapped.connect(self._on_clip_thumb_tapped)
        self._thumb_end.tapped.connect(self._on_clip_thumb_tapped)
        self._refresh_thumb_selection_style()

        self._wave = WaveformWidget(self)
        self._wave.set_status_texts(
            tr("waveform.ui.loading"),
            tr("waveform.ui.none"),
            tr("waveform.ui.empty"),
        )
        self._timeline = TimelineWidget(self)
        self._timeline.seek_frame.connect(self._on_timeline_seek)
        self._timeline.reset_view.connect(self._reset_timeline_view)
        self._timeline.crosshair_hover.connect(self._sync_scrub_crosshair)
        self._timeline.clip_select_requested.connect(self._on_timeline_clip_select)
        self._timeline.clip_drag_delta.connect(self._on_timeline_clip_drag_delta)
        self._timeline.clip_drag_finished.connect(self._on_timeline_clip_drag_finished)
        self._wave.crosshair_hover.connect(self._sync_scrub_crosshair)
        self._wave.seek_frame.connect(self._on_waveform_seek)

        self._btn_prev_kf = QPushButton("<<")
        self._btn_prev_fr = QPushButton("<")
        self._btn_play = QPushButton("▶")
        self._btn_next_fr = QPushButton(">")
        self._btn_next_kf = QPushButton(">>")
        self._btn_clip_loop = QPushButton()
        self._btn_clip_loop.setCheckable(True)
        self._btn_timeline_left = QPushButton(tr("transport.btn.timeline_left"))
        self._btn_timeline_right = QPushButton(tr("transport.btn.timeline_right"))
        self._btn_zoom_out = QPushButton(tr("transport.btn.zoom_out"))
        self._btn_zoom_in = QPushButton(tr("transport.btn.zoom_in"))
        self._btn_zoom_reset = QPushButton(tr("transport.btn.zoom_reset"))
        self._btn_mark_start = QPushButton(tr("transport.btn.start"))
        self._btn_mark_end = QPushButton(tr("transport.btn.end"))
        self._btn_mark_snap_ceil = QPushButton(tr("transport.btn.snap_ceil"))
        self._btn_mark_snap_floor = QPushButton(tr("transport.btn.snap_floor"))
        self._btn_clip_nudge_left = QPushButton(tr("transport.btn.clip_nudge_left"))
        self._btn_clip_nudge_left.setEnabled(False)
        self._btn_clip_nudge_right = QPushButton(tr("transport.btn.clip_nudge_right"))
        self._btn_clip_nudge_right.setEnabled(False)
        self._btn_boundary_nudge_left = QPushButton(tr("transport.btn.boundary_nudge_left"))
        self._btn_boundary_nudge_left.setEnabled(False)
        self._btn_boundary_nudge_right = QPushButton(tr("transport.btn.boundary_nudge_right"))
        self._btn_boundary_nudge_right.setEnabled(False)
        self._btn_quick_slice_1 = QPushButton("1")
        self._btn_quick_slice_2 = QPushButton("2")
        self._btn_quick_slice_3 = QPushButton("3")
        self._btn_quick_slice_4 = QPushButton("4")
        self._btn_quick_slice_5 = QPushButton("5")
        for b in (
            self._btn_prev_kf,
            self._btn_prev_fr,
            self._btn_play,
            self._btn_next_fr,
            self._btn_next_kf,
            self._btn_clip_loop,
        ):
            b.setFixedSize(40, 32)
        for b in (
            self._btn_timeline_left,
            self._btn_timeline_right,
            self._btn_zoom_out,
            self._btn_zoom_in,
            self._btn_zoom_reset,
        ):
            b.setFixedSize(34, 30)
        for b in (
            self._btn_mark_start,
            self._btn_mark_end,
        ):
            b.setFixedSize(88, 30)
        for b in (
            self._btn_quick_slice_1,
            self._btn_quick_slice_2,
            self._btn_quick_slice_3,
            self._btn_quick_slice_4,
            self._btn_quick_slice_5,
        ):
            b.setFixedSize(48, 30)
        for b in (
            self._btn_mark_snap_ceil,
            self._btn_mark_snap_floor,
        ):
            b.setFixedSize(86, 30)
        for b in (
            self._btn_clip_nudge_left,
            self._btn_clip_nudge_right,
            self._btn_boundary_nudge_left,
            self._btn_boundary_nudge_right,
        ):
            b.setFixedSize(70, 30)
        for b in (self._btn_boundary_nudge_left, self._btn_boundary_nudge_right):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_prev_kf.clicked.connect(lambda: self._skip_playback_seconds(-1))
        self._btn_prev_fr.clicked.connect(lambda: self._step_frame(-1))
        self._btn_play.clicked.connect(self._toggle_play)
        self._btn_next_fr.clicked.connect(lambda: self._step_frame(1))
        self._btn_next_kf.clicked.connect(lambda: self._skip_playback_seconds(1))
        self._btn_clip_loop.clicked.connect(self._toggle_clip_loop)
        self._btn_timeline_left.clicked.connect(lambda: self._pan_timeline(move_left=True))
        self._btn_timeline_right.clicked.connect(lambda: self._pan_timeline(move_left=False))
        self._btn_zoom_out.clicked.connect(lambda: self._zoom_timeline_from_shortcut(zoom_in=False))
        self._btn_zoom_in.clicked.connect(lambda: self._zoom_timeline_from_shortcut(zoom_in=True))
        self._btn_zoom_reset.clicked.connect(self._reset_timeline_view)
        self._btn_mark_start.clicked.connect(self._shortcut_start)
        self._btn_mark_end.clicked.connect(self._shortcut_end)
        self._btn_mark_snap_ceil.clicked.connect(lambda: self._snap_8n1(True))
        self._btn_mark_snap_floor.clicked.connect(lambda: self._snap_8n1(False))
        self._btn_clip_nudge_left.clicked.connect(lambda: self._nudge_selected_clip_frames(-1))
        self._btn_clip_nudge_right.clicked.connect(lambda: self._nudge_selected_clip_frames(1))
        self._btn_boundary_nudge_left.clicked.connect(lambda: self._nudge_boundary_frames(-1))
        self._btn_boundary_nudge_right.clicked.connect(lambda: self._nudge_boundary_frames(1))
        self._btn_quick_slice_1.clicked.connect(lambda: self._shortcut_quick_slice(1))
        self._btn_quick_slice_2.clicked.connect(lambda: self._shortcut_quick_slice(2))
        self._btn_quick_slice_3.clicked.connect(lambda: self._shortcut_quick_slice(3))
        self._btn_quick_slice_4.clicked.connect(lambda: self._shortcut_quick_slice(4))
        self._btn_quick_slice_5.clicked.connect(lambda: self._shortcut_quick_slice(5))
        self._apply_transport_button_icons()
        self._refresh_transport_tooltips()
        self._shortcuts: list[QShortcut] = []

        transport = QHBoxLayout()
        transport.addWidget(self._btn_timeline_left)
        transport.addWidget(self._btn_timeline_right)
        transport.addStretch()
        transport.addWidget(self._btn_prev_kf)
        transport.addWidget(self._btn_prev_fr)
        transport.addWidget(self._btn_play)
        transport.addWidget(self._btn_next_fr)
        transport.addWidget(self._btn_next_kf)
        transport.addWidget(self._btn_clip_loop)
        transport.addStretch()
        transport.addWidget(self._btn_zoom_out)
        transport.addWidget(self._btn_zoom_in)
        transport.addWidget(self._btn_zoom_reset)

        mark_row = QHBoxLayout()
        mark_row.addStretch()
        mark_row.addWidget(self._btn_mark_start)
        mark_row.addWidget(self._btn_mark_end)
        mark_row.addWidget(self._btn_mark_snap_ceil)
        mark_row.addWidget(self._btn_mark_snap_floor)
        mark_row.addWidget(self._btn_clip_nudge_left)
        mark_row.addWidget(self._btn_clip_nudge_right)
        mark_row.addWidget(self._btn_boundary_nudge_left)
        mark_row.addWidget(self._btn_boundary_nudge_right)
        mark_row.addStretch()

        right_thumb = QVBoxLayout()
        thumb_head = QHBoxLayout()
        self._thumbs_header_label = QLabel(tr("thumbs"))
        thumb_head.addWidget(self._thumbs_header_label)
        thumb_head.addStretch(1)
        self._thumb_duration_label = QLabel(tr("thumbs.duration_placeholder"), self)
        self._thumb_duration_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._thumb_duration_label.setStyleSheet("color:#b8b8b8;")
        thumb_head.addWidget(self._thumb_duration_label)
        right_thumb.addLayout(thumb_head)
        right_thumb.addWidget(self._thumb_start)
        right_thumb.addWidget(self._thumb_end)
        right_thumb.addStretch(1)
        self._preview_volume_label = QLabel(tr("preview.volume"))
        right_thumb.addWidget(self._preview_volume_label)
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setMouseTracking(True)
        self._vol_slider.installEventFilter(self)
        self._vol_slider.setValue(int(round(self._settings.preview_volume() * 100)))
        self._vol_slider.valueChanged.connect(self._on_preview_volume_changed)
        self._vol_slider.sliderPressed.connect(self._on_vol_slider_pressed)
        self._vol_slider.sliderReleased.connect(self._on_vol_slider_released)
        right_thumb.addWidget(self._vol_slider)
        self._vol_tooltip_timer = QTimer(self)
        self._vol_tooltip_timer.setSingleShot(True)
        self._vol_tooltip_timer.setInterval(900)
        self._vol_tooltip_timer.timeout.connect(self._hide_vol_tooltip)
        self._vol_slider_pressed = False
        self._vol_mouse_over_slider = False
        right_thumb.addSpacing(8)

        center_stack = QVBoxLayout()
        self._source_path_label = _ClickableLabel(self)
        self._source_path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._source_path_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._source_path_label.setStyleSheet("color:#a8a8a8; padding: 2px 0;")
        self._source_path_label.clicked.connect(self._toggle_source_path_display)
        self._source_path_label.setToolTip(tr("source_path.toggle_tooltip"))
        self._refresh_source_path_label()
        center_stack.addWidget(self._source_path_label)
        center_stack.addWidget(self._video_drop_host, stretch=1)
        center_stack.addWidget(self._preview_label, stretch=1)
        self._center_drop_widget = QWidget()
        self._center_drop_widget.setLayout(center_stack)
        self._center_drop_widget.setMinimumWidth(520)

        self._left_drop_widget = QWidget()
        self._left_drop_widget.setMinimumWidth(180)
        left_lay = QVBoxLayout(self._left_drop_widget)
        clip_title_row = QWidget(self._left_drop_widget)
        clip_title_lay = QHBoxLayout(clip_title_row)
        clip_title_lay.setContentsMargins(0, 0, 0, 0)
        clip_title_lay.setSpacing(6)
        self._clips_header_label = QLabel(tr("clips"), clip_title_row)
        clip_title_lay.addWidget(self._clips_header_label)
        clip_title_lay.addStretch(1)
        self._btn_clip_list_mode = QPushButton(clip_title_row)
        self._btn_clip_list_mode.setFixedHeight(24)
        self._btn_clip_list_mode.clicked.connect(self._on_clip_list_display_toggle)
        self._sync_clip_list_display_button()
        clip_title_lay.addWidget(self._btn_clip_list_mode)
        left_lay.addWidget(clip_title_row)
        left_lay.addWidget(self._clip_list)

        top = QSplitter(self)
        top.addWidget(self._left_drop_widget)
        top.addWidget(self._center_drop_widget)
        self._right_drop_widget = QWidget()
        self._right_drop_widget.setLayout(right_thumb)
        self._right_drop_widget.setMaximumWidth(200)
        top.addWidget(self._right_drop_widget)
        top.setStretchFactor(1, 1)
        # Prevent any top pane from being collapsed to zero width by dragging.
        top.setChildrenCollapsible(False)
        top.setCollapsible(0, False)
        top.setCollapsible(1, False)
        top.setCollapsible(2, False)
        self._top_split = top

        self._preview_wave_split = QSplitter(Qt.Orientation.Vertical, self)
        self._preview_wave_split.addWidget(top)
        self._preview_wave_split.addWidget(self._wave)
        self._preview_wave_split.setStretchFactor(0, 3)
        self._preview_wave_split.setStretchFactor(1, 2)
        self._preview_wave_split.setHandleWidth(8)
        self._preview_wave_split.setCollapsible(0, False)
        self._preview_wave_split.setCollapsible(1, False)
        self._preview_wave_split.setStyleSheet(
            "QSplitter::handle { background: #4a4a52; } "
            "QSplitter::handle:hover { background: #5c8a9a; }"
        )
        top.setMinimumHeight(120)
        self._pw_split_sizes_inited = False
        self._pw_split_save_timer = QTimer(self)
        self._pw_split_save_timer.setSingleShot(True)
        self._pw_split_save_timer.timeout.connect(self._save_preview_wave_split_sizes)
        self._preview_wave_split.splitterMoved.connect(
            lambda _pos, _idx: self._pw_split_save_timer.start(400)
        )

        root = QVBoxLayout()
        root.addWidget(self._preview_wave_split, stretch=5)
        root.addWidget(self._timeline)
        root.addLayout(transport)
        root.addLayout(mark_row)
        self._btn_export_sidebar = QPushButton(tr("toolbar.export_all"))
        self._btn_export_sidebar.clicked.connect(self._export_clips)
        export_row = QHBoxLayout()
        export_row.addStretch()
        export_row.addWidget(self._btn_quick_slice_1)
        export_row.addWidget(self._btn_quick_slice_2)
        export_row.addWidget(self._btn_quick_slice_3)
        export_row.addWidget(self._btn_quick_slice_4)
        export_row.addWidget(self._btn_quick_slice_5)
        export_row.addStretch()
        export_row.addWidget(self._btn_export_sidebar)
        root.addLayout(export_row)

        cw = QWidget()
        cw.setLayout(root)
        self.setCentralWidget(cw)
        self._drop_targets = (
            self._left_drop_widget,
            self._center_drop_widget,
            self._preview_label,
            self._right_drop_widget,
            self._thumb_start,
            self._thumb_end,
            self._vol_slider,
            self._btn_export_sidebar,
            self._clip_list,
            self._wave,
            self._timeline,
        )
        for w in self._drop_targets:
            w.setAcceptDrops(True)
            w.installEventFilter(self)

        QTimer.singleShot(0, self._raise_video_preview_shim)
        QTimer.singleShot(0, self._refresh_min_window_size)

        self._build_menu()
        self._install_shortcuts()

        _app = QApplication.instance()
        if _app is not None:
            _app.focusChanged.connect(self._on_app_focus_changed_clear_thumb_arm)
            _app.installEventFilter(self)

        self._status_label = QLabel("")
        self.statusBar().addPermanentWidget(self._status_label)

    def _build_menu(self) -> None:
        m_file = self.menuBar().addMenu(tr("menu.file"))
        act_open = QAction(tr("menu.open"), self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self._open_video)
        m_file.addAction(act_open)
        act_proxy = QAction(tr("menu.generate_proxy"), self)
        act_proxy.triggered.connect(self._generate_proxy_clicked)
        m_file.addAction(act_proxy)
        act_clear_proxy = QAction(tr("menu.clear_proxy"), self)
        act_clear_proxy.triggered.connect(self._clear_all_proxies)
        m_file.addAction(act_clear_proxy)
        self._act_export = QAction(tr("menu.export"), self)
        self._act_export.triggered.connect(self._export_clips)
        m_file.addAction(self._act_export)
        self._act_export_strong = QAction(tr("menu.export_strong"), self)
        self._act_export_strong.triggered.connect(self._export_clips_strong)
        m_file.addAction(self._act_export_strong)
        m_file.addSeparator()
        act_quit = QAction(tr("menu.quit"), self)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_edit = self.menuBar().addMenu(tr("menu.edit"))
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

        m_settings = self.menuBar().addMenu(tr("menu.settings"))
        act_set = QAction(tr("menu.settings"), self)
        act_set.triggered.connect(self._show_settings)
        m_settings.addAction(act_set)

        m_help = self.menuBar().addMenu(tr("menu.help"))
        act_about = QAction(tr("menu.about"), self)
        act_about.triggered.connect(self._about)
        m_help.addAction(act_about)

    def _install_shortcuts(self) -> None:
        def _bind(seq: QKeySequence, context: Qt.ShortcutContext, fn: Any) -> None:
            sc = QShortcut(seq, self)
            sc.setContext(context)
            sc.activated.connect(fn)
            self._shortcuts.append(sc)

        for key, fn in (
            ("S", self._shortcut_start),
            ("E", self._shortcut_end),
            ("C", lambda: self._snap_8n1(True)),
            ("F", lambda: self._snap_8n1(False)),
            ("L", self._toggle_clip_loop),
            ("A", lambda: self._nudge_selected_clip_frames(-1)),
            ("D", lambda: self._nudge_selected_clip_frames(1)),
            ("Z", lambda: self._nudge_boundary_frames(-1)),
            ("X", lambda: self._nudge_boundary_frames(1)),
        ):
            _bind(QKeySequence(key), Qt.ShortcutContext.ApplicationShortcut, fn)
        for digit in range(1, 6):
            _bind(
                QKeySequence(str(digit)),
                Qt.ShortcutContext.ApplicationShortcut,
                lambda d=digit: self._shortcut_quick_slice(d),
            )
        for key, delta in ((Qt.Key.Key_Left, -1), (Qt.Key.Key_Right, 1)):
            _bind(
                QKeySequence(key),
                Qt.ShortcutContext.WindowShortcut,
                lambda d=delta: self._arrow_horizontal_transport(d),
            )
        for key, vol_delta in ((Qt.Key.Key_Up, 5), (Qt.Key.Key_Down, -5)):
            _bind(
                QKeySequence(key),
                Qt.ShortcutContext.WindowShortcut,
                lambda d=vol_delta: self._adjust_volume(d),
            )
        for seq in (
            QKeySequence(QKeySequence.StandardKey.Delete),
            QKeySequence(Qt.Key.Key_Backspace),
        ):
            _bind(seq, Qt.ShortcutContext.WindowShortcut, self._delete_selected_clip)
        _bind(QKeySequence(Qt.Key.Key_Space), Qt.ShortcutContext.WindowShortcut, self._toggle_play)
        for seq in (
            QKeySequence(","),
            QKeySequence(Qt.Key.Key_Comma),
        ):
            _bind(seq, Qt.ShortcutContext.ApplicationShortcut, lambda: self._pan_timeline(move_left=True))
        for seq in (
            QKeySequence("."),
            QKeySequence(Qt.Key.Key_Period),
        ):
            _bind(seq, Qt.ShortcutContext.ApplicationShortcut, lambda: self._pan_timeline(move_left=False))
        for seq in (
            QKeySequence("-"),
            QKeySequence(Qt.Key.Key_Minus),
        ):
            _bind(seq, Qt.ShortcutContext.ApplicationShortcut, lambda: self._zoom_timeline_from_shortcut(zoom_in=False))
        for seq in (
            QKeySequence("="),
            QKeySequence("+"),
            QKeySequence(Qt.Key.Key_Equal),
            QKeySequence(Qt.Key.Key_Plus),
        ):
            _bind(seq, Qt.ShortcutContext.ApplicationShortcut, lambda: self._zoom_timeline_from_shortcut(zoom_in=True))
        for seq in (
            QKeySequence("0"),
            QKeySequence(Qt.Key.Key_0),
            QKeySequence("Num+0"),
        ):
            _bind(seq, Qt.ShortcutContext.ApplicationShortcut, self._reset_timeline_view)
        _bind(
            QKeySequence("Ctrl+Shift+Z"),
            Qt.ShortcutContext.ApplicationShortcut,
            self._redo,
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        _app = QApplication.instance()
        if _app is not None:
            try:
                _app.focusChanged.disconnect(self._on_app_focus_changed_clear_thumb_arm)
            except TypeError:
                pass
            _app.removeEventFilter(self)
        self._pw_split_save_timer.stop()
        self._save_preview_wave_split_sizes()
        self._save_project()
        if self._export_worker:
            try:
                self._export_worker.disconnect()
            except TypeError:
                pass
        if self._export_thread:
            try:
                self._export_thread.disconnect()
            except TypeError:
                pass
        if self._export_progress is not None:
            self._export_progress.close()
            self._export_progress.deleteLater()
            self._export_progress = None
        for t in (
            self._proxy_thread,
            self._wave_thread,
            self._keyframe_thread,
            self._thumb_thread,
            self._export_thread,
        ):
            if t and t.isRunning():
                t.requestInterruption()
        for t in (
            self._proxy_thread,
            self._wave_thread,
            self._keyframe_thread,
            self._thumb_thread,
            self._export_thread,
        ):
            if t and t.isRunning():
                if not t.wait(10_000):
                    t.terminate()
                    t.wait(3000)
        self._export_thread = None
        self._export_worker = None
        event.accept()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._maybe_init_preview_wave_split_sizes()
        if not self._startup_restore_checked:
            self._startup_restore_checked = True
            QTimer.singleShot(0, self._maybe_restore_last_project_on_startup)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._maybe_init_preview_wave_split_sizes()
        self._raise_video_preview_shim()
        if not self._preview_uses_qt:
            self._refresh_frame_preview()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if isinstance(event, QKeyEvent) and self._handle_transport_keypress(event):
            return
        super().keyPressEvent(event)

    def _maybe_init_preview_wave_split_sizes(self) -> None:
        if self._pw_split_sizes_inited:
            return
        sp = self._preview_wave_split
        h = sp.height()
        if h < 80:
            return
        self._pw_split_sizes_inited = True
        saved = self._settings.preview_wave_split_sizes()
        if saved and saved[0] >= 100 and saved[1] >= 80:
            sp.setSizes([saved[0], saved[1]])
        else:
            top_h = max(120, int(h * 0.58))
            sp.setSizes([top_h, max(140, h - top_h)])

    def _save_preview_wave_split_sizes(self) -> None:
        sizes = self._preview_wave_split.sizes()
        if len(sizes) == 2 and sizes[0] > 0 and sizes[1] > 0:
            self._settings.set_preview_wave_split_sizes(sizes[0], sizes[1])

    def _make_text_icon(self, text: str) -> QIcon:
        px = 20
        pix = QPixmap(px, px)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QColor("#E6E6E6"))
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(12 if len(text) == 1 else 10)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        return QIcon(pix)

    def _apply_transport_button_icons(self) -> None:
        st = self.style()
        self._btn_prev_kf.setIcon(self._make_text_icon("<<"))
        self._btn_prev_fr.setIcon(self._make_text_icon("|<"))
        self._btn_play.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self._btn_next_fr.setIcon(self._make_text_icon(">|"))
        self._btn_next_kf.setIcon(self._make_text_icon(">>"))
        self._btn_clip_loop.setIcon(self._make_text_icon("⟲"))

        self._btn_timeline_left.setIcon(self._make_text_icon("⇤"))
        self._btn_timeline_right.setIcon(self._make_text_icon("⇥"))
        self._btn_zoom_out.setIcon(self._make_text_icon("－"))
        self._btn_zoom_in.setIcon(self._make_text_icon("＋"))
        self._btn_zoom_reset.setIcon(self._make_text_icon("↺"))

        self._btn_mark_snap_ceil.setIcon(QIcon())
        self._btn_mark_snap_floor.setIcon(QIcon())
        self._btn_mark_snap_ceil.setText(tr("transport.btn.snap_ceil"))
        self._btn_mark_snap_floor.setText(tr("transport.btn.snap_floor"))
        self._btn_clip_nudge_left.setIcon(QIcon())
        self._btn_clip_nudge_right.setIcon(QIcon())
        self._btn_clip_nudge_left.setText(tr("transport.btn.clip_nudge_left"))
        self._btn_clip_nudge_right.setText(tr("transport.btn.clip_nudge_right"))
        self._btn_boundary_nudge_left.setIcon(QIcon())
        self._btn_boundary_nudge_right.setIcon(QIcon())
        self._btn_boundary_nudge_left.setText(tr("transport.btn.boundary_nudge_left"))
        self._btn_boundary_nudge_right.setText(tr("transport.btn.boundary_nudge_right"))
        self._btn_mark_start.setIcon(QIcon())
        self._btn_mark_end.setIcon(QIcon())


        for b in (
            self._btn_prev_kf,
            self._btn_prev_fr,
            self._btn_play,
            self._btn_next_fr,
            self._btn_next_kf,
            self._btn_clip_loop,
            self._btn_timeline_left,
            self._btn_timeline_right,
            self._btn_zoom_out,
            self._btn_zoom_in,
            self._btn_zoom_reset,
        ):
            b.setText("")
            b.setIconSize(QSize(max(16, int(b.width() * 0.58)), max(16, int(b.height() * 0.58))))

    def _refresh_transport_tooltips(self) -> None:
        z = self._settings.playback_seek_seconds()
        self._btn_prev_kf.setToolTip(tr("transport.tip.seek_back", seconds=z))
        self._btn_prev_fr.setToolTip(tr("transport.tip.prev_frame"))
        self._btn_play.setToolTip(tr("transport.tip.play_toggle"))
        self._btn_next_fr.setToolTip(tr("transport.tip.next_frame"))
        self._btn_next_kf.setToolTip(tr("transport.tip.seek_forward", seconds=z))
        self._btn_clip_loop.setToolTip(tr("transport.tip.loop_clip"))
        self._btn_timeline_left.setToolTip(tr("transport.tip.timeline_left"))
        self._btn_timeline_right.setToolTip(tr("transport.tip.timeline_right"))
        self._btn_zoom_out.setToolTip(tr("transport.tip.zoom_out"))
        self._btn_zoom_in.setToolTip(tr("transport.tip.zoom_in"))
        self._btn_zoom_reset.setToolTip(tr("transport.tip.zoom_reset"))
        self._btn_mark_start.setToolTip(tr("transport.tip.start"))
        self._btn_mark_end.setToolTip(tr("transport.tip.end"))
        self._btn_mark_snap_ceil.setToolTip(tr("transport.tip.snap_ceil"))
        self._btn_mark_snap_floor.setToolTip(tr("transport.tip.snap_floor"))
        for digit, btn in [
            (1, self._btn_quick_slice_1),
            (2, self._btn_quick_slice_2),
            (3, self._btn_quick_slice_3),
            (4, self._btn_quick_slice_4),
            (5, self._btn_quick_slice_5),
        ]:
            b, a = self._settings.quick_slice_extend(digit)
            btn.setToolTip(tr("transport.tip.quick_slice", digit=digit, before=b, after=a))
        self._refresh_clip_nudge_controls()
        self._refresh_boundary_nudge_controls()
        self._refresh_clip_loop_controls()

    def _refresh_ui_language(self) -> None:
        """Refresh all translatable UI text after a language change."""
        # Menus — clear and rebuild
        mb = self.menuBar()
        mb.clear()
        self._build_menu()

        # Transport buttons: _apply_transport_button_icons handles text for
        # snap_ceil/floor, clip_nudge_*, boundary_nudge_* via tr().
        # It only clears icons (not text) for mark_start/end — set those after.
        self._apply_transport_button_icons()
        self._btn_mark_start.setText(tr("transport.btn.start"))
        self._btn_mark_end.setText(tr("transport.btn.end"))

        # Sidebar
        self._btn_export_sidebar.setText(tr("toolbar.export_all"))
        self._thumbs_header_label.setText(tr("thumbs"))
        self._preview_volume_label.setText(tr("preview.volume"))
        self._clips_header_label.setText(tr("clips"))

        # Labels and tooltips
        self._source_path_label.setToolTip(tr("source_path.toggle_tooltip"))
        self._sync_clip_list_display_button()
        self._refresh_clip_list()
        self._refresh_selected_clip_duration_label()

        # Tooltips and dynamic state
        self._refresh_transport_tooltips()
        self._refresh_undo_menu_state()
        self.setWindowTitle(tr("app.title"))

    def _set_clip_loop_button_checked(self, checked: bool) -> None:
        self._btn_clip_loop.blockSignals(True)
        try:
            self._btn_clip_loop.setChecked(checked)
        finally:
            self._btn_clip_loop.blockSignals(False)

    def _can_start_clip_loop_on_selection(self) -> bool:
        if not self._data.source_path:
            return False
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._data.clips):
            return False
        c = self._data.clips[row]
        return c.end_frame is not None

    def _refresh_clip_loop_controls(self) -> None:
        self._invalidate_clip_loop_if_stale()
        sel_ok = self._can_start_clip_loop_on_selection()
        self._btn_clip_loop.setEnabled(sel_ok)
        self._set_clip_loop_button_checked(self._clip_loop_clip_id is not None)

    def _invalidate_clip_loop_if_stale(self) -> None:
        cid = self._clip_loop_clip_id
        if cid is None:
            return
        bounds = self._clip_loop_bounds_for_id(cid)
        row = self._clip_list.currentRow()
        sel_match = (
            0 <= row < len(self._data.clips)
            and self._data.clips[row].id == cid
            and self._data.clips[row].end_frame is not None
        )
        if bounds is None or not sel_match:
            self._clip_loop_clip_id = None

    def _clip_loop_bounds_for_id(self, clip_id: str) -> tuple[int, int] | None:
        for c in self._data.clips:
            if c.id == clip_id and c.end_frame is not None:
                return int(c.start_frame), int(c.end_frame)
        return None

    def _clip_loop_bounds_active(self) -> tuple[int, int] | None:
        cid = self._clip_loop_clip_id
        if not cid:
            return None
        return self._clip_loop_bounds_for_id(cid)

    def _clear_clip_loop(self) -> None:
        self._clip_loop_clip_id = None
        self._refresh_clip_loop_controls()

    def _clip_loop_maybe_wrap_playhead_frame(self, t_sec: float) -> int | None:
        """若解码时钟已越过当前循环片段的最后一帧区间，返回到起始帧索引。"""
        cid = self._clip_loop_clip_id
        if cid is None or not self._data.source_path:
            return None
        b = self._clip_loop_bounds_for_id(cid)
        if b is None:
            self._clear_clip_loop()
            return None
        s, e = b
        fps = max(self._tb.fps, 1e-6)
        # 片段 [s,e] 的最后一帧在时间轴占据约 [e/fps, (e+1)/fps)，仅比较 round 帧号
        # 易在刚过尾帧瞬间仍读到 e ——改用越界时间与帧号并列判断。
        t_after_last = float(e + 1) / fps
        if self._tb.duration_sec > 0:
            t_after_last = min(t_after_last, float(self._tb.duration_sec))
        eps_t = 1.0 / max(fps * 8.0, 24.0)  # ~1/8 帧，避免卡在舍入边界
        # 仅用「已过尾帧」判定：不要用 f<s / t<t_start——positionChanged 常会短暂回报偏低
        # 的旧位置，那会反复 set_current_frame(s)，若毫秒去重则跳过 seek，就会出现
        # 黄线卡在片段首帧、画面却一直往后走的假死。
        f = self._tb.time_to_frame(t_sec)
        if f > e:
            return s
        if t_sec + eps_t >= t_after_last:
            return s
        return None

    def _clip_loop_restart_tail_at_stream_end(self) -> None:
        """片尾与逻辑时间轴最后一帧对齐时：播到 EOS 可能不会再来 position，须在此回到片段起点并继续播放。"""
        cid = self._clip_loop_clip_id
        if cid is None or not self._data.source_path:
            return
        b = self._clip_loop_bounds_for_id(cid)
        if b is None:
            self._clear_clip_loop()
            return
        s, e = b
        self._invalidate_clip_loop_if_stale()
        if self._clip_loop_clip_id is None:
            return
        self._last_seek_target_ms = None
        self._last_seek_setpos_mono = 0.0
        self.set_current_frame(s)
        self._flush_pending_seek()

        def _resume() -> None:
            if self._clip_loop_clip_id is None or not self._data.source_path:
                return
            self._player.play()

        QTimer.singleShot(0, _resume)

    def _clip_loop_clamp_playhead_if_oob(self) -> None:
        if self._clip_loop_clip_id is None:
            return
        b = self._clip_loop_bounds_active()
        if b is None:
            self._clear_clip_loop()
            return
        s, e = b
        cf = self._current_frame
        if cf < s or cf > e:
            was_playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            self.set_current_frame(s)
            if was_playing:
                self._player.play()

    def _activate_clip_loop(self) -> None:
        if not self._can_start_clip_loop_on_selection():
            self._refresh_clip_loop_controls()
            return
        row = self._clip_list.currentRow()
        self._clip_loop_clip_id = self._data.clips[row].id
        self._clip_loop_clamp_playhead_if_oob()
        self._refresh_clip_loop_controls()

    def _toggle_clip_loop(self) -> None:
        if not self._data.source_path:
            self._clear_clip_loop()
            return
        if self._clip_loop_clip_id is not None:
            self._clear_clip_loop()
            return
        self._activate_clip_loop()

    def _refresh_clip_nudge_controls(self) -> None:
        ok = self._can_nudge_selected_clip()
        self._btn_clip_nudge_left.setEnabled(ok)
        self._btn_clip_nudge_right.setEnabled(ok)
        if ok:
            self._btn_clip_nudge_left.setToolTip(tr("transport.tip.clip_nudge_left"))
            self._btn_clip_nudge_right.setToolTip(tr("transport.tip.clip_nudge_right"))
        else:
            tip = tr("transport.tip.clip_nudge_disabled")
            self._btn_clip_nudge_left.setToolTip(tip)
            self._btn_clip_nudge_right.setToolTip(tip)

    def _can_nudge_selected_clip(self) -> bool:
        if not self._data.source_path:
            return False
        row = self._clip_list.currentRow()
        return 0 <= row < len(self._data.clips)

    def _handle_transport_keypress(self, event: QKeyEvent) -> bool:
        if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            return False
        # Do not steal keys when modal dialogs (e.g., settings) are active.
        active_modal = QApplication.activeModalWidget()
        if active_modal is not None and active_modal is not self:
            return False
        key = event.key()
        text = event.text()
        mods = event.modifiers()
        blocking_mods = (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        )
        if mods & blocking_mods:
            return False
        if key == Qt.Key.Key_Comma or text == ",":
            self._pan_timeline(move_left=True)
            event.accept()
            return True
        if key == Qt.Key.Key_Period or text == ".":
            self._pan_timeline(move_left=False)
            event.accept()
            return True
        if key == Qt.Key.Key_Minus or text == "-":
            self._zoom_timeline_from_shortcut(zoom_in=False)
            event.accept()
            return True
        if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus) or text in ("=", "+"):
            self._zoom_timeline_from_shortcut(zoom_in=True)
            event.accept()
            return True
        if key == Qt.Key.Key_0 or text == "0":
            self._reset_timeline_view()
            event.accept()
            return True

        if key == Qt.Key.Key_Up:
            self._adjust_volume(5)
            event.accept()
            return True
        if key == Qt.Key.Key_Down:
            self._adjust_volume(-5)
            event.accept()
            return True

        if key == Qt.Key.Key_A or text.lower() == "a":
            if self._can_nudge_selected_clip():
                self._nudge_selected_clip_frames(-1)
                event.accept()
                return True
            return False
        if key == Qt.Key.Key_D or text.lower() == "d":
            if self._can_nudge_selected_clip():
                self._nudge_selected_clip_frames(1)
                event.accept()
                return True
            return False
        if key == Qt.Key.Key_Z or text.lower() == "z":
            if self._can_nudge_boundary():
                self._nudge_boundary_frames(-1)
                event.accept()
                return True
            return False
        if key == Qt.Key.Key_X or text.lower() == "x":
            if self._can_nudge_boundary():
                self._nudge_boundary_frames(1)
                event.accept()
                return True
            return False
        return False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not hasattr(self, "_drop_targets"):
            return super().eventFilter(watched, event)
        if isinstance(event, QKeyEvent):
            if event.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
                focus = QApplication.focusWidget()
                if watched is focus or watched is self:
                    if self._handle_transport_keypress(event):
                        return True
        if watched is self._vol_slider:
            if event.type() == QEvent.Type.Enter:
                self._vol_mouse_over_slider = True
                self._vol_tooltip_timer.stop()
                self._show_vol_tooltip()
                return True
            if event.type() == QEvent.Type.Leave:
                self._vol_mouse_over_slider = False
                if not self._vol_slider_pressed:
                    self._hide_vol_tooltip()
                return True
        if watched in self._drop_targets:
            if isinstance(event, QWheelEvent) and watched in (self._timeline, self._wave):
                if not self._data.source_path:
                    return super().eventFilter(watched, event)
                mods = event.modifiers()
                if mods & Qt.KeyboardModifier.ControlModifier:
                    self._zoom_timeline_from_wheel(event, watched)
                    event.accept()
                    return True
                if (
                    mods & Qt.KeyboardModifier.ShiftModifier
                    and self._view_span < self._tb.total_frames
                ):
                    self._pan_timeline_from_wheel(event)
                    event.accept()
                    return True
            if isinstance(event, QDragEnterEvent):
                if self._first_video_path_from_mime(event.mimeData()) is not None:
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return True
            if isinstance(event, QDragMoveEvent):
                if self._first_video_path_from_mime(event.mimeData()) is not None:
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return True
            if isinstance(event, QDropEvent):
                path = self._first_video_path_from_mime(event.mimeData())
                if path is not None:
                    event.acceptProposedAction()
                    self._request_load_video_path(path)
                else:
                    event.ignore()
                return True
        return super().eventFilter(watched, event)

    def _first_video_path_from_mime(self, md: QMimeData) -> Path | None:
        if not md.hasUrls():
            return None
        for u in md.urls():
            if not u.isLocalFile():
                continue
            p = Path(u.toLocalFile())
            if p.is_file() and p.suffix.lower() in VIDEO_OPEN_SUFFIXES:
                return p
        return None

    def _request_load_video_path(self, source: Path) -> None:
        source = source.resolve()
        if self._data.source_path:
            self._save_project()
        try:
            self._load_video_path(source)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("open_video"), str(e))

    def _stop_threads_for_video_switch(self) -> None:
        """Best-effort stop of per-video workers before replacing thread references."""
        for attr in ("_proxy_thread", "_wave_thread", "_keyframe_thread", "_thumb_thread"):
            t = getattr(self, attr, None)
            if t is None:
                continue
            if t.isRunning():
                t.requestInterruption()
                if not t.wait(2_000):
                    t.terminate()
                    t.wait(1_000)
            if not t.isRunning():
                setattr(self, attr, None)

    def _should_defer_keyframe_scan(self, probe: VideoProbe, bitrate_kbps: int | None) -> bool:
        if probe.duration_sec > KEYFRAME_AUTOSCAN_MAX_DURATION_SEC:
            return True
        if bitrate_kbps is not None and bitrate_kbps >= KEYFRAME_AUTOSCAN_MAX_BITRATE_KBPS:
            return True
        return False

    def _start_keyframe_scan(self, session_gen: int, *, show_status: bool) -> None:
        if not self._data.source_path:
            return
        if self._keyframe_thread and self._keyframe_thread.isRunning():
            return
        self._keyframes_loading = True
        self._keyframes_deferred = False
        self._keyframe_thread = KeyframeThread(Path(self._data.source_path))
        self._keyframe_thread.succeeded.connect(lambda times, g=session_gen: self._on_keyframes(times, g))
        self._keyframe_thread.failed.connect(lambda e, g=session_gen: self._on_keyframe_failed(e, g))
        self._keyframe_thread.start()
        if show_status:
            self._status(tr("status.analyzing_keyframes"))

    def _about(self) -> None:
        QMessageBox.about(
            self,
            tr("menu.about"),
            "EasyClip\nMIT License — see LICENSE\nFFmpeg is not part of MIT; bundle LGPL builds and notices.",
        )

    def _show_filename_template_help(self, parent: QWidget | None = None) -> None:
        host = parent if parent is not None else self
        dlg = QDialog(host)
        dlg.setWindowTitle(tr("export.filename_template.help.title"))
        lay = QVBoxLayout(dlg)
        txt = QPlainTextEdit(dlg)
        txt.setReadOnly(True)
        txt.setPlainText(tr("export.filename_template.help.body"))
        txt.setMinimumSize(560, 380)
        lay.addWidget(txt)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, dlg)
        buttons.accepted.connect(dlg.accept)
        lay.addWidget(buttons)
        dlg.exec()

    def _ask_load_last_project_at_startup(self, source: Path) -> tuple[bool, bool]:
        msg = QMessageBox(self)
        msg.setWindowTitle(tr("startup.restore.title"))
        msg.setText(tr("startup.restore.body", filename=source.name))
        cb_remember = QCheckBox(tr("startup.restore.remember"), msg)
        msg.setCheckBox(cb_remember)
        btn_load = msg.addButton(tr("startup.restore.load"), QMessageBox.ButtonRole.AcceptRole)
        msg.addButton(tr("startup.restore.skip"), QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_load)
        msg.exec()
        should_load = msg.clickedButton() == btn_load
        return should_load, cb_remember.isChecked()

    def _maybe_restore_last_project_on_startup(self) -> None:
        source_path = self._settings.last_open_source_path().strip()
        if not source_path:
            return
        source = Path(source_path).resolve()
        if not source.is_file():
            self._settings.set_last_open_source_path("")
            return
        behavior = self._settings.startup_behavior()
        if behavior == StartupBehavior.DO_NOTHING:
            return
        should_load = behavior == StartupBehavior.AUTO_LOAD_LAST_PROJECT
        if behavior == StartupBehavior.ASK:
            should_load, remember_choice = self._ask_load_last_project_at_startup(source)
            if remember_choice:
                next_behavior = (
                    StartupBehavior.AUTO_LOAD_LAST_PROJECT
                    if should_load
                    else StartupBehavior.DO_NOTHING
                )
                self._settings.set_startup_behavior(next_behavior)
        if not should_load:
            return
        try:
            self._load_video_path(source)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(
                self,
                tr("startup.restore.title"),
                tr("startup.restore.failed", detail=str(e)),
            )

    def _shortcut_quick_slice(self, digit: int) -> None:
        self._push_undo(tr("undo.action.quick_slice"))
        b, a = self._settings.quick_slice_extend(digit)
        m = self._current_frame
        fs = max(1e-9, float(self._tb.fps))
        fo = max(1, self._get_target_ui_fps())
        tot = max(1, self._tb.total_frames)

        t_m = m / fs
        
        # Calculate rough start
        t_s = t_m - b / float(fo)
        s = max(0, min(tot - 1, int(round(t_s * fs))))
        # Refine start using output_frame_count
        while s > 0 and output_frame_count(s, m - 1, fs, fo) < b:
            s -= 1
        while s < m and output_frame_count(s + 1, m - 1, fs, fo) >= b:
            s += 1
            
        # Calculate rough end
        t_e = t_m + (a + 1) / float(fo) - 1e-9
        e = max(s, min(tot - 1, int(round(t_e * fs)) - 1))
        # Refine end using output_frame_count
        while e < tot - 1 and output_frame_count(m + 1, e, fs, fo) < a:
            e += 1
        while e > m and output_frame_count(m + 1, e - 1, fs, fo) >= a:
            e -= 1

        # Guarantee the total is exactly b + a + 1 if possible
        target = b + a + 1
        while output_frame_count(s, e, fs, fo) < target and e < tot - 1:
            e += 1
        while output_frame_count(s, e, fs, fo) > target and e > m:
            e -= 1
        while output_frame_count(s, e, fs, fo) > target and s < m:
            s += 1

        start = s
        end = e
        clip = Clip(id=uuid.uuid4().hex, start_frame=start, end_frame=end, state=ClipState.CLOSED.value)
        idx_open = self._open_clip_index()
        if idx_open is not None:
            self._data.clips.pop(idx_open)
        self._data.clips.append(clip)
        self._refresh_clip_list()
        self._save_project()
        self._update_timeline()
        row = len(self._data.clips) - 1
        if not self._clip_output_matches_align_preview(clip.start_frame, clip.end_frame):
            self._prompt_align_8n1(row)

    def _make_export_preset_payload(
        self,
        *,
        name: str,
        export_fps: int,
        inherit_fps: bool,
        export_video_codec: str,
        export_video_rate_mode: str,
        export_video_bitrate_kbps: int,
        bitrate_match_source: bool,
        export_video_quality: int,
        export_filename_template: str = DEFAULT_EXPORT_FILENAME_TEMPLATE,
        size_multiple_enabled: bool = False,
        size_multiple_value: int = 2,
        align_enabled: bool = True,
        align_x: int = 8,
        align_y: int = 1,
        align_round: str = "ceil",
        align_apply: str = "tail",
        preset_id: str | None = None,
    ) -> dict[str, Any]:
        ax = max(1, min(1024, int(align_x)))
        ay = int(align_y) % ax
        ar = str(align_round or "ceil").strip().lower()
        if ar not in {"ceil", "floor"}:
            ar = "ceil"
        aa = str(align_apply or "tail").strip().lower()
        if aa not in {"tail", "head", "symmetric"}:
            aa = "tail"
        payload: dict[str, Any] = {
            "id": str(preset_id or uuid.uuid4().hex),
            "name": str(name or "").strip() or tr("export.preset.default_name"),
            "export_fps": max(1, min(240, int(export_fps))),
            "inherit_fps": bool(inherit_fps),
            "export_video_codec": str(export_video_codec or "auto").strip().lower() or "auto",
            "export_video_rate_mode": (
                "quality" if str(export_video_rate_mode).strip().lower() == "quality" else "bitrate"
            ),
            "export_video_bitrate_kbps": max(300, min(200000, int(export_video_bitrate_kbps))),
            "bitrate_match_source": bool(bitrate_match_source),
            "export_video_quality": max(0, min(51, int(export_video_quality))),
            "export_filename_template": (
                str(export_filename_template or "").strip() or DEFAULT_EXPORT_FILENAME_TEMPLATE
            ),
            "size_multiple_enabled": bool(size_multiple_enabled),
            "size_multiple_value": max(1, min(4096, int(size_multiple_value))),
            "align_enabled": bool(align_enabled),
            "align_x": ax,
            "align_y": ay,
            "align_round": ar,
            "align_apply": aa,
        }
        return payload

    def _save_export_preset(self, payload: dict[str, Any], *, set_as_default: bool = True) -> str:
        preset_id = str(payload.get("id", "") or "").strip() or uuid.uuid4().hex
        payload = dict(payload)
        payload["id"] = preset_id
        presets = self._settings.export_presets()
        replaced = False
        for i, p in enumerate(presets):
            if str(p.get("id", "")) == preset_id:
                presets[i] = payload
                replaced = True
                break
        if not replaced:
            presets.append(payload)
        self._settings.set_export_presets(presets)
        if set_as_default:
            self._settings.set_default_export_preset_id(preset_id)
        return preset_id

    def _show_settings(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("settings.title"))
        lay = QVBoxLayout(dlg)
        tabs = QTabWidget(dlg)
        lay.addWidget(tabs)

        # Tab 1: General
        tab_general = QWidget(dlg)
        general_form = QFormLayout(tab_general)
        lang = QComboBox(tab_general)
        lang.addItem(tr("settings.lang.zh_CN"), "zh_CN")
        lang.addItem(tr("settings.lang.en_US"), "en_US")
        idx = lang.findData(self._settings.language())
        lang.setCurrentIndex(idx if idx >= 0 else 0)
        general_form.addRow(tr("settings.lang"), lang)
        mode = QComboBox(tab_general)
        mode.addItem(tr("settings.mode.home"), ProjectDirMode.HOME_DEFAULT)
        mode.addItem(tr("settings.mode.source"), ProjectDirMode.NEXT_TO_SOURCE)
        mode.addItem(tr("settings.mode.exe"), ProjectDirMode.NEXT_TO_EXECUTABLE)
        mode.addItem(tr("settings.mode.custom"), ProjectDirMode.CUSTOM)
        cur = self._settings.project_dir_mode()
        for i in range(mode.count()):
            if mode.itemData(i) == cur:
                mode.setCurrentIndex(i)
                break
        general_form.addRow(tr("settings.project_dir"), mode)
        sb_seek_step = QSpinBox(tab_general)
        sb_seek_step.setRange(1, 600)
        sb_seek_step.setSuffix(tr("settings.playback_seek_seconds_suffix"))
        sb_seek_step.setValue(self._settings.playback_seek_seconds())
        general_form.addRow(tr("settings.playback_seek_seconds"), sb_seek_step)
        startup_behavior = QComboBox(tab_general)
        startup_behavior.addItem(tr("settings.startup.ask"), StartupBehavior.ASK)
        startup_behavior.addItem(tr("settings.startup.do_nothing"), StartupBehavior.DO_NOTHING)
        startup_behavior.addItem(
            tr("settings.startup.auto_load"),
            StartupBehavior.AUTO_LOAD_LAST_PROJECT,
        )
        cur_behavior = self._settings.startup_behavior()
        for i in range(startup_behavior.count()):
            if startup_behavior.itemData(i) == cur_behavior:
                startup_behavior.setCurrentIndex(i)
                break
        general_form.addRow(tr("settings.startup_behavior"), startup_behavior)
        sb_undo_steps = QSpinBox(tab_general)
        sb_undo_steps.setRange(10, 500)
        sb_undo_steps.setValue(self._settings.undo_max_steps())
        general_form.addRow(tr("settings.undo_max_steps"), sb_undo_steps)

        tabs.addTab(tab_general, tr("settings.tab.general"))

        # Tab 2: Snap / Quick Slice
        tab_snap = QWidget(dlg)
        snap_lay = QVBoxLayout(tab_snap)

        # 1. FPS Baseline
        row_fps = QHBoxLayout()
        row_fps.addWidget(QLabel(tr("settings.ui_align_fps")))
        sb_ui_align_fps = QSpinBox(tab_snap)
        sb_ui_align_fps.setRange(1, 240)
        sb_ui_align_fps.setValue(self._settings.ui_align_fps_baseline())
        row_fps.addWidget(sb_ui_align_fps)
        
        cb_match_source = QCheckBox(tr("settings.ui_align_fps_match_source"), tab_snap)
        cb_match_source.setChecked(self._settings.ui_align_fps_match_source())
        row_fps.addWidget(cb_match_source)
        row_fps.addStretch(1)
        snap_lay.addLayout(row_fps)

        def _sync_fps_state() -> None:
            sb_ui_align_fps.setEnabled(not cb_match_source.isChecked())
        cb_match_source.stateChanged.connect(_sync_fps_state)
        _sync_fps_state()

        # 2. C/F Snap Prediction
        gb_ui_align = QGroupBox(tr("settings.ui_align_group"), tab_snap)
        gb_ui_align_form = QFormLayout(gb_ui_align)
        lb_ui_align_hint = QLabel(gb_ui_align)
        lb_ui_align_hint.setWordWrap(True)
        gb_ui_align_form.addRow(lb_ui_align_hint)

        def _refresh_ui_align_hint() -> None:
            x = sb_ui_align_x.value()
            y = sb_ui_align_y.value()
            current = tr("settings.ui_align_current_pattern").format(x=x, y=y)
            lines = tr("settings.ui_align_block_hint").split("\n", 1)
            lines[0] = lines[0] + current
            lb_ui_align_hint.setText("\n".join(lines))

        warn_align = QCheckBox(tr("settings.warn_align_8n1"), gb_ui_align)
        warn_align.setChecked(self._settings.warn_align_8n1())
        gb_ui_align_form.addRow(warn_align)

        sb_ui_align_x = QSpinBox(gb_ui_align)
        sb_ui_align_x.setRange(1, 1024)
        sb_ui_align_x.setValue(self._settings.ui_align_x())
        sb_ui_align_y = QSpinBox(gb_ui_align)
        sb_ui_align_y.setRange(0, 1023)
        sb_ui_align_y.setValue(self._settings.ui_align_y())

        def _sync_ui_align_y_max(_v: int | None = None) -> None:
            xm = max(1, sb_ui_align_x.value())
            sb_ui_align_y.setRange(0, xm - 1 if xm > 1 else 0)
            sb_ui_align_y.setValue(int(sb_ui_align_y.value()) % xm)

        sb_ui_align_x.valueChanged.connect(_sync_ui_align_y_max)
        sb_ui_align_x.valueChanged.connect(lambda _v: _refresh_ui_align_hint())
        sb_ui_align_y.valueChanged.connect(lambda _v: _refresh_ui_align_hint())
        _sync_ui_align_y_max()
        _refresh_ui_align_hint()
        gb_ui_align_form.addRow(tr("settings.ui_align_x"), sb_ui_align_x)
        gb_ui_align_form.addRow(tr("settings.ui_align_y"), sb_ui_align_y)

        snap_lay.addWidget(gb_ui_align)

        # 3. Quick Slice
        gb_quick_slice = QGroupBox(tr("settings.quick_slice_group"), tab_snap)
        gb_quick_slice_form = QFormLayout(gb_quick_slice)
        self._qs_spins = []
        for i in range(1, 6):
            row_qs = QWidget()
            row_qs_lay = QHBoxLayout(row_qs)
            row_qs_lay.setContentsMargins(0, 0, 0, 0)
            row_qs_lay.addWidget(QLabel(tr("settings.quick_slice_key_label", num=i)))
            sb_before = QSpinBox()
            sb_before.setRange(0, 10_000_000)
            sb_before.setValue(self._settings.quick_slice_extend(i)[0])
            row_qs_lay.addWidget(sb_before)
            row_qs_lay.addWidget(QLabel(tr("settings.quick_slice_mid_label")))
            sb_after = QSpinBox()
            sb_after.setRange(0, 10_000_000)
            sb_after.setValue(self._settings.quick_slice_extend(i)[1])
            row_qs_lay.addWidget(sb_after)
            row_qs_lay.addWidget(QLabel(tr("settings.quick_slice_suffix")))
            row_qs_lay.addStretch(1)
            gb_quick_slice_form.addRow(row_qs)
            self._qs_spins.append((sb_before, sb_after))
            
        snap_lay.addWidget(gb_quick_slice)
        snap_lay.addStretch(1)

        tabs.addTab(tab_snap, tr("settings.tab.snap"))

        # Tab 2: Default export preset
        tab_export = QWidget(dlg)
        export_lay = QVBoxLayout(tab_export)
        export_lay.setContentsMargins(0, 0, 0, 0)
        export_form = QFormLayout()
        export_lay.addLayout(export_form)

        row_preset = QWidget(tab_export)
        row_preset_lay = QHBoxLayout(row_preset)
        row_preset_lay.setContentsMargins(0, 0, 0, 0)
        row_preset_lay.setSpacing(8)
        cb_preset = QComboBox(row_preset)
        edit_preset_name = QLineEdit(row_preset)
        edit_preset_name.setPlaceholderText(tr("settings.export_preset.name_placeholder"))
        row_preset_lay.addWidget(cb_preset, stretch=2)
        row_preset_lay.addWidget(edit_preset_name, stretch=3)
        export_form.addRow(tr("settings.export_preset"), row_preset)

        row_codec = QWidget(tab_export)
        row_codec_lay = QHBoxLayout(row_codec)
        row_codec_lay.setContentsMargins(0, 0, 0, 0)
        row_codec_lay.setSpacing(8)
        cb_codec = QComboBox(row_codec)
        row_codec_lay.addWidget(cb_codec, stretch=1)
        row_codec_lay.addStretch(1)
        export_form.addRow(tr("export.options.encoder"), row_codec)

        row_rate = QWidget(tab_export)
        row_rate_lay = QHBoxLayout(row_rate)
        row_rate_lay.setContentsMargins(0, 0, 0, 0)
        row_rate_lay.setSpacing(8)
        rb_bitrate = QRadioButton(tr("export.options.rate_mode.bitrate"), row_rate)
        sb_bitrate = QSpinBox(row_rate)
        sb_bitrate.setRange(300, 200000)
        sb_bitrate.setSuffix(" kbps")
        cb_bitrate_match_source = QCheckBox(tr("export.options.bitrate_match_source"), row_rate)
        rb_quality = QRadioButton(tr("export.options.rate_mode.quality_inline", qname="CRF"), row_rate)
        lb_quality_info = QLabel("ℹ️", row_rate)
        sb_quality = QSpinBox(row_rate)
        sb_quality.setRange(0, 51)
        row_rate_lay.addWidget(rb_bitrate)
        row_rate_lay.addWidget(sb_bitrate)
        row_rate_lay.addWidget(cb_bitrate_match_source)
        row_rate_lay.addSpacing(10)
        row_rate_lay.addWidget(rb_quality)
        row_rate_lay.addWidget(lb_quality_info)
        row_rate_lay.addWidget(sb_quality)
        row_rate_lay.addStretch(1)
        export_form.addRow(tr("export.options.rate_mode"), row_rate)

        row_fps = QWidget(tab_export)
        row_fps_lay = QHBoxLayout(row_fps)
        row_fps_lay.setContentsMargins(0, 0, 0, 0)
        row_fps_lay.setSpacing(8)
        sb_fps = QSpinBox(row_fps)
        sb_fps.setRange(1, 240)
        cb_inherit_fps = QCheckBox(tr("export.options.inherit_fps"), row_fps)
        row_fps_lay.addWidget(sb_fps)
        row_fps_lay.addWidget(cb_inherit_fps)
        row_fps_lay.addStretch(1)
        export_form.addRow(tr("settings.export_fps"), row_fps)

        row_multiple = QWidget(tab_export)
        row_multiple_lay = QHBoxLayout(row_multiple)
        row_multiple_lay.setContentsMargins(0, 0, 0, 0)
        row_multiple_lay.setSpacing(8)
        cb_multiple = QCheckBox(tr("export.options.multiple_enable"), row_multiple)
        sb_multiple = QSpinBox(row_multiple)
        sb_multiple.setRange(1, 4096)
        sb_multiple.setValue(2)
        row_multiple_lay.addWidget(cb_multiple)
        row_multiple_lay.addWidget(sb_multiple)
        row_multiple_lay.addWidget(QLabel(tr("export.options.multiple_suffix"), row_multiple))
        row_multiple_lay.addStretch(1)
        export_form.addRow(row_multiple)

        row_p_align = QWidget(tab_export)
        row_p_align_lay = QHBoxLayout(row_p_align)
        row_p_align_lay.setContentsMargins(0, 0, 0, 0)
        row_p_align_lay.setSpacing(8)
        cb_p_align_enabled = QCheckBox(tr("export.options.align_enabled"), row_p_align)
        cb_p_align_enabled.setChecked(True)
        sb_p_align_x = QSpinBox(row_p_align)
        sb_p_align_x.setRange(1, 1024)
        sb_p_align_x.setValue(8)
        sb_p_align_y = QSpinBox(row_p_align)
        sb_p_align_y.setRange(0, 1023)
        sb_p_align_y.setValue(1)
        cb_p_align_round = QComboBox(row_p_align)
        cb_p_align_round.addItem(tr("export.options.align_round.ceil"), "ceil")
        cb_p_align_round.addItem(tr("export.options.align_round.floor"), "floor")
        cb_p_align_apply = QComboBox(row_p_align)
        cb_p_align_apply.addItem(tr("export.options.align_apply.tail"), "tail")
        cb_p_align_apply.addItem(tr("export.options.align_apply.head"), "head")
        cb_p_align_apply.addItem(tr("export.options.align_apply.symmetric"), "symmetric")
        row_p_align_lay.addWidget(cb_p_align_enabled)
        row_p_align_lay.addWidget(QLabel(tr("export.options.align_x"), row_p_align))
        row_p_align_lay.addWidget(sb_p_align_x)
        row_p_align_lay.addWidget(QLabel(tr("export.options.align_y"), row_p_align))
        row_p_align_lay.addWidget(sb_p_align_y)
        row_p_align_lay.addWidget(QLabel(tr("export.options.align_round"), row_p_align))
        row_p_align_lay.addWidget(cb_p_align_round)
        row_p_align_lay.addWidget(QLabel(tr("export.options.align_apply"), row_p_align))
        row_p_align_lay.addWidget(cb_p_align_apply)
        row_p_align_lay.addStretch(1)
        export_form.addRow(tr("settings.align_preset"), row_p_align)

        def _sync_p_align_y_max(_i: int | None = None) -> None:
            xm = max(1, sb_p_align_x.value())
            sb_p_align_y.setRange(0, xm - 1 if xm > 1 else 0)
            sb_p_align_y.setValue(int(sb_p_align_y.value()) % xm)

        sb_p_align_x.valueChanged.connect(_sync_p_align_y_max)
        _sync_p_align_y_max()

        row_name_tpl = QWidget(tab_export)
        row_name_tpl_lay = QHBoxLayout(row_name_tpl)
        row_name_tpl_lay.setContentsMargins(0, 0, 0, 0)
        row_name_tpl_lay.setSpacing(8)
        edit_filename_template = QLineEdit(row_name_tpl)
        edit_filename_template.setPlaceholderText(tr("export.filename_template.placeholder"))
        btn_name_tpl_help = QToolButton(row_name_tpl)
        btn_name_tpl_help.setText("ℹ")
        btn_name_tpl_help.setToolTip(tr("export.filename_template.help.tip"))
        btn_name_tpl_help.clicked.connect(lambda: self._show_filename_template_help(dlg))
        row_name_tpl_lay.addWidget(edit_filename_template, stretch=1)
        row_name_tpl_lay.addWidget(btn_name_tpl_help)
        export_form.addRow(tr("export.filename_template.label"), row_name_tpl)

        row_save = QWidget(tab_export)
        row_save_lay = QHBoxLayout(row_save)
        row_save_lay.setContentsMargins(0, 0, 0, 0)
        row_save_lay.setSpacing(8)
        btn_save_preset = QPushButton(tr("settings.export_preset.save"), row_save)
        lb_saved = QLabel("", row_save)
        row_save_lay.addWidget(btn_save_preset)
        row_save_lay.addWidget(lb_saved, stretch=1)
        export_lay.addWidget(row_save)
        tabs.addTab(tab_export, tr("settings.tab.export_defaults"))

        presets: list[dict[str, Any]] = self._settings.export_presets()
        default_preset_id = self._settings.default_export_preset_id()
        preset_new_tag = "__new__"
        try:
            ffmpeg_bin, _ = find_ffmpeg()
            available_encoders = list_video_encoders(ffmpeg_bin)
        except Exception:
            ffmpeg_bin = "ffmpeg"
            available_encoders = set()
        try:
            auto_resolved = resolve_video_codec(ffmpeg_bin, "auto")
        except Exception:
            auto_resolved = "auto"
        encoder_options: list[tuple[str, str]] = [
            ("auto", tr("export.options.encoder.auto", resolved=auto_resolved))
        ]
        for key, label_key in (
            ("libx264", "export.options.encoder.libx264"),
            ("libopenh264", "export.options.encoder.libopenh264"),
            ("mpeg4", "export.options.encoder.mpeg4"),
            ("h264_nvenc", "export.options.encoder.h264_nvenc"),
            ("hevc_nvenc", "export.options.encoder.hevc_nvenc"),
            ("h264_amf", "export.options.encoder.h264_amf"),
            ("hevc_amf", "export.options.encoder.hevc_amf"),
            ("h264_qsv", "export.options.encoder.h264_qsv"),
            ("hevc_qsv", "export.options.encoder.hevc_qsv"),
        ):
            name = tr(label_key)
            if key not in available_encoders:
                name = f"{name} {tr('export.options.encoder.unavailable_tag')}"
            encoder_options.append((key, name))
        for k, n in encoder_options:
            cb_codec.addItem(n, k)

        sync_ui = [False]

        def _resolved_codec() -> str:
            requested = str(cb_codec.currentData() or "auto").strip().lower()
            try:
                return resolve_video_codec(ffmpeg_bin, requested)
            except Exception:
                return requested

        def _refresh_quality_annotation() -> None:
            qname = quantizer_symbol_for_codec(_resolved_codec())
            rb_quality.setText(tr("export.options.rate_mode.quality_inline", qname=qname))
            lb_quality_info.setToolTip(tr("export.options.video_quality_tip", qname=qname))

        def _refresh_rate_enabled() -> None:
            sb_bitrate.setEnabled(rb_bitrate.isChecked() and not cb_bitrate_match_source.isChecked())
            sb_quality.setEnabled(rb_quality.isChecked())
            sb_fps.setEnabled(not cb_inherit_fps.isChecked())
            cb_bitrate_match_source.setEnabled(rb_bitrate.isChecked())
            sb_multiple.setEnabled(cb_multiple.isChecked())

        def _next_default_preset_name() -> str:
            base = tr("export.preset.default_name")
            used = {str(p.get("name", "")).strip() for p in presets}
            if base not in used:
                return base
            idx = 2
            while True:
                cand = f"{base}{idx}"
                if cand not in used:
                    return cand
                idx += 1

        def _current_preset_form_state() -> dict[str, Any]:
            return {
                "name": edit_preset_name.text().strip(),
                "export_fps": max(1, min(240, int(sb_fps.value()))),
                "inherit_fps": bool(cb_inherit_fps.isChecked()),
                "export_video_codec": str(cb_codec.currentData() or "auto").strip().lower() or "auto",
                "export_video_rate_mode": "quality" if rb_quality.isChecked() else "bitrate",
                "export_video_bitrate_kbps": max(300, min(200000, int(sb_bitrate.value()))),
                "bitrate_match_source": bool(cb_bitrate_match_source.isChecked()),
                "export_video_quality": max(0, min(51, int(sb_quality.value()))),
                "export_filename_template": (
                    edit_filename_template.text().strip() or DEFAULT_EXPORT_FILENAME_TEMPLATE
                ),
                "size_multiple_enabled": bool(cb_multiple.isChecked()),
                "size_multiple_value": max(1, min(4096, int(sb_multiple.value()))),
                "align_enabled": bool(cb_p_align_enabled.isChecked()),
                "align_x": max(1, min(1024, int(sb_p_align_x.value()))),
                "align_y": int(sb_p_align_y.value()) % max(1, sb_p_align_x.value()),
                "align_round": str(cb_p_align_round.currentData() or "ceil"),
                "align_apply": str(cb_p_align_apply.currentData() or "tail"),
            }

        def _collect_preset_form(*, override_target_id: str | None = None) -> dict[str, Any]:
            current_id = str(cb_preset.currentData() or "")
            target_id = (
                override_target_id
                if override_target_id is not None
                else (current_id if current_id and current_id != preset_new_tag else None)
            )
            st = _current_preset_form_state()
            return self._make_export_preset_payload(
                preset_id=target_id,
                name=st["name"],
                export_fps=int(st["export_fps"]),
                inherit_fps=bool(st["inherit_fps"]),
                export_video_codec=str(st["export_video_codec"]),
                export_video_rate_mode=str(st["export_video_rate_mode"]),
                export_video_bitrate_kbps=int(st["export_video_bitrate_kbps"]),
                bitrate_match_source=bool(st["bitrate_match_source"]),
                export_video_quality=int(st["export_video_quality"]),
                export_filename_template=str(st["export_filename_template"]),
                size_multiple_enabled=bool(st["size_multiple_enabled"]),
                size_multiple_value=int(st["size_multiple_value"]),
                align_enabled=bool(st["align_enabled"]),
                align_x=int(st["align_x"]),
                align_y=int(st["align_y"]),
                align_round=str(st["align_round"]),
                align_apply=str(st["align_apply"]),
            )

        def _preset_state_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
            ax = max(1, min(1024, int(payload.get("align_x", 8))))
            ay = int(payload.get("align_y", 1)) % ax
            ar = str(payload.get("align_round", "ceil") or "ceil")
            if ar not in {"ceil", "floor"}:
                ar = "ceil"
            aa = str(payload.get("align_apply", "tail") or "tail")
            if aa not in {"tail", "head", "symmetric"}:
                aa = "tail"
            return {
                "name": str(payload.get("name", "") or "").strip(),
                "export_fps": max(1, min(240, int(payload.get("export_fps", self._settings.export_fps())))),
                "inherit_fps": bool(payload.get("inherit_fps", False)),
                "export_video_codec": str(payload.get("export_video_codec", "auto") or "auto").strip().lower() or "auto",
                "export_video_rate_mode": (
                    "quality"
                    if str(payload.get("export_video_rate_mode", "bitrate")).strip().lower() == "quality"
                    else "bitrate"
                ),
                "export_video_bitrate_kbps": max(300, min(200000, int(payload.get("export_video_bitrate_kbps", 8000)))),
                "bitrate_match_source": bool(payload.get("bitrate_match_source", False)),
                "export_video_quality": max(0, min(51, int(payload.get("export_video_quality", 23)))),
                "export_filename_template": (
                    str(payload.get("export_filename_template", DEFAULT_EXPORT_FILENAME_TEMPLATE) or "").strip()
                    or DEFAULT_EXPORT_FILENAME_TEMPLATE
                ),
                "size_multiple_enabled": bool(payload.get("size_multiple_enabled", False)),
                "size_multiple_value": max(1, min(4096, int(payload.get("size_multiple_value", 2)))),
                "align_enabled": bool(payload.get("align_enabled", True)),
                "align_x": ax,
                "align_y": ay,
                "align_round": ar,
                "align_apply": aa,
            }

        def _new_preset_template_state() -> dict[str, Any]:
            template = self._make_export_preset_payload(
                name="",
                export_fps=self._settings.export_fps(),
                inherit_fps=False,
                export_video_codec="auto",
                export_video_rate_mode="bitrate",
                export_video_bitrate_kbps=8000,
                bitrate_match_source=False,
                export_video_quality=23,
                export_filename_template=DEFAULT_EXPORT_FILENAME_TEMPLATE,
                size_multiple_enabled=False,
                size_multiple_value=2,
                preset_id=None,
            )
            st = _preset_state_from_payload(template)
            st["name"] = ""
            return st

        def _apply_preset_to_form(preset: dict[str, Any], *, is_new: bool) -> None:
            sync_ui[0] = True
            try:
                edit_preset_name.setText(str(preset.get("name", "") or ""))
                codec = str(preset.get("export_video_codec", "auto") or "auto").strip().lower()
                idx_codec = cb_codec.findData(codec)
                cb_codec.setCurrentIndex(idx_codec if idx_codec >= 0 else 0)
                mode = str(preset.get("export_video_rate_mode", "bitrate") or "bitrate").strip().lower()
                rb_quality.setChecked(mode == "quality")
                rb_bitrate.setChecked(mode != "quality")
                sb_bitrate.setValue(max(300, min(200000, int(preset.get("export_video_bitrate_kbps", 8000)))))
                cb_bitrate_match_source.setChecked(bool(preset.get("bitrate_match_source", False)))
                sb_quality.setValue(max(0, min(51, int(preset.get("export_video_quality", 23)))))
                sb_fps.setValue(max(1, min(240, int(preset.get("export_fps", self._settings.export_fps())))))
                cb_inherit_fps.setChecked(bool(preset.get("inherit_fps", False)))
                edit_filename_template.setText(
                    str(preset.get("export_filename_template", DEFAULT_EXPORT_FILENAME_TEMPLATE) or "").strip()
                    or DEFAULT_EXPORT_FILENAME_TEMPLATE
                )
                cb_multiple.setChecked(bool(preset.get("size_multiple_enabled", False)))
                sb_multiple.setValue(max(1, min(4096, int(preset.get("size_multiple_value", 2)))))
                st_align = _preset_state_from_payload(preset)
                cb_p_align_enabled.setChecked(bool(st_align["align_enabled"]))
                sb_p_align_x.setValue(int(st_align["align_x"]))
                sb_p_align_y.setValue(int(st_align["align_y"]))
                ir = cb_p_align_round.findData(st_align["align_round"])
                cb_p_align_round.setCurrentIndex(ir if ir >= 0 else 0)
                ia = cb_p_align_apply.findData(st_align["align_apply"])
                cb_p_align_apply.setCurrentIndex(ia if ia >= 0 else 0)
                _sync_p_align_y_max()
            finally:
                sync_ui[0] = False
            if is_new and not edit_preset_name.text().strip():
                edit_preset_name.setText(_next_default_preset_name())
            _refresh_quality_annotation()
            _refresh_rate_enabled()

        def _reload_preset_combo(select_id: str | None = None) -> None:
            cb_preset.blockSignals(True)
            cb_preset.clear()
            cb_preset.addItem(tr("settings.export_preset.new"), preset_new_tag)
            for p in presets:
                cb_preset.addItem(str(p.get("name", tr("export.preset.default_name"))), str(p.get("id", "")))
            target_id = select_id or default_preset_id
            idx = -1
            if target_id:
                idx = cb_preset.findData(target_id)
            if idx < 0:
                idx = 1 if cb_preset.count() > 1 else 0
            cb_preset.setCurrentIndex(idx)
            cb_preset.blockSignals(False)
            _on_preset_changed()

        def _on_preset_changed() -> None:
            nonlocal default_preset_id
            if sync_ui[0]:
                return
            selected = str(cb_preset.currentData() or preset_new_tag)
            if selected == preset_new_tag:
                tmpl = self._make_export_preset_payload(
                    name="",
                    export_fps=self._settings.export_fps(),
                    inherit_fps=False,
                    export_video_codec="auto",
                    export_video_rate_mode="bitrate",
                    export_video_bitrate_kbps=8000,
                    bitrate_match_source=False,
                    export_video_quality=23,
                    export_filename_template=DEFAULT_EXPORT_FILENAME_TEMPLATE,
                    size_multiple_enabled=False,
                    size_multiple_value=2,
                    preset_id=None,
                )
                _apply_preset_to_form(tmpl, is_new=True)
                sync_ui[0] = True
                try:
                    edit_preset_name.clear()
                finally:
                    sync_ui[0] = False
                return
            for p in presets:
                if str(p.get("id", "")) == selected:
                    _apply_preset_to_form(p, is_new=False)
                    default_preset_id = selected
                    self._settings.set_default_export_preset_id(selected)
                    break

        def _is_preset_form_dirty() -> bool:
            selected = str(cb_preset.currentData() or preset_new_tag)
            current = _current_preset_form_state()
            if selected == preset_new_tag:
                return current != _new_preset_template_state()
            for p in presets:
                if str(p.get("id", "")) == selected:
                    return current != _preset_state_from_payload(p)
            return current != _new_preset_template_state()

        def _save_current_preset() -> bool:
            nonlocal presets, default_preset_id
            name = edit_preset_name.text().strip()
            if not name:
                QMessageBox.warning(
                    dlg,
                    tr("settings.export_preset.name_required.title"),
                    tr("settings.export_preset.name_required.body"),
                )
                edit_preset_name.setFocus()
                return False
            selected = str(cb_preset.currentData() or preset_new_tag)
            current_id = selected if selected and selected != preset_new_tag else None
            target_id_override: str | None = None
            same_name_ids = [
                str(p.get("id", ""))
                for p in presets
                if str(p.get("name", "")).strip() == name and str(p.get("id", "")) != (current_id or "")
            ]
            if same_name_ids:
                ans = QMessageBox.question(
                    dlg,
                    tr("settings.export_preset.duplicate.title"),
                    tr("settings.export_preset.duplicate.body", name=name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ans != QMessageBox.StandardButton.Yes:
                    edit_preset_name.setFocus()
                    return False
                target_id_override = same_name_ids[0]
            payload = _collect_preset_form(override_target_id=target_id_override)
            saved_id = self._save_export_preset(payload, set_as_default=True)
            presets = self._settings.export_presets()
            default_preset_id = saved_id
            lb_saved.setText(tr("settings.export_preset.saved"))
            _reload_preset_combo(saved_id)
            return True

        cb_preset.currentIndexChanged.connect(lambda _i: _on_preset_changed())
        cb_codec.currentIndexChanged.connect(lambda _i: _refresh_quality_annotation())
        rb_bitrate.toggled.connect(lambda _v: _refresh_rate_enabled())
        rb_quality.toggled.connect(lambda _v: _refresh_rate_enabled())
        cb_inherit_fps.toggled.connect(lambda _v: _refresh_rate_enabled())
        cb_bitrate_match_source.toggled.connect(lambda _v: _refresh_rate_enabled())
        cb_multiple.toggled.connect(lambda _v: _refresh_rate_enabled())
        btn_save_preset.clicked.connect(lambda: _save_current_preset())

        _reload_preset_combo(default_preset_id)
        _refresh_quality_annotation()
        _refresh_rate_enabled()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        def _on_settings_accept() -> None:
            if _is_preset_form_dirty():
                ans = QMessageBox.question(
                    dlg,
                    tr("settings.export_preset.unsaved.title"),
                    tr("settings.export_preset.unsaved.body"),
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                if ans == QMessageBox.StandardButton.Cancel:
                    return
                if ans == QMessageBox.StandardButton.Yes and not _save_current_preset():
                    return
            dlg.accept()

        buttons.accepted.connect(_on_settings_accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._settings.set_language(lang.currentData())
        self._settings.set_project_dir_mode(mode.currentData())
        for i, (sb_before, sb_after) in enumerate(self._qs_spins, start=1):
            self._settings.set_quick_slice_extend(i, sb_before.value(), sb_after.value())
        self._settings.set_playback_seek_seconds(sb_seek_step.value())
        self._settings.set_startup_behavior(startup_behavior.currentData())
        self._settings.set_undo_max_steps(sb_undo_steps.value())
        self._settings.set_warn_align_8n1(warn_align.isChecked())
        self._settings.set_ui_align_fps_baseline(sb_ui_align_fps.value())
        self._settings.set_ui_align_fps_match_source(cb_match_source.isChecked())
        self._settings.set_ui_align_x(sb_ui_align_x.value())
        self._settings.set_ui_align_y(sb_ui_align_y.value())
        self._refresh_ui_language()
        self._refresh_min_window_size()

    def _proxy_scan_roots(self, mode: ProjectDirMode) -> list[Path]:
        roots: list[Path] = []
        if mode == ProjectDirMode.HOME_DEFAULT:
            roots.append(default_projects_root())
        elif mode == ProjectDirMode.CUSTOM:
            custom = self._settings.custom_project_root()
            if custom is not None:
                roots.append(custom)
        elif mode == ProjectDirMode.NEXT_TO_EXECUTABLE:
            if getattr(sys, "frozen", False):
                exe_dir = Path(sys.executable).parent
            else:
                exe_dir = Path(QCoreApplication.applicationDirPath())
            roots.append(exe_dir / ".easyclip_projects")
        elif mode == ProjectDirMode.NEXT_TO_SOURCE:
            if self._store is not None:
                roots.append(self._store.project_dir.parent)
            elif self._data.source_path:
                roots.append(Path(self._data.source_path).resolve().parent / ".easyclip_projects")
        # Deduplicate while preserving order.
        unique: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root.resolve()) if root.exists() else str(root)
            if key in seen:
                continue
            seen.add(key)
            unique.append(root)
        return unique

    def _clear_all_proxies(self) -> None:
        mode = self._settings.project_dir_mode()
        roots = self._proxy_scan_roots(mode)
        if not roots:
            QMessageBox.information(
                self,
                tr("settings.clear_proxies.title"),
                tr("settings.clear_proxies.no_root"),
            )
            return
        ans = QMessageBox.question(
            self,
            tr("settings.clear_proxies.title"),
            tr("settings.clear_proxies.confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        deleted = 0
        failed = 0
        for root in roots:
            if not root.is_dir():
                continue
            for proxy in root.rglob("proxy.mp4"):
                try:
                    proxy.unlink()
                    deleted += 1
                except OSError:
                    failed += 1
        # If the currently attached proxy was deleted, immediately fall back to source preview.
        if self._data.proxy_path and not Path(self._data.proxy_path).is_file():
            self._data.proxy_path = None
            self._save_project()
            if self._data.source_path:
                self._set_qt_preview_source(Path(self._data.source_path))
        QMessageBox.information(
            self,
            tr("settings.clear_proxies.title"),
            tr("settings.clear_proxies.done", deleted=deleted, failed=failed),
        )

    def _refresh_min_window_size(self) -> None:
        # Ensure the top splitter cannot shrink below the right sidebar's required content height,
        # avoiding overlap between thumbnail previews and sidebar controls.
        sidebar_min_h = self._right_drop_widget.minimumSizeHint().height()
        self._top_split.setMinimumHeight(max(120, sidebar_min_h))
        self.setMinimumSize(self.minimumSizeHint())

    def _toggle_source_path_display(self) -> None:
        if not self._data.source_path:
            return
        self._source_path_show_full = not self._source_path_show_full
        self._refresh_source_path_label()

    def _refresh_source_path_label(self) -> None:
        source = self._data.source_path
        if not source:
            self._source_path_label.setText("")
            self._source_path_label.setToolTip(tr("source_path.toggle_tooltip"))
            return
        src = Path(source)
        self._source_path_label.setText(str(src) if self._source_path_show_full else src.name)
        self._source_path_label.setToolTip(str(src))

    def _open_video(self) -> None:
        start = self._settings.last_open_video_dir() or ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("open_video"),
            start,
            "Video (*.mp4 *.mkv *.mov *.avi *.webm);;All (*.*)",
        )
        if not path:
            return
        self._request_load_video_path(Path(path))

    def _load_video_path(self, source: Path) -> None:
        self._stop_threads_for_video_switch()
        self._seek_apply_timer.stop()
        self._pending_seek_frame = None
        self._last_seek_target_ms = None
        self._session_gen += 1
        self._preroll_seek_token += 1
        gen = self._session_gen
        self._thumb_pending_job = None
        self._thumb_pix_cache.clear()
        self._player.stop()
        self._player.setSource(QUrl())
        self._clear_clip_loop()

        probe = probe_video(source)
        self._tb = Timebase.from_probe(probe)
        source_bitrate_kbps = probe_video_bitrate_kbps(source)
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).parent
        else:
            exe_dir = Path(QCoreApplication.applicationDirPath())
        root = resolve_project_root(source, self._settings, exe_dir)
        self._store = ProjectStore(root)
        self._store.ensure_dir()
        loaded = self._store.load()
        if loaded and Path(loaded.source_path).resolve() == source.resolve():
            self._data = loaded
        else:
            self._data = ProjectData(
                source_path=str(source.resolve()),
                fps=self._tb.fps,
                duration_sec=self._tb.duration_sec,
                total_frames=self._tb.total_frames,
                width=probe.width,
                height=probe.height,
            )
        self._data.source_path = str(source.resolve())
        self._data.fps = self._tb.fps
        self._data.duration_sec = self._tb.duration_sec
        self._data.total_frames = self._tb.total_frames
        self._data.width = probe.width
        self._data.height = probe.height
        if self._store.proxy_path.is_file():
            self._data.proxy_path = str(self._store.proxy_path)
        self._current_frame = max(0, min(int(self._data.current_frame), self._tb.total_frames - 1))
        self._view_start = 0
        self._view_span = max(1, self._tb.total_frames)
        self._timeline_clip_drag_dirty = False
        self._timeline.set_sync_crosshair_frame(None)
        self._wave.set_sync_crosshair_frame(None)
        self._keyframe_times = []
        self._keyframes_loaded = False
        self._keyframes_loading = False
        self._keyframes_deferred = self._should_defer_keyframe_scan(probe, source_bitrate_kbps)
        self._refresh_clip_list()
        self._refresh_undo_menu_state()
        self._save_project()

        if self._keyframes_deferred:
            self._status(tr("status.keyframes_deferred"))
        else:
            self._start_keyframe_scan(gen, show_status=False)

        has_audio = probe_has_audio(self._data.source_path)
        use_cached_wave = False
        if self._store.waveform_path.is_file():
            try:
                with np.load(self._store.waveform_path) as z:
                    if self._waveform_npz_matches(z):
                        m, mx = z["mins"], z["maxs"]
                        rms = z["rms"] if "rms" in z.files else None
                        use_cached_wave = True
                        if m.size and float(np.max(np.abs(m))) < 1e-6 and float(np.max(np.abs(mx))) < 1e-6:
                            self._wave.set_no_audio()
                        else:
                            self._wave.set_peaks(m, mx, rms)
            except OSError:
                use_cached_wave = False
        if not use_cached_wave:
            if not has_audio:
                self._wave.set_no_audio()
            else:
                self._wave.set_loading()
                self._start_waveform_build(gen, has_audio=has_audio)

        # Default behavior: never auto-attach proxy on project/video load.
        # Proxy preview is opt-in and should only be used after an explicit user action.
        self._set_qt_preview_source(source)

        self._update_timeline()
        self._source_path_show_full = False
        self._refresh_source_path_label()
        self._status("")
        self._settings.set_last_open_video_dir(str(source.parent))
        self._settings.set_last_open_source_path(str(source.resolve()))

    def _start_proxy_build(self) -> None:
        assert self._store is not None
        dst = self._store.proxy_path
        self._status(tr("proxy.generating"))
        sg = self._session_gen
        self._proxy_thread = ProxyBuildThread(Path(self._data.source_path), dst)
        self._proxy_thread.progress_sec.connect(lambda t: self._status(f"{tr('proxy.generating')} {t:.0f}s"))
        self._proxy_thread.succeeded.connect(lambda s=sg: self._on_proxy_done(s))
        self._proxy_thread.failed.connect(lambda e: self._status(f"proxy: {e}"))
        self._proxy_thread.start()

    def _generate_proxy_clicked(self) -> None:
        if not self._data.source_path:
            return
        assert self._store is not None
        if self._proxy_thread and self._proxy_thread.isRunning():
            self._status(tr("proxy.generating"))
            return
        self._start_proxy_build()

    @Slot(int)
    def _on_proxy_done(self, session_when_started: int) -> None:
        if session_when_started != self._session_gen:
            return
        assert self._store is not None
        self._data.proxy_path = str(self._store.proxy_path)
        self._save_project()
        self._attach_proxy(self._store.proxy_path)
        self._status("")

    def _set_qt_preview_source(self, path: Path) -> None:
        self._preview_uses_qt = True
        self._preview_label.hide()
        self._video_widget.show()
        # After setSource(), backends may ignore immediate setPosition before media is ready.
        # Keep a one-shot "seek when ready" fallback so restored frame is reliably applied.
        self._seek_after_source_ready = True
        self._player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self._player.pause()
        self._seek_media_to_frame(seek_now=True)
        self._btn_play.setText("▶")
        self._raise_video_preview_shim()

    def _raise_video_preview_shim(self) -> None:
        if getattr(self, "_video_preview_shim", None) is None:
            return
        self._video_preview_shim.setGeometry(0, 0, max(1, self._video_widget.width()), max(1, self._video_widget.height()))
        self._video_preview_shim.show()
        self._video_preview_shim.raise_()
        QTimer.singleShot(0, self._video_preview_shim.raise_)
        QTimer.singleShot(150, self._video_preview_shim.raise_)
        QTimer.singleShot(500, self._video_preview_shim.raise_)

    def _attach_proxy(self, path: Path) -> None:
        self._set_qt_preview_source(path)

    def _start_waveform_build(self, session_gen: int, *, has_audio: bool) -> None:
        if not self._data.source_path or not self._store:
            return
        bins = recommended_peak_bins(max(1200, self.width()), self._tb.duration_sec)
        self._wave_thread = WaveformThread(
            Path(self._data.source_path),
            bins,
            self._tb.duration_sec,
            self._store.waveform_path,
            has_audio=has_audio,
            sample_rate=STREAM_SAMPLE_RATE,
        )
        self._wave_thread.succeeded.connect(
            lambda m, mx, r, g=session_gen: self._on_wave_done(m, mx, r, g)
        )
        self._wave_thread.failed.connect(lambda e, g=session_gen: self._on_wave_failed(e, g))
        self._status(tr("waveform.generating"))
        self._wave_thread.start()

    @Slot(object, object, object, int)
    def _on_wave_done(self, mins, maxs, rms, session_gen: int) -> None:
        if session_gen != self._session_gen:
            return
        if mins is not None and maxs is not None and len(mins) > 0:
            if float(np.max(np.abs(mins))) < 1e-6 and float(np.max(np.abs(maxs))) < 1e-6:
                self._wave.set_no_audio()
            else:
                self._wave.set_peaks(mins, maxs, rms)
        else:
            self._wave.set_no_audio()
        self._status("")
        self._update_timeline()

    @Slot(str, int)
    def _on_wave_failed(self, err: str, session_gen: int) -> None:
        if session_gen != self._session_gen:
            return
        self._wave.set_error(f"{tr('waveform.ui.failed')}: {err}")
        self._status("")

    @Slot(list, int)
    def _on_keyframes(self, times: list[float], session_gen: int) -> None:
        if session_gen != self._session_gen:
            return
        self._keyframe_times = times
        self._keyframes_loaded = True
        self._keyframes_loading = False
        self._keyframes_deferred = False
        self._status("")

    @Slot(str, int)
    def _on_keyframe_failed(self, err: str, session_gen: int) -> None:
        if session_gen != self._session_gen:
            return
        self._keyframes_loading = False
        self._keyframes_loaded = False
        self._keyframes_deferred = False
        self._status(f"keyframes: {err}")

    # ──────────────────────────────────────────────────────────
    # Undo / Redo
    # ──────────────────────────────────────────────────────────

    def _clips_snapshot(self) -> list[dict]:
        """Serialize current clips list into a list of plain dicts for undo storage."""
        return [
            {"id": c.id, "start_frame": c.start_frame, "end_frame": c.end_frame, "state": c.state}
            for c in self._data.clips
        ]

    def _clips_from_snapshot(self, snap: list[dict]) -> list[Clip]:
        """Reconstruct Clip list from a snapshot stored in UndoState."""
        result: list[Clip] = []
        for d in snap:
            result.append(
                Clip(
                    id=str(d.get("id", uuid.uuid4().hex)),
                    start_frame=int(d.get("start_frame", 0)),
                    end_frame=int(d["end_frame"]) if d.get("end_frame") is not None else None,
                    state=str(d.get("state", ClipState.CLOSED.value)),
                )
            )
        return result

    def _push_undo(self, description: str) -> None:
        """Snapshot *current* state and push onto undo stack. Call this BEFORE making changes."""
        state = UndoState(
            description=description,
            current_frame=int(self._current_frame),
            clips_json=self._clips_snapshot(),
        )
        self._data.undo_history.append(state)
        max_steps = self._settings.undo_max_steps()
        over = len(self._data.undo_history) - max_steps
        if over > 0:
            self._data.undo_history = self._data.undo_history[over:]
        # New action invalidates the redo branch.
        self._data.redo_history.clear()
        self._refresh_undo_menu_state()

    def _undo(self) -> None:
        if not self._data.undo_history:
            self._status(tr("undo.status.nothing_to_undo"))
            return
        # Save *current* state into redo stack before restoring.
        old = self._data.undo_history[-1]
        redo_state = UndoState(
            description=old.description,
            current_frame=int(self._current_frame),
            clips_json=self._clips_snapshot(),
        )
        self._data.redo_history.append(redo_state)
        # Pop and restore.
        state = self._data.undo_history.pop()
        self._data.clips = self._clips_from_snapshot(state.clips_json)
        self._refresh_clip_list()
        self.set_current_frame(state.current_frame)
        self._update_timeline()
        self._save_project()
        self._status(tr("undo.status.undo", action=state.description))
        self._refresh_undo_menu_state()
        self._clip_loop_clamp_playhead_if_oob()

    def _redo(self) -> None:
        if not self._data.redo_history:
            self._status(tr("undo.status.nothing_to_redo"))
            return
        # Save current state back to undo stack.
        old = self._data.redo_history[-1]
        undo_state = UndoState(
            description=old.description,
            current_frame=int(self._current_frame),
            clips_json=self._clips_snapshot(),
        )
        self._data.undo_history.append(undo_state)
        # Pop and restore.
        state = self._data.redo_history.pop()
        self._data.clips = self._clips_from_snapshot(state.clips_json)
        self._refresh_clip_list()
        self.set_current_frame(state.current_frame)
        self._update_timeline()
        self._save_project()
        self._status(tr("undo.status.redo", action=state.description))
        self._refresh_undo_menu_state()
        self._clip_loop_clamp_playhead_if_oob()

    def _refresh_undo_menu_state(self) -> None:
        """Update Edit→Undo/Redo menu text and enabled state."""
        if not hasattr(self, "_act_undo"):
            return
        if self._data.undo_history:
            desc = self._data.undo_history[-1].description
            self._act_undo.setText(tr("menu.undo_action", action=desc))
            self._act_undo.setEnabled(True)
        else:
            self._act_undo.setText(tr("menu.undo"))
            self._act_undo.setEnabled(False)
        if self._data.redo_history:
            desc = self._data.redo_history[-1].description
            self._act_redo.setText(tr("menu.redo_action", action=desc))
            self._act_redo.setEnabled(True)
        else:
            self._act_redo.setText(tr("menu.redo"))
            self._act_redo.setEnabled(False)

    # ──────────────────────────────────────────────────────────

    def _save_project(self) -> None:
        if self._store is None:
            return
        self._data.current_frame = max(0, min(int(self._current_frame), self._tb.total_frames - 1))
        self._store.save(self._data)

    def _format_clip_duration_text(self, start_frame: int, end_frame: int | None) -> str:
        if end_frame is None:
            return "…"
        fps = max(self._tb.fps, 1e-6)
        frame_count = self._tb.frame_count_inclusive(start_frame, end_frame)
        duration_ms = int(round(frame_count * 1000.0 / fps))
        if duration_ms < 1000:
            return f"{duration_ms}ms"
        if duration_ms < 60_000:
            seconds = duration_ms / 1000.0
            sec_text = f"{seconds:.3f}".rstrip("0").rstrip(".")
            return f"{sec_text}s"
        minutes, rem_ms = divmod(duration_ms, 60_000)
        seconds, ms = divmod(rem_ms, 1000)
        return f"{minutes}min {seconds}s {ms}ms"

    def _format_clip_list_abs_time_hhmmss_mmm(self, total_ms: int) -> str:
        """Fixed-width timeline clock ``HH:MM:SS.mmm`` for aligned colon columns in the clip list."""
        total_ms = max(0, int(total_ms))
        h, r = divmod(total_ms, 3_600_000)
        m, r2 = divmod(r, 60_000)
        s, ms = divmod(r2, 1000)
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    def _format_clip_list_duration_compact_sec(self, total_ms: int) -> str:
        """Span like ``16.433s``; if >= 1 minute, ``1m16.433s`` (no ``0m``)."""
        total_ms = max(0, int(total_ms))
        total_sec = total_ms / 1000.0
        m_full = int(total_sec // 60)
        s_rem = total_sec - 60.0 * m_full
        s_str = f"{s_rem:.3f}".rstrip("0").rstrip(".")
        if m_full > 0:
            return f"{m_full}m{s_str}s"
        return f"{s_str}s"

    def _sync_clip_list_display_button(self) -> None:
        if self._settings.clip_list_show_time():
            self._btn_clip_list_mode.setText(tr("clips.show_frames"))
            self._btn_clip_list_mode.setToolTip(tr("clips.switch_to_frames_tip"))
        else:
            self._btn_clip_list_mode.setText(tr("clips.show_time"))
            self._btn_clip_list_mode.setToolTip(tr("clips.switch_to_time_tip"))

    def _on_clip_list_display_toggle(self) -> None:
        self._settings.set_clip_list_show_time(not self._settings.clip_list_show_time())
        self._sync_clip_list_display_button()
        self._refresh_clip_list()

    def _clip_list_row_text(self, idx0: int, c: Clip) -> str:
        n = idx0 + 1
        if self._settings.clip_list_show_time():
            fs = max(self._tb.fps, 1e-9)
            sm = int(round(c.start_frame / fs * 1000))
            st = self._format_clip_list_abs_time_hhmmss_mmm(sm)
            if c.end_frame is None:
                return f"{n}. {st} + …"
            end_f = c.end_frame
            em = int(round((end_f + 1) / fs * 1000))
            dur_ms = max(0, em - sm)
            dtxt = self._format_clip_list_duration_compact_sec(dur_ms)
            return f"{n}. {st} + {dtxt}"
        if c.end_frame is None:
            return f"{n}. [{c.start_frame}+…] (o…)"
        end_f = c.end_frame
        src_n = self._tb.frame_count_inclusive(c.start_frame, end_f)
        out_n = output_frame_count(
            c.start_frame,
            end_f,
            float(self._tb.fps),
            float(self._get_target_ui_fps()),
        )
        return f"{n}. [{c.start_frame}+{src_n}] (o{out_n})"

    def _clip_list_row_tooltip(self, idx0: int, c: Clip) -> str:
        if self._settings.clip_list_show_time():
            return self._clip_list_row_tooltip_time_detail(idx0, c)
        return self._clip_list_row_tooltip_frame_line(c)

    def _clip_list_row_tooltip_frame_line(self, c: Clip) -> str:
        start = int(c.start_frame)
        if c.end_frame is None:
            return tr("clips.row_tooltip", start=start, end="…", src="…", out="…")
        end = int(c.end_frame)
        src_n = self._tb.frame_count_inclusive(start, end)
        out_n = output_frame_count(
            start,
            end,
            float(self._tb.fps),
            float(self._get_target_ui_fps()),
        )
        return tr("clips.row_tooltip", start=start, end=end, src=src_n, out=out_n)

    def _clip_list_row_tooltip_time_detail(self, idx0: int, c: Clip) -> str:
        """Former in-row time string: in/out bracket + duration (shown as tooltip in time mode)."""
        n = idx0 + 1
        fs = max(self._tb.fps, 1e-9)
        sm = int(round(c.start_frame / fs * 1000))
        st = self._format_clip_list_abs_time_hhmmss_mmm(sm)
        if c.end_frame is None:
            return f"{n}. [{st} – …] (…)"
        end_f = c.end_frame
        em = int(round((end_f + 1) / fs * 1000))
        et = self._format_clip_list_abs_time_hhmmss_mmm(em)
        dur_ms = max(0, em - sm)
        dtxt = self._format_clip_list_duration_compact_sec(dur_ms)
        return f"{n}. [{st} – {et}] ({dtxt})"

    def _refresh_selected_clip_duration_label(self) -> None:
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._data.clips):
            self._thumb_duration_label.setText(tr("thumbs.duration_placeholder"))
            return
        c = self._data.clips[row]
        self._thumb_duration_label.setText(
            tr("thumbs.duration", duration=self._format_clip_duration_text(c.start_frame, c.end_frame))
        )

    def _refresh_clip_list(self) -> None:
        keep_row = self._clip_list.currentRow()
        self._clip_list.clear()
        for i, c in enumerate(self._data.clips):
            text = self._clip_list_row_text(i, c)
            item = QListWidgetItem()
            self._clip_list.addItem(item)

            row_widget = QWidget(self._clip_list)
            row_lay = QHBoxLayout(row_widget)
            row_lay.setContentsMargins(6, 2, 6, 2)
            row_lay.setSpacing(6)

            lb = QLabel(text, row_widget)
            # Keep delete button visible when the left pane is narrow: let text label shrink first.
            lb.setMinimumWidth(0)
            lb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lb.setToolTip(self._clip_list_row_tooltip(i, c))
            row_lay.addWidget(lb)

            btn_del = QPushButton("🗑", row_widget)
            btn_del.setToolTip(tr("clips.delete"))
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del.setFixedWidth(26)
            btn_del.setStyleSheet(
                "QPushButton { color:#e44; border:1px solid #944; border-radius:4px; background:#2b1a1a; } "
                "QPushButton:hover { background:#3a2020; }"
            )
            btn_del.clicked.connect(lambda _checked=False, r=i: self._delete_clip_row(r))
            row_lay.addWidget(btn_del)

            item.setSizeHint(row_widget.sizeHint())
            self._clip_list.setItemWidget(item, row_widget)
        if self._data.clips:
            row = keep_row if 0 <= keep_row < len(self._data.clips) else 0
            self._clip_list.setCurrentRow(row)
        self._refresh_clip_row_actions()
        self._refresh_selected_clip_duration_label()
        self._refresh_clip_nudge_controls()
        self._refresh_clip_loop_controls()

    def _update_clip_list_row_label(self, row: int) -> None:
        """Update only the text label and tooltip of one row without rebuilding the list."""
        if row < 0 or row >= self._clip_list.count():
            return
        item = self._clip_list.item(row)
        if item is None:
            return
        w = self._clip_list.itemWidget(item)
        if w is None:
            return
        c = self._data.clips[row]
        text = self._clip_list_row_text(row, c)
        tooltip = self._clip_list_row_tooltip(row, c)
        for lb in w.findChildren(QLabel):
            lb.setText(text)
            lb.setToolTip(tooltip)
            break

    def _delete_clip_row(self, row: int) -> None:
        if row < 0 or row >= len(self._data.clips):
            return
        self._clip_list.setCurrentRow(row)
        self._delete_selected_clip()

    def _refresh_clip_row_actions(self) -> None:
        current = self._clip_list.currentRow()
        for row in range(self._clip_list.count()):
            item = self._clip_list.item(row)
            if item is None:
                continue
            w = self._clip_list.itemWidget(item)
            if w is None:
                continue
            for b in w.findChildren(QPushButton):
                if b.text() == "🗑":
                    b.setVisible(row == current)
                    break

    def _get_target_ui_fps(self) -> float:
        if self._settings.ui_align_fps_match_source() and self._tb.fps > 0:
            return float(self._tb.fps)
        return float(self._settings.ui_align_fps_baseline())

    def _open_clip_index(self) -> int | None:
        for i, c in enumerate(self._data.clips):
            if c.end_frame is None:
                return i
        return None

    def _shortcut_start(self) -> None:
        if self._thumb_repeat_target == "start":
            row = self._clip_list.currentRow()
            if 0 <= row < len(self._data.clips):
                c = self._data.clips[row]
                if c.end_frame is not None:
                    if self._current_frame > c.end_frame:
                        # 当前帧已越过该片段尾帧：取消首帧编辑态，按常规 S 行为开启新片段。
                        self._thumb_repeat_target = None
                        self._refresh_thumb_selection_style()
                    else:
                        self._push_undo(tr("undo.action.set_start"))
                        c.start_frame = self._current_frame
                        self._refresh_clip_list()
                        self._clip_list.setCurrentRow(row)
                        self._save_project()
                        self._update_timeline()
                        self._update_thumbs_for_row(row)
                        self._clip_loop_clamp_playhead_if_oob()
                        return
        self._push_undo(tr("undo.action.set_start"))
        idx = self._open_clip_index()
        if idx is not None:
            self._data.clips.pop(idx)
        clip = Clip(id=uuid.uuid4().hex, start_frame=self._current_frame, end_frame=None, state=ClipState.OPEN.value)
        self._data.clips.append(clip)
        self._refresh_clip_list()
        self._clip_list.setCurrentRow(len(self._data.clips) - 1)
        if self._thumb_repeat_target is not None:
            self._thumb_repeat_target = None
            self._refresh_thumb_selection_style()
        self._save_project()
        self._update_timeline()

    def _apply_closed_clip_tail_from_playhead(self, row: int) -> None:
        """Set tail of an already-closed clip to the playhead (S/E/C/F 语义下 E 仅作用于尾帧)."""
        self._push_undo(tr("undo.action.set_end"))
        tb = self._tb
        c = self._data.clips[row]
        assert c.end_frame is not None
        c.end_frame = max(self._current_frame, c.start_frame)
        c.end_frame = min(c.end_frame, tb.total_frames - 1)
        self._refresh_clip_list()
        self._clip_list.setCurrentRow(row)
        self._thumb_repeat_target = "end"
        self._refresh_thumb_selection_style()
        self._save_project()
        self._update_timeline()
        self._update_thumbs_for_row(row)
        self._clip_loop_clamp_playhead_if_oob()

    def _finalize_open_clip_at_playhead(self, idx: int) -> None:
        """Close the open clip at idx using the current frame as tail."""
        self._push_undo(tr("undo.action.set_end"))
        c = self._data.clips[idx]
        assert c.end_frame is None
        c.end_frame = self._current_frame
        if c.end_frame < c.start_frame:
            c.end_frame = c.start_frame
        c.state = ClipState.CLOSED.value
        if not self._clip_output_matches_align_preview(c.start_frame, c.end_frame):
            self._prompt_align_8n1(idx)
        self._refresh_clip_list()
        self._clip_list.setCurrentRow(idx)
        self._thumb_repeat_target = "end"
        self._refresh_thumb_selection_style()
        self._save_project()
        self._update_timeline()
        self._update_thumbs_for_row(idx)
        self._clip_loop_clamp_playhead_if_oob()

    def _shortcut_end(self) -> None:
        row = self._clip_list.currentRow()

        if self._thumb_repeat_target == "end":
            if 0 <= row < len(self._data.clips):
                c = self._data.clips[row]
                if c.end_frame is not None:
                    self._apply_closed_clip_tail_from_playhead(row)
                    return

        if 0 <= row < len(self._data.clips):
            c = self._data.clips[row]
            if self._thumb_repeat_target == "start":
                if c.end_frame is not None:
                    self._apply_closed_clip_tail_from_playhead(row)
                else:
                    self._finalize_open_clip_at_playhead(row)
                return
            if self._thumb_repeat_target is None:
                if c.end_frame is not None:
                    self._apply_closed_clip_tail_from_playhead(row)
                else:
                    self._finalize_open_clip_at_playhead(row)
                return

        idx = self._open_clip_index()
        if idx is None:
            return
        self._finalize_open_clip_at_playhead(idx)

    def _clip_output_matches_align_preview(self, start_f: int, end_f: int) -> bool:
        n = output_frame_count(
            start_f,
            end_f,
            self._tb.fps,
            int(round(self._get_target_ui_fps())),
        )
        return is_valid_length(n, self._settings.ui_align_x(), self._settings.ui_align_y())

    def _prompt_align_8n1(self, clip_idx: int) -> None:
        if not self._settings.warn_align_8n1():
            return
        c = self._data.clips[clip_idx]
        assert c.end_frame is not None
        L_out = output_frame_count(
            c.start_frame,
            c.end_frame,
            self._tb.fps,
            int(round(self._get_target_ui_fps())),
        )
        ux, uy = self._settings.ui_align_x(), self._settings.ui_align_y()
        msg = QMessageBox(self)
        msg.setWindowTitle(tr("align.title", x=ux, y=uy))
        msg.setText(tr("align.body", length=L_out, x=ux, y=uy))
        cb_skip = QCheckBox(tr("align.skip_future"), msg)
        msg.setCheckBox(cb_skip)
        btn_ceil = msg.addButton(tr("align.ceil"), QMessageBox.ButtonRole.AcceptRole)
        btn_floor = msg.addButton(tr("align.floor"), QMessageBox.ButtonRole.ActionRole)
        btn_no = msg.addButton(tr("align.no"), QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if cb_skip.isChecked():
            self._settings.set_warn_align_8n1(False)
        clicked = msg.clickedButton()
        ui_fps = int(round(self._get_target_ui_fps()))
        end0 = c.end_frame
        if clicked == btn_ceil:
            e_new = snap_moving_end(
                start_f=c.start_frame,
                playhead_f=end0,
                fps_src=self._tb.fps,
                fps_out=ui_fps,
                total_frames=self._tb.total_frames,
                x=ux,
                y=uy,
                ceil=True,
            )
            if e_new is not None:
                c.end_frame = e_new
        elif clicked == btn_floor:
            e_new = snap_moving_end(
                start_f=c.start_frame,
                playhead_f=end0,
                fps_src=self._tb.fps,
                fps_out=ui_fps,
                total_frames=self._tb.total_frames,
                x=ux,
                y=uy,
                ceil=False,
            )
            if e_new is not None:
                c.end_frame = e_new
        self._refresh_clip_list()
        self._save_project()
        self._update_timeline()
        self._update_thumbs_for_row(clip_idx)
        self._clip_loop_clamp_playhead_if_oob()

    def _snap_8n1(self, ceil: bool) -> None:
        ui_fps = int(round(self._get_target_ui_fps()))
        ux, uy = self._settings.ui_align_x(), self._settings.ui_align_y()
        tb = self._tb
        row = self._clip_list.currentRow()
        if self._thumb_repeat_target in ("start", "end") and 0 <= row < len(self._data.clips):
            c = self._data.clips[row]
            if c.end_frame is not None:
                if self._thumb_repeat_target == "start":
                    target = snap_moving_start(
                        end_f=c.end_frame,
                        playhead_f=self._current_frame,
                        fps_src=tb.fps,
                        fps_out=ui_fps,
                        total_frames=tb.total_frames,
                        x=ux,
                        y=uy,
                        ceil=ceil,
                    )
                else:
                    target = snap_moving_end(
                        start_f=c.start_frame,
                        playhead_f=self._current_frame,
                        fps_src=tb.fps,
                        fps_out=ui_fps,
                        total_frames=tb.total_frames,
                        x=ux,
                        y=uy,
                        ceil=ceil,
                    )
                if target is None:
                    return
                self._push_undo(tr("undo.action.snap_align"))
                if self._thumb_repeat_target == "start":
                    c.start_frame = target
                else:
                    c.end_frame = target
                self._refresh_clip_list()
                self._clip_list.setCurrentRow(row)
                self._save_project()
                self._update_timeline()
                self._update_thumbs_for_row(row)
                self.set_current_frame(target)
                self._clip_loop_clamp_playhead_if_oob()
                return

        idx = self._open_clip_index()
        if idx is not None:
            c = self._data.clips[idx]
            if c.end_frame is None:
                c.end_frame = self._current_frame
            e = snap_moving_end(
                start_f=c.start_frame,
                playhead_f=self._current_frame,
                fps_src=tb.fps,
                fps_out=ui_fps,
                total_frames=tb.total_frames,
                x=ux,
                y=uy,
                ceil=ceil,
            )
            if e is None:
                return
            self._push_undo(tr("undo.action.snap_align"))
            c.end_frame = max(c.start_frame, min(e, tb.total_frames - 1))
            self._refresh_clip_list()
            self._clip_list.setCurrentRow(idx)
            self._thumb_repeat_target = "end"
            self._refresh_thumb_selection_style()
            self._save_project()
            self._update_timeline()
            self._update_thumbs_for_row(idx)
            self._clip_loop_clamp_playhead_if_oob()
            return
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._data.clips):
            return
        c = self._data.clips[row]
        if c.end_frame is None:
            return
        e = snap_moving_end(
            start_f=c.start_frame,
            playhead_f=self._current_frame,
            fps_src=tb.fps,
            fps_out=ui_fps,
            total_frames=tb.total_frames,
            x=ux,
            y=uy,
            ceil=ceil,
        )
        if e is None:
            return
        self._push_undo(tr("undo.action.snap_align"))
        c.end_frame = max(c.start_frame, min(e, tb.total_frames - 1))
        self._refresh_clip_list()
        self._save_project()
        self._update_timeline()
        self._update_thumbs_for_row(row)
        self._clip_loop_clamp_playhead_if_oob()

    def _on_clip_row(self, row: int) -> None:
        self._refresh_clip_row_actions()
        self._refresh_selected_clip_duration_label()
        self._refresh_clip_nudge_controls()
        self._refresh_boundary_nudge_controls()
        self._refresh_clip_loop_controls()
        # Keep timeline selected-highlight in sync with clip list selection immediately.
        self._update_timeline()
        if row < 0 or row >= len(self._data.clips):
            self._thumb_pending_job = None
            self._cancel_thumb_thread()
            self._thumb_start.clear()
            self._thumb_start.setText(tr("no_clip"))
            self._thumb_end.clear()
            self._thumb_end.setText(tr("no_clip"))
            return
        self._update_thumbs_for_row(row)

    def _on_clip_item_pressed(self, item: QListWidgetItem) -> None:
        row = self._clip_list.row(item)
        self._clip_row_reclick_candidate = row if row == self._clip_list.currentRow() else None

    def _on_clip_item_clicked(self, item: QListWidgetItem) -> None:
        row = self._clip_list.row(item)
        should_seek = self._clip_row_reclick_candidate == row
        self._clip_row_reclick_candidate = None
        if not should_seek:
            return
        if row < 0 or row >= len(self._data.clips):
            return
        self.set_current_frame(self._data.clips[row].start_frame)

    def _on_app_focus_changed_clear_thumb_arm(self, old: QObject | None, now: QObject | None) -> None:
        if now not in (self._thumb_start, self._thumb_end):
            self._thumb_repeat_target = None
            self._refresh_thumb_selection_style()

    def _on_clip_thumb_tapped(self, role: Literal["start", "end"]) -> None:
        if self._thumb_repeat_target == role:
            self._seek_selected_clip_thumb(role)
        else:
            self._thumb_repeat_target = role
        self._refresh_thumb_selection_style()

    def _refresh_thumb_selection_style(self) -> None:
        base = "border:1px solid #555;background:#222;"
        active = "border:2px solid #4da3ff;background:#222;"
        self._thumb_start.setStyleSheet(active if self._thumb_repeat_target == "start" else base)
        self._thumb_end.setStyleSheet(active if self._thumb_repeat_target == "end" else base)
        self._refresh_boundary_nudge_controls()

    def _seek_selected_clip_thumb(self, role: Literal["start", "end"]) -> None:
        if not self._data.source_path:
            return
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._data.clips):
            return
        c = self._data.clips[row]
        if role == "start":
            self.set_current_frame(c.start_frame)
        elif c.end_frame is not None:
            self.set_current_frame(c.end_frame)

    def _delete_selected_clip(self) -> None:
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._data.clips):
            return
        if self._thumb_repeat_target in ("start", "end"):
            self._push_undo(tr("undo.action.delete_boundary"))
        else:
            self._push_undo(tr("undo.action.delete_clip"))
        if self._thumb_repeat_target in ("start", "end"):
            c = self._data.clips[row]
            if self._thumb_repeat_target == "end":
                # 删除尾帧：回到未闭合片段，仅保留起点。
                c.end_frame = None
                c.state = ClipState.OPEN.value
                self._thumb_repeat_target = None
                self._refresh_thumb_selection_style()
                self._refresh_clip_list()
                self._clip_list.setCurrentRow(row)
                self._save_project()
                self._update_timeline()
                return
            # 删除首帧：数据模型必须有 start_frame，若有尾帧则保留该帧作为新的起点并转为未闭合；
            # 若本就无尾帧，则等价于删除该片段。
            if c.end_frame is not None:
                c.start_frame = c.end_frame
                c.end_frame = None
                c.state = ClipState.OPEN.value
                self._thumb_repeat_target = None
                self._refresh_thumb_selection_style()
                self._refresh_clip_list()
                self._clip_list.setCurrentRow(row)
                self._save_project()
                self._update_timeline()
                return
            self._thumb_repeat_target = None
            self._refresh_thumb_selection_style()
        self._data.clips.pop(row)
        self._refresh_clip_list()
        n = len(self._data.clips)
        if n == 0:
            self._thumb_start.clear()
            self._thumb_end.clear()
            self._thumb_start.setText(tr("no_clip"))
            self._thumb_end.setText(tr("no_clip"))
            self._thumb_repeat_target = None
            self._refresh_thumb_selection_style()
        else:
            new_row = min(row, n - 1)
            self._clip_list.setCurrentRow(new_row)
        self._save_project()
        self._update_timeline()

    def _clip_list_context_menu(self, pos) -> None:
        item = self._clip_list.itemAt(pos)
        if item is None:
            return
        row = self._clip_list.row(item)
        self._clip_list.setCurrentRow(row)
        menu = QMenu(self)
        del_act = QAction(tr("clips.delete"), self)
        del_act.triggered.connect(self._delete_selected_clip)
        menu.addAction(del_act)
        menu.exec(self._clip_list.mapToGlobal(pos))

    def _thumb_cache_get(self, source_path: str, frame: int) -> QPixmap | None:
        key = (source_path, int(frame))
        pix = self._thumb_pix_cache.get(key)
        if pix is None:
            return None
        self._thumb_pix_cache.move_to_end(key)
        return pix

    def _thumb_cache_put(self, source_path: str, frame: int, pix: QPixmap) -> None:
        key = (source_path, int(frame))
        self._thumb_pix_cache[key] = pix
        self._thumb_pix_cache.move_to_end(key)
        while len(self._thumb_pix_cache) > self._thumb_cache_max:
            self._thumb_pix_cache.popitem(last=False)

    def _cancel_thumb_thread(self) -> None:
        t = self._thumb_thread
        if t and t.isRunning():
            t.requestInterruption()

    def _scaled_thumb_pixmap_from_png(self, png: bytes) -> QPixmap | None:
        im = QImage.fromData(png, "PNG")
        if im.isNull():
            return None
        return QPixmap.fromImage(im).scaled(160, 120, Qt.AspectRatioMode.KeepAspectRatio)

    def _start_thumb_thread(
        self,
        source_path: str,
        row: int,
        clip_id: str,
        *,
        start_frame: int | None,
        end_frame: int | None,
    ) -> None:
        src = Path(source_path)
        if not src.is_file():
            return
        self._thumb_request_id += 1
        request_id = self._thumb_request_id
        t = ClipThumbThread(
            src=src,
            row=row,
            clip_id=clip_id,
            request_id=request_id,
            start_time_sec=self._tb.frame_to_time(start_frame) if start_frame is not None else None,
            end_time_sec=self._tb.frame_to_time(end_frame) if end_frame is not None else None,
        )
        t.succeeded.connect(self._on_thumb_thread_succeeded, Qt.ConnectionType.QueuedConnection)
        t.failed.connect(self._on_thumb_thread_failed, Qt.ConnectionType.QueuedConnection)
        t.finished.connect(self._on_thumb_thread_finished, Qt.ConnectionType.QueuedConnection)
        t.finished.connect(t.deleteLater)
        self._thumb_thread = t
        t.start()

    def _drain_pending_thumb_job(self) -> None:
        if self._thumb_thread is not None and self._thumb_thread.isRunning():
            return
        job = self._thumb_pending_job
        self._thumb_pending_job = None
        if job is None:
            return
        source_path, row, clip_id, start_frame, end_frame = job
        if row < 0 or row >= len(self._data.clips):
            return
        if not self._data.source_path or self._data.source_path != source_path:
            return
        c = self._data.clips[row]
        if c.id != clip_id:
            return
        self._start_thumb_thread(
            source_path,
            row,
            clip_id,
            start_frame=start_frame,
            end_frame=end_frame,
        )

    @Slot()
    def _on_thumb_thread_finished(self) -> None:
        sender = self.sender()
        if sender is self._thumb_thread and self._thumb_thread is not None and not self._thumb_thread.isRunning():
            self._thumb_thread = None
        self._drain_pending_thumb_job()

    @Slot(int, int, str, object, object)
    def _on_thumb_thread_succeeded(
        self,
        request_id: int,
        row: int,
        clip_id: str,
        start_png: object,
        end_png: object,
    ) -> None:
        is_latest = request_id == self._thumb_request_id
        if not is_latest:
            self._drain_pending_thumb_job()
            return
        if row != self._clip_list.currentRow():
            self._drain_pending_thumb_job()
            return
        if row < 0 or row >= len(self._data.clips):
            self._drain_pending_thumb_job()
            return
        if not self._data.source_path:
            self._drain_pending_thumb_job()
            return
        c = self._data.clips[row]
        if c.id != clip_id:
            self._drain_pending_thumb_job()
            return
        source_path = self._data.source_path
        if isinstance(start_png, bytes):
            start_pix = self._scaled_thumb_pixmap_from_png(start_png)
            if start_pix is not None:
                self._thumb_cache_put(source_path, c.start_frame, start_pix)
                self._thumb_start.setPixmap(start_pix)
        if c.end_frame is None:
            self._thumb_end.clear()
            self._thumb_end.setText("…")
            self._drain_pending_thumb_job()
            return
        if isinstance(end_png, bytes):
            end_pix = self._scaled_thumb_pixmap_from_png(end_png)
            if end_pix is not None:
                self._thumb_cache_put(source_path, c.end_frame, end_pix)
                self._thumb_end.setPixmap(end_pix)
        self._drain_pending_thumb_job()

    @Slot(int, int, str, str)
    def _on_thumb_thread_failed(self, request_id: int, row: int, clip_id: str, _err: str) -> None:
        is_latest = request_id == self._thumb_request_id
        if not is_latest:
            self._drain_pending_thumb_job()
            return
        if row != self._clip_list.currentRow():
            self._drain_pending_thumb_job()
            return
        if row < 0 or row >= len(self._data.clips):
            self._drain_pending_thumb_job()
            return
        if self._data.clips[row].id != clip_id:
            self._drain_pending_thumb_job()
            return
        self._thumb_start.setText(tr("no_clip"))
        self._thumb_end.setText(tr("no_clip"))
        self._drain_pending_thumb_job()

    def _update_thumbs_for_row(self, row: int) -> None:
        self._thumb_pending_job = None
        if row < 0 or row >= len(self._data.clips):
            self._thumb_start.clear()
            self._thumb_start.setText(tr("no_clip"))
            self._thumb_end.clear()
            self._thumb_end.setText(tr("no_clip"))
            return
        if not self._data.source_path:
            self._thumb_start.clear()
            self._thumb_start.setText(tr("no_clip"))
            self._thumb_end.clear()
            self._thumb_end.setText(tr("no_clip"))
            return

        c = self._data.clips[row]
        source_path = self._data.source_path
        src = Path(source_path)
        if not src.is_file():
            self._thumb_start.clear()
            self._thumb_start.setText(tr("no_clip"))
            self._thumb_end.clear()
            self._thumb_end.setText(tr("no_clip"))
            return

        start_cached = self._thumb_cache_get(source_path, c.start_frame)
        if start_cached is not None:
            self._thumb_start.setPixmap(start_cached)
            need_start = False
        else:
            self._thumb_start.clear()
            self._thumb_start.setText("…")
            need_start = True

        need_end = False
        if c.end_frame is None:
            self._thumb_end.clear()
            self._thumb_end.setText("…")
        else:
            end_cached = self._thumb_cache_get(source_path, c.end_frame)
            if end_cached is not None:
                self._thumb_end.setPixmap(end_cached)
            else:
                self._thumb_end.clear()
                self._thumb_end.setText("…")
                need_end = True

        if not need_start and not need_end:
            return

        start_frame = c.start_frame if need_start else None
        end_frame = c.end_frame if need_end and c.end_frame is not None else None
        if self._thumb_thread is not None and self._thumb_thread.isRunning():
            self._thumb_pending_job = (source_path, row, c.id, start_frame, end_frame)
            self._cancel_thumb_thread()
            return
        self._start_thumb_thread(
            source_path,
            row,
            c.id,
            start_frame=start_frame,
            end_frame=end_frame,
        )

    def _waveform_npz_matches(self, z: Any) -> bool:
        if "meta_bins" not in z.files or "meta_sr" not in z.files:
            return False
        if "mins" not in z.files or "maxs" not in z.files:
            return False
        kb = recommended_peak_bins(max(1200, self.width()), self._tb.duration_sec)
        ks = STREAM_SAMPLE_RATE
        try:
            if int(z["meta_bins"][0]) != kb or int(z["meta_sr"][0]) != ks:
                return False
            if "meta_ver" not in z.files or int(z["meta_ver"][0]) != WAVE_NPZ_VERSION:
                return False
        except (TypeError, ValueError, IndexError):
            return False
        m = z["mins"]
        return bool(m.size == kb)

    def _min_timeline_span(self) -> int:
        t = max(1, self._tb.total_frames)
        return max(30, min(t, max(1, t // 2000)))

    def _reset_timeline_view(self) -> None:
        self._view_start = 0
        self._view_span = max(1, self._tb.total_frames)
        self._update_timeline()

    def _zoom_timeline(self, *, zoom_in: bool, anchor_frame: float) -> None:
        t = max(1, self._tb.total_frames)
        old_span = max(1, self._view_span)
        factor = 1.12
        new_span = int(old_span / factor) if zoom_in else int(old_span * factor)
        min_sp = self._min_timeline_span()
        new_span = max(min_sp, min(t, new_span))
        if new_span >= t:
            self._view_start = 0
            self._view_span = t
            self._update_timeline()
            return
        anchor = max(0.0, min(float(t - 1), anchor_frame))
        t_anchor = (anchor - self._view_start) / old_span if old_span else 0.0
        new_start = int(anchor - t_anchor * new_span)
        new_start = max(0, min(new_start, t - new_span))
        self._view_start = new_start
        self._view_span = new_span
        self._update_timeline()

    def _zoom_anchor_from_cursor_or_playhead(self) -> float:
        cursor_pos = QCursor.pos()
        for widget in (self._timeline, self._wave):
            local = widget.mapFromGlobal(cursor_pos)
            if widget.rect().contains(local):
                ratio = max(0.0, min(1.0, float(local.x()) / max(1, widget.width())))
                return float(self._view_start) + ratio * max(1, self._view_span)
        return float(self._current_frame)

    def _zoom_timeline_from_wheel(self, event: QWheelEvent, watched: QObject) -> None:
        dy = event.angleDelta().y()
        if dy == 0:
            return
        zoom_in = dy > 0
        w = max(1, watched.width())  # type: ignore[attr-defined]
        x = float(event.position().x())
        anchor = self._view_start + max(0.0, min(1.0, x / w)) * max(1, self._view_span)
        self._zoom_timeline(zoom_in=zoom_in, anchor_frame=anchor)

    def _pan_timeline_from_wheel(self, event: QWheelEvent) -> None:
        self._pan_timeline(move_left=event.angleDelta().y() > 0)

    def _pan_timeline(self, *, move_left: bool) -> None:
        t = max(1, self._tb.total_frames)
        step = max(1, self._view_span // 80)
        delta = -step if move_left else step
        self._view_start = max(0, min(t - self._view_span, self._view_start + delta))
        self._update_timeline()

    def _zoom_timeline_from_shortcut(self, *, zoom_in: bool) -> None:
        if not self._data.source_path:
            return
        anchor = self._zoom_anchor_from_cursor_or_playhead()
        self._zoom_timeline(zoom_in=zoom_in, anchor_frame=anchor)

    def _follow_playhead_in_view(self) -> None:
        if self._view_span >= self._tb.total_frames:
            return
        margin = max(2, self._view_span // 12)
        if self._current_frame < self._view_start + margin:
            self._view_start = max(0, self._current_frame - margin)
        elif self._current_frame >= self._view_start + self._view_span - margin:
            self._view_start = min(
                self._tb.total_frames - self._view_span,
                self._current_frame - self._view_span + margin,
            )

    def _timeline_x_for_frame(self, frame: int) -> int:
        w = max(1, self._timeline.width())
        vs = max(1, self._view_span)
        return int((frame - self._view_start) * w / vs)

    def _pan_timeline_keep_clip_visible(self, clip_idx: int, *, mouse_x: float | None = None) -> None:
        """Pan zoomed timeline view so the clip stays in sight; optional cursor-edge scroll while dragging."""
        if self._view_span >= self._tb.total_frames:
            return
        if not (0 <= clip_idx < len(self._data.clips)):
            return
        c = self._data.clips[clip_idx]
        if c.end_frame is None:
            return
        t = max(1, self._tb.total_frames)
        vs = max(1, min(self._view_span, t))
        w = max(1, self._timeline.width())
        margin = max(8, w // 80)
        edge = max(12, w // 60)
        step = max(1, vs // 80)

        panned = False
        for _ in range(8):
            changed = False
            x0 = self._timeline_x_for_frame(c.start_frame)
            x1 = self._timeline_x_for_frame(c.end_frame + 1)

            if x0 < margin:
                df = int(round((margin - x0) / w * vs))
                self._view_start = max(0, self._view_start - df)
                changed = True
            if x1 > w - margin:
                df = int(round((x1 - (w - margin)) / w * vs))
                self._view_start = min(t - vs, self._view_start + df)
                changed = True

            if mouse_x is not None:
                if mouse_x < edge:
                    self._view_start = max(0, self._view_start - step)
                    changed = True
                elif mouse_x > w - edge:
                    self._view_start = min(t - vs, self._view_start + step)
                    changed = True

            self._view_start = max(0, min(self._view_start, t - vs))
            if not changed:
                break
            panned = True
        if panned:
            self._update_timeline()

    def _translate_clip_row_by_delta(self, row: int, delta_frames: int) -> bool:
        """Translate clip in time by delta_frames. Closed clips move span; open clips move start only."""
        if delta_frames == 0:
            return False
        c = self._data.clips[row]
        if c.end_frame is not None:
            lo = 0
            hi = self._tb.total_frames - 1
            new_s = c.start_frame + delta_frames
            new_e = c.end_frame + delta_frames
            if new_s < lo:
                d = lo - new_s
                new_s += d
                new_e += d
            if new_e > hi:
                d = hi - new_e
                new_s += d
                new_e += d
            if new_s == c.start_frame and new_e == c.end_frame:
                return False
            c.start_frame = new_s
            c.end_frame = new_e
            return True
        new_s = max(0, min(self._tb.total_frames - 1, c.start_frame + delta_frames))
        if new_s == c.start_frame:
            return False
        c.start_frame = new_s
        return True

    def _nudge_selected_clip_frames(self, delta_frames: int) -> None:
        if not self._data.source_path:
            return
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._data.clips):
            return
        # Snapshot before mutation; only commit if translate actually changes something.
        pre_snap = self._clips_snapshot()
        pre_frame = int(self._current_frame)
        if not self._translate_clip_row_by_delta(row, delta_frames):
            return
        state = UndoState(
            description=tr("undo.action.nudge_clip"),
            current_frame=pre_frame,
            clips_json=pre_snap,
        )
        self._data.undo_history.append(state)
        max_steps = self._settings.undo_max_steps()
        over = len(self._data.undo_history) - max_steps
        if over > 0:
            self._data.undo_history = self._data.undo_history[over:]
        self._data.redo_history.clear()
        self._refresh_undo_menu_state()
        self._refresh_clip_list()
        self._clip_list.setCurrentRow(row)
        self._update_thumbs_for_row(row)
        self._update_timeline()
        c = self._data.clips[row]
        if c.end_frame is not None:
            self._pan_timeline_keep_clip_visible(row, mouse_x=None)
        self._save_project()
        self._clip_loop_clamp_playhead_if_oob()

    def _can_nudge_boundary(self) -> bool:
        """Z/X boundary nudge is only valid when a thumb boundary is selected and a clip row is active."""
        if not self._data.source_path:
            return False
        if self._thumb_repeat_target not in ("start", "end"):
            return False
        row = self._clip_list.currentRow()
        if not (0 <= row < len(self._data.clips)):
            return False
        c = self._data.clips[row]
        if self._thumb_repeat_target == "end" and c.end_frame is None:
            return False
        return True

    def _refresh_boundary_nudge_controls(self) -> None:
        if not hasattr(self, "_btn_boundary_nudge_left"):
            return
        ok = self._can_nudge_boundary()
        self._btn_boundary_nudge_left.setEnabled(ok)
        self._btn_boundary_nudge_right.setEnabled(ok)
        if ok:
            self._btn_boundary_nudge_left.setToolTip(tr("transport.tip.boundary_nudge_left"))
            self._btn_boundary_nudge_right.setToolTip(tr("transport.tip.boundary_nudge_right"))
        else:
            tip = tr("transport.tip.boundary_nudge_disabled")
            self._btn_boundary_nudge_left.setToolTip(tip)
            self._btn_boundary_nudge_right.setToolTip(tip)

    def _nudge_boundary_frames(self, delta_frames: int) -> None:
        if not self._can_nudge_boundary():
            return
        row = self._clip_list.currentRow()
        c = self._data.clips[row]
        tb = self._tb
        if self._thumb_repeat_target == "start":
            new_val = c.start_frame + delta_frames
            new_val = max(0, new_val)
            if c.end_frame is not None:
                new_val = min(new_val, c.end_frame)
            else:
                new_val = min(new_val, tb.total_frames - 1)
            if new_val == c.start_frame:
                return
            self._push_undo(tr("undo.action.nudge_boundary"))
            c.start_frame = new_val
        else:  # end
            assert c.end_frame is not None
            new_val = c.end_frame + delta_frames
            new_val = max(c.start_frame, new_val)
            new_val = min(new_val, tb.total_frames - 1)
            if new_val == c.end_frame:
                return
            self._push_undo(tr("undo.action.nudge_boundary"))
            c.end_frame = new_val
        self._refresh_clip_list()
        self._clip_list.setCurrentRow(row)
        self._update_thumbs_for_row(row)
        self._update_timeline()
        self._save_project()
        self._clip_loop_clamp_playhead_if_oob()

    def _on_timeline_clip_select(self, clip_idx: int) -> None:
        if not self._data.source_path:
            return
        if not (0 <= clip_idx < len(self._data.clips)):
            return
        self._clip_list.setCurrentRow(clip_idx)

    def _on_timeline_clip_drag_delta(self, clip_idx: int, delta_frames: int) -> None:
        if not self._data.source_path:
            return
        if not (0 <= clip_idx < len(self._data.clips)):
            return
        c = self._data.clips[clip_idx]
        if c.end_frame is None:
            return
        if delta_frames != 0:
            # Push undo only once at the start of a drag gesture (before dirty flag is set).
            if not self._timeline_clip_drag_dirty:
                self._push_undo(tr("undo.action.drag_clip"))
            if self._translate_clip_row_by_delta(clip_idx, delta_frames):
                self._timeline_clip_drag_dirty = True
                # Lightweight: only update the affected row label text, not the whole list.
                self._update_clip_list_row_label(clip_idx)
                self._refresh_selected_clip_duration_label()
        self._update_timeline()
        local = self._timeline.mapFromGlobal(QCursor.pos())
        mx = float(local.x()) if self._timeline.rect().contains(local) else None
        self._pan_timeline_keep_clip_visible(clip_idx, mouse_x=mx)

    def _on_timeline_clip_drag_finished(self) -> None:
        if self._timeline_clip_drag_dirty:
            row = self._clip_list.currentRow()
            self._refresh_clip_list()
            if 0 <= row < len(self._data.clips):
                self._clip_list.setCurrentRow(row)
            self._save_project()
            self._timeline_clip_drag_dirty = False
            self._clip_loop_clamp_playhead_if_oob()

    def _update_timeline(self) -> None:
        self._timeline.set_state(
            self._tb.total_frames,
            self._current_frame,
            self._data.clips,
            view_start=self._view_start,
            view_span=self._view_span,
            fps=self._tb.fps,
            selected_clip_index=(
                self._clip_list.currentRow() if 0 <= self._clip_list.currentRow() < len(self._data.clips) else None
            ),
        )
        self._wave.set_view(
            self._view_start,
            self._view_span,
            self._tb.total_frames,
            current_frame=self._current_frame,
        )

    def _on_preview_volume_changed(self, value: int) -> None:
        v = max(0.0, min(1.0, value / 100.0))
        self._audio.setVolume(v)
        self._settings.set_preview_volume(v)
        if self._vol_slider_pressed:
            self._show_vol_tooltip(value)

    def _adjust_volume(self, delta: int) -> None:
        new_val = max(0, min(100, self._vol_slider.value() + delta))
        self._vol_slider.setValue(new_val)
        self._show_vol_tooltip(new_val)
        self._vol_tooltip_timer.start()

    def _vol_handle_global_pos(self) -> QPoint:
        slider = self._vol_slider
        w = slider.width()
        val = slider.value()
        rng = slider.maximum() - slider.minimum()
        groove_margin = 10
        groove_w = max(w - 2 * groove_margin, 1)
        ratio = (val - slider.minimum()) / rng if rng > 0 else 0.0
        handle_cx = groove_margin + int(ratio * groove_w)
        return slider.mapToGlobal(QPoint(handle_cx, -4))

    def _show_vol_tooltip(self, value: int | None = None) -> None:
        from PySide6.QtWidgets import QToolTip
        v = value if value is not None else self._vol_slider.value()
        QToolTip.showText(self._vol_handle_global_pos(), str(v), self._vol_slider)

    def _hide_vol_tooltip(self) -> None:
        from PySide6.QtWidgets import QToolTip
        QToolTip.hideText()

    def _on_vol_slider_pressed(self) -> None:
        self._vol_slider_pressed = True
        self._show_vol_tooltip()

    def _on_vol_slider_released(self) -> None:
        self._vol_slider_pressed = False
        if not self._vol_mouse_over_slider:
            self._hide_vol_tooltip()

    def _sync_scrub_crosshair(self, frame: object) -> None:
        if frame is None:
            self._timeline.set_sync_crosshair_frame(None)
            self._wave.set_sync_crosshair_frame(None)
            return
        t = max(1, self._tb.total_frames)
        f = max(0, min(int(frame), t - 1))
        self._timeline.set_sync_crosshair_frame(f)
        self._wave.set_sync_crosshair_frame(f)

    def _on_waveform_seek(self, frame: int) -> None:
        if not self._data.source_path:
            return
        self.set_current_frame(frame, seek_now=False)

    def _on_timeline_seek(self, frame: int) -> None:
        if not self._data.source_path:
            return
        self.set_current_frame(frame, seek_now=False)

    def set_current_frame(self, f: int, *, seek_now: bool = True) -> None:
        self._current_frame = max(0, min(int(f), self._tb.total_frames - 1))
        self._seek_media_to_frame(seek_now=seek_now)
        # Keep timeline/wave viewport focused when frame jumps outside visible window.
        self._follow_playhead_in_view()
        self._refresh_frame_preview()
        self._update_timeline()
        self._clip_loop_clamp_playhead_if_oob()

    def _seek_media_to_frame(self, *, seek_now: bool) -> None:
        if not self._data.source_path:
            self._seek_apply_timer.stop()
            self._pending_seek_frame = None
            return
        target_frame = self._current_frame
        if not seek_now:
            self._pending_seek_frame = target_frame
            self._seek_apply_timer.start()
            return
        self._seek_apply_timer.stop()
        self._pending_seek_frame = None
        self._apply_seek_for_frame(target_frame, now=time.monotonic(), force=True)

    def _flush_pending_seek(self) -> None:
        if self._pending_seek_frame is None:
            return
        if not self._data.source_path:
            self._pending_seek_frame = None
            self._seek_apply_timer.stop()
            return
        target = int(self._pending_seek_frame)
        self._pending_seek_frame = None
        self._apply_seek_for_frame(target, now=time.monotonic(), force=False)

    def _apply_seek_for_frame(self, target_frame: int, *, now: float, force: bool) -> None:
        """Coalesce rapid seek bursts to reduce demuxer churn/noise."""
        t = self._tb.frame_to_time(target_frame)
        ms = int(t * 1000)
        if self._last_seek_target_ms is not None and abs(ms - self._last_seek_target_ms) <= 1:
            return
        # Avoid forcing the backend through back-to-back seeks in the same event burst.
        if not force and now - self._last_seek_setpos_mono < 0.02:
            self._pending_seek_frame = target_frame
            self._seek_apply_timer.start()
            return
        self._last_seek_setpos_mono = now
        self._last_seek_target_ms = ms
        self._player.setPosition(ms)

    def _refresh_frame_preview(self) -> None:
        if self._preview_uses_qt:
            return
        if not self._data.source_path:
            return
        try:
            png = extract_frame_png(Path(self._data.source_path), self._tb.frame_to_time(self._current_frame))
            img = QImage.fromData(png, "PNG")
            self._preview_label.setPixmap(
                QPixmap.fromImage(img).scaled(
                    self._preview_label.width(),
                    self._preview_label.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_player_position(self, pos: int) -> None:
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        t = pos / 1000.0
        frame_here = self._tb.time_to_frame(t)
        jump_start = self._clip_loop_maybe_wrap_playhead_frame(t)
        if jump_start is not None:
            # 必须与上次 seek 目标拉开，否则会跳过 setPosition → UI 卡在片段首帧、解码照常走。
            self._last_seek_target_ms = None
            self._last_seek_setpos_mono = 0.0
            self.set_current_frame(jump_start)
            self._last_pos_ui_update_mono = time.monotonic()
            self._follow_playhead_in_view()
            self._update_timeline()
            self._player.play()
            return
        now = time.monotonic()
        throttle_s = 0.0 if self._clip_loop_clip_id is not None else 0.05
        if throttle_s > 0 and now - self._last_pos_ui_update_mono < throttle_s:
            return
        self._last_pos_ui_update_mono = now
        self._current_frame = frame_here
        self._follow_playhead_in_view()
        self._update_timeline()

    def _sync_audio_output_to_system_default(self) -> None:
        default_dev = QMediaDevices.defaultAudioOutput()
        if default_dev is None or default_dev.isNull():
            return
        cur = self._audio.device()
        if not cur.isNull() and cur.id() == default_dev.id():
            return
        self._audio.setDevice(default_dev)

    def _refresh_audio_default_poll_timer(self) -> None:
        """仅在前台或正在播放时轮询默认输出设备（切换系统默认但列表不变时的兜底）。"""
        app = QApplication.instance()
        in_foreground = (
            app is not None
            and app.applicationState() == Qt.ApplicationState.ApplicationActive
        )
        playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if in_foreground or playing:
            if not self._audio_default_poll.isActive():
                self._audio_default_poll.start()
        else:
            self._audio_default_poll.stop()

    @Slot(Qt.ApplicationState)
    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self._sync_audio_output_to_system_default()
        self._refresh_audio_default_poll_timer()

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        st = self.style()
        self._btn_play.setText("")
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._btn_play.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self._btn_play.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            if (
                state == QMediaPlayer.PlaybackState.StoppedState
                and self._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia
            ):
                self._clip_loop_restart_tail_at_stream_end()
        self._refresh_audio_default_poll_timer()

    @Slot(QMediaPlayer.MediaStatus)
    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._clip_loop_restart_tail_at_stream_end()
        if not self._seek_after_source_ready:
            return
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            # Force a retry even if an earlier same-ms seek was de-duplicated.
            self._last_seek_target_ms = None
            self._last_seek_setpos_mono = 0.0
            self._seek_after_source_ready = False
            self._apply_seek_for_frame(self._current_frame, now=time.monotonic(), force=True)
        elif status in (
            QMediaPlayer.MediaStatus.InvalidMedia,
            QMediaPlayer.MediaStatus.NoMedia,
        ):
            self._seek_after_source_ready = False

    def _toggle_play(self) -> None:
        if not self._data.source_path:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            # Ensure queued timeline click seeks land before resuming playback.
            self._flush_pending_seek()
            self._player.play()

    def _step_frame(self, delta: int) -> None:
        was_playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.set_current_frame(self._current_frame + delta)
        if was_playing:
            self._player.play()

    def _arrow_horizontal_transport(self, direction: int) -> None:
        """←/→：播放时按设置秒数快进/快退；暂停时单帧步进。"""
        if not self._data.source_path:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._skip_playback_seconds(direction)
        else:
            self._step_frame(direction)

    def _maybe_trigger_keyframe_scan_for_seek(self) -> None:
        if self._keyframes_loaded or self._keyframes_loading:
            return
        if not self._keyframes_deferred:
            return
        self._start_keyframe_scan(self._session_gen, show_status=False)

    def _keyframe_preroll_sec(self, target_sec: float) -> float | None:
        """不大于 target 的最近关键帧时刻，用于先试跳再精确定位。"""
        ts = self._keyframe_times
        if not ts:
            return None
        i = bisect.bisect_right(ts, target_sec) - 1
        if i >= 0:
            return float(ts[i])
        return 0.0

    def _finish_preroll_seek(self, target_frame: int, resume_playing: bool, token: int) -> None:
        if token != self._preroll_seek_token:
            return
        if not self._data.source_path:
            return
        self._last_seek_target_ms = None
        self._last_seek_setpos_mono = 0.0
        self._current_frame = target_frame
        self._apply_seek_for_frame(target_frame, now=time.monotonic(), force=True)
        self._follow_playhead_in_view()
        self._refresh_frame_preview()
        self._update_timeline()
        if resume_playing:
            self._player.play()

    def _seek_to_frame_with_optional_preroll(self, target_frame: int, *, resume_playing: bool) -> None:
        target_frame = max(0, min(int(target_frame), self._tb.total_frames - 1))
        self._current_frame = target_frame
        target_sec = self._tb.frame_to_time(target_frame)
        hint_sec = self._keyframe_preroll_sec(target_sec)
        self._pending_seek_frame = None
        self._seek_apply_timer.stop()
        min_gap = 0.055
        if hint_sec is None or abs(target_sec - hint_sec) < min_gap:
            self._apply_seek_for_frame(target_frame, now=time.monotonic(), force=True)
            self._follow_playhead_in_view()
            self._refresh_frame_preview()
            self._update_timeline()
            if resume_playing:
                self._player.play()
            return
        self._preroll_seek_token += 1
        token = self._preroll_seek_token
        self._last_seek_target_ms = None
        if resume_playing:
            self._player.pause()
        self._player.setPosition(int(hint_sec * 1000))
        QTimer.singleShot(
            50,
            lambda: self._finish_preroll_seek(target_frame, resume_playing, token),
        )

    def _skip_playback_seconds(self, sign: int) -> None:
        if not self._data.source_path:
            return
        self._maybe_trigger_keyframe_scan_for_seek()
        z = float(self._settings.playback_seek_seconds())
        was_playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        cur_sec = self._tb.frame_to_time(self._current_frame)
        target_sec = cur_sec + sign * z
        if self._tb.duration_sec > 0:
            target_sec = max(0.0, min(target_sec, self._tb.duration_sec - 1e-6))
        target_frame = self._tb.time_to_frame(target_sec)
        self._seek_to_frame_with_optional_preroll(target_frame, resume_playing=was_playing)

    def _confirm_strong_export(self) -> bool:
        ans = QMessageBox.warning(
            self,
            tr("export.strong.title"),
            tr("export.strong.confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return ans == QMessageBox.StandardButton.Yes

    def _export_clips(self) -> None:
        self._start_export_clips(strong_av_sync=False)

    def _export_clips_strong(self) -> None:
        if not self._confirm_strong_export():
            return
        self._start_export_clips(strong_av_sync=True)

    def _show_export_task_options(self, *, strong_av_sync: bool) -> ExportTaskOptions | None:
        source_w = max(1, int(self._data.width or 1))
        source_h = max(1, int(self._data.height or 1))

        def _parse_sar_ratio(raw: str | None) -> float:
            if not raw:
                return 1.0
            s = str(raw).strip()
            if not s or s == "0:1":
                return 1.0
            if ":" in s:
                a, b = s.split(":", 1)
            elif "/" in s:
                a, b = s.split("/", 1)
            else:
                try:
                    v = float(s)
                except ValueError:
                    return 1.0
                return v if v > 0 else 1.0
            try:
                na = float(a)
                nb = float(b)
            except ValueError:
                return 1.0
            if nb == 0:
                return 1.0
            r = na / nb
            return r if r > 0 else 1.0

        sar_ratio = 1.0
        if self._data.source_path:
            try:
                vp = probe_video(self._data.source_path)
                sar_ratio = _parse_sar_ratio(vp.sample_aspect_ratio)
            except Exception:
                sar_ratio = 1.0

        # Visual-equivalent base for UI math: matches ffmpeg's leading ``scale=iw*sar,setsar=1``.
        source_vis_w = max(1, int(round(float(source_w) * sar_ratio)))
        source_vis_h = source_h
        source_aspect = float(source_vis_w) / float(max(1, source_vis_h))
        source_fps_float = float(self._tb.fps if self._tb.fps > 0 else self._settings.export_fps())
        source_fps_int = max(1, min(240, int(round(source_fps_float))))
        source_has_audio = bool(self._data.source_path) and probe_has_audio(self._data.source_path)
        source_bitrate_kbps = 8000
        if self._data.source_path:
            try:
                br = probe_video_bitrate_kbps(self._data.source_path)
                if br is not None:
                    source_bitrate_kbps = br
            except Exception:
                pass
        export_presets = self._settings.export_presets()
        default_preset = self._settings.default_export_preset()
        default_preset_id = str(self._settings.default_export_preset_id() or "").strip()
        preset_fps = max(1, min(240, int(default_preset.get("export_fps", self._settings.export_fps()))))
        preset_inherit_fps = bool(default_preset.get("inherit_fps", False))
        preset_codec = str(default_preset.get("export_video_codec", "auto") or "auto").strip().lower()
        preset_rate_mode = str(default_preset.get("export_video_rate_mode", "bitrate") or "bitrate").strip().lower()
        if preset_rate_mode not in {"bitrate", "quality"}:
            preset_rate_mode = "bitrate"
        preset_bitrate = max(
            300,
            min(200000, int(default_preset.get("export_video_bitrate_kbps", source_bitrate_kbps))),
        )
        preset_bitrate_match_source = bool(default_preset.get("bitrate_match_source", False))
        preset_quality = max(0, min(51, int(default_preset.get("export_video_quality", 23))))
        preset_filename_template = (
            str(default_preset.get("export_filename_template", DEFAULT_EXPORT_FILENAME_TEMPLATE) or "").strip()
            or DEFAULT_EXPORT_FILENAME_TEMPLATE
        )
        preset_multiple_enabled = bool(default_preset.get("size_multiple_enabled", False))
        preset_multiple_value = max(1, min(4096, int(default_preset.get("size_multiple_value", 2))))
        ax0 = max(1, min(1024, int(default_preset.get("align_x", 8))))
        ay0 = int(default_preset.get("align_y", 1)) % ax0
        ar0 = str(default_preset.get("align_round", "ceil") or "ceil")
        if ar0 not in {"ceil", "floor"}:
            ar0 = "ceil"
        aa0 = str(default_preset.get("align_apply", "tail") or "tail")
        if aa0 not in {"tail", "head", "symmetric"}:
            aa0 = "tail"
        preset_align_enabled = bool(default_preset.get("align_enabled", True))
        try:
            ffmpeg_bin, _ = find_ffmpeg()
            available_encoders = list_video_encoders(ffmpeg_bin)
        except Exception:
            ffmpeg_bin = "ffmpeg"
            available_encoders = set()
        try:
            auto_resolved = resolve_video_codec(ffmpeg_bin, "auto")
        except Exception:
            auto_resolved = "auto"
        encoder_options: list[tuple[str, str]] = [
            ("auto", tr("export.options.encoder.auto", resolved=auto_resolved))
        ]
        for key, label_key in (
            ("libx264", "export.options.encoder.libx264"),
            ("libopenh264", "export.options.encoder.libopenh264"),
            ("mpeg4", "export.options.encoder.mpeg4"),
            ("h264_nvenc", "export.options.encoder.h264_nvenc"),
            ("hevc_nvenc", "export.options.encoder.hevc_nvenc"),
            ("h264_amf", "export.options.encoder.h264_amf"),
            ("hevc_amf", "export.options.encoder.hevc_amf"),
            ("h264_qsv", "export.options.encoder.h264_qsv"),
            ("hevc_qsv", "export.options.encoder.hevc_qsv"),
        ):
            tag = tr("export.options.encoder.unavailable_tag")
            name = tr(label_key)
            if key not in available_encoders:
                name = f"{name} {tag}"
            encoder_options.append((key, name))

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("export.options.title"))
        lay = QVBoxLayout(dlg)
        form = QFormLayout()

        row_quick_preset = QWidget(dlg)
        row_quick_preset_lay = QHBoxLayout(row_quick_preset)
        row_quick_preset_lay.setContentsMargins(0, 0, 0, 0)
        row_quick_preset_lay.setSpacing(8)
        cb_quick_preset = QComboBox(dlg)
        cb_quick_preset.addItem(tr("export.options.quick_preset.placeholder"), "")
        for p in export_presets:
            pid = str(p.get("id", "") or "").strip()
            name = str(p.get("name", "") or "").strip() or tr("export.preset.default_name")
            if pid:
                cb_quick_preset.addItem(name, pid)
        if default_preset_id:
            idx_quick = cb_quick_preset.findData(default_preset_id)
            if idx_quick >= 0:
                cb_quick_preset.setCurrentIndex(idx_quick)
        row_quick_preset_lay.addWidget(cb_quick_preset, stretch=1)
        row_quick_preset_lay.addStretch(1)
        form.addRow(tr("export.options.quick_preset"), row_quick_preset)

        row_codec = QWidget(dlg)
        row_codec_lay = QHBoxLayout(row_codec)
        row_codec_lay.setContentsMargins(0, 0, 0, 0)
        row_codec_lay.setSpacing(8)
        cb_codec = QComboBox(dlg)
        for k, n in encoder_options:
            cb_codec.addItem(n, k)
        codec_idx = cb_codec.findData(preset_codec)
        if codec_idx >= 0:
            cb_codec.setCurrentIndex(codec_idx)
        row_codec_lay.addWidget(cb_codec, stretch=1)
        row_codec_lay.addStretch(1)
        form.addRow(tr("export.options.encoder"), row_codec)

        row_rate = QWidget(dlg)
        row_rate_lay = QHBoxLayout(row_rate)
        row_rate_lay.setContentsMargins(0, 0, 0, 0)
        row_rate_lay.setSpacing(8)
        rb_bitrate = QRadioButton(tr("export.options.rate_mode.bitrate"), dlg)
        rb_bitrate.setChecked(preset_rate_mode != "quality")
        sb_bitrate = QSpinBox(dlg)
        sb_bitrate.setRange(300, 200000)
        sb_bitrate.setValue(preset_bitrate)
        sb_bitrate.setSuffix(" kbps")
        cb_bitrate_match_source = QCheckBox(tr("export.options.bitrate_match_source"), dlg)
        cb_bitrate_match_source.setChecked(preset_bitrate_match_source)
        rb_quality = QRadioButton(tr("export.options.rate_mode.quality_inline", qname="CRF"), dlg)
        lb_quality_info = QLabel("ℹ️", dlg)
        lb_quality_info.setToolTip(tr("export.options.video_quality_tip", qname="CRF"))
        sb_quality = QSpinBox(dlg)
        sb_quality.setRange(0, 51)
        sb_quality.setValue(preset_quality)
        row_rate_lay.addWidget(rb_bitrate)
        row_rate_lay.addWidget(sb_bitrate)
        row_rate_lay.addWidget(cb_bitrate_match_source)
        row_rate_lay.addSpacing(10)
        row_rate_lay.addWidget(rb_quality)
        row_rate_lay.addWidget(lb_quality_info)
        row_rate_lay.addWidget(sb_quality)
        row_rate_lay.addStretch(1)
        rb_quality.setChecked(preset_rate_mode == "quality")
        form.addRow(tr("export.options.rate_mode"), row_rate)

        row_fps = QWidget(dlg)
        row_fps_lay = QHBoxLayout(row_fps)
        row_fps_lay.setContentsMargins(0, 0, 0, 0)
        row_fps_lay.setSpacing(8)
        sb_fps = QSpinBox(dlg)
        sb_fps.setRange(1, 240)
        sb_fps.setValue(preset_fps)
        cb_inherit_fps = QCheckBox(
            tr("export.options.inherit_fps_inline", fps=f"{source_fps_float:.3f}"),
            dlg,
        )
        cb_inherit_fps.setChecked(preset_inherit_fps)
        row_fps_lay.addWidget(sb_fps)
        row_fps_lay.addWidget(cb_inherit_fps)
        row_fps_lay.addStretch(1)
        form.addRow(tr("settings.export_fps"), row_fps)

        lb_align_ui_mismatch = QLabel("", dlg)
        lb_align_ui_mismatch.setWordWrap(True)
        lb_align_ui_mismatch.setStyleSheet(
            "QLabel { background-color:#fff8e1; color:#5c4a00; padding:8px; border-radius:6px; }"
        )
        lb_align_ui_mismatch.setVisible(False)
        form.addRow(lb_align_ui_mismatch)

        row_export_align = QWidget(dlg)
        rel = QHBoxLayout(row_export_align)
        rel.setContentsMargins(0, 0, 0, 0)
        rel.setSpacing(8)
        cb_export_align_enabled = QCheckBox(tr("export.options.align_enabled"), dlg)
        cb_export_align_enabled.setChecked(preset_align_enabled)
        sb_export_align_x = QSpinBox(dlg)
        sb_export_align_x.setRange(1, 1024)
        sb_export_align_x.setValue(ax0)
        sb_export_align_y = QSpinBox(dlg)
        sb_export_align_y.setRange(0, 1023)
        sb_export_align_y.setValue(ay0)
        cb_export_align_round = QComboBox(dlg)
        cb_export_align_round.addItem(tr("export.options.align_round.ceil"), "ceil")
        cb_export_align_round.addItem(tr("export.options.align_round.floor"), "floor")
        cb_export_align_round.setCurrentIndex(0 if ar0 == "ceil" else 1)
        cb_export_align_apply = QComboBox(dlg)
        cb_export_align_apply.addItem(tr("export.options.align_apply.tail"), "tail")
        cb_export_align_apply.addItem(tr("export.options.align_apply.head"), "head")
        cb_export_align_apply.addItem(tr("export.options.align_apply.symmetric"), "symmetric")
        iax = cb_export_align_apply.findData(aa0)
        cb_export_align_apply.setCurrentIndex(iax if iax >= 0 else 0)
        rel.addWidget(cb_export_align_enabled)
        rel.addWidget(QLabel(tr("export.options.align_x"), dlg))
        rel.addWidget(sb_export_align_x)
        rel.addWidget(QLabel(tr("export.options.align_y"), dlg))
        rel.addWidget(sb_export_align_y)
        rel.addWidget(QLabel(tr("export.options.align_round"), dlg))
        rel.addWidget(cb_export_align_round)
        rel.addWidget(QLabel(tr("export.options.align_apply"), dlg))
        rel.addWidget(cb_export_align_apply)
        rel.addStretch(1)
        form.addRow(tr("settings.align_preset"), row_export_align)

        def _sync_export_align_y_max(_v: int | None = None) -> None:
            xm = max(1, sb_export_align_x.value())
            sb_export_align_y.setRange(0, xm - 1 if xm > 1 else 0)
            sb_export_align_y.setValue(int(sb_export_align_y.value()) % xm)

        _sync_export_align_y_max()

        def _refresh_export_align_ui_mismatch() -> None:
            ui_fps = int(round(self._get_target_ui_fps()))
            ui_x = self._settings.ui_align_x()
            ui_y = self._settings.ui_align_y()
            efps = source_fps_int if cb_inherit_fps.isChecked() else sb_fps.value()
            ex = max(1, sb_export_align_x.value())
            ey = int(sb_export_align_y.value()) % ex
            mis = (efps != ui_fps) or (ex != ui_x) or (ey != ui_y)
            lb_align_ui_mismatch.setVisible(mis)
            if mis:
                parts = []
                if efps != ui_fps:
                    parts.append(tr("export.options.align_mismatch_fps", ui_fps=ui_fps, efps=efps))
                if ex != ui_x:
                    parts.append(tr("export.options.align_mismatch_x", ui_x=ui_x, ex=ex))
                if ey != ui_y:
                    parts.append(tr("export.options.align_mismatch_y", ui_y=ui_y, ey=ey))
                sep = tr("export.options.align_mismatch_sep")
                details = sep.join(parts)
                lb_align_ui_mismatch.setText(tr("export.options.align_mismatch", details=details))

        row_res = QWidget(dlg)
        row_res_lay = QHBoxLayout(row_res)
        row_res_lay.setContentsMargins(0, 0, 0, 0)
        row_res_lay.setSpacing(8)
        sb_w = QSpinBox(dlg)
        sb_w.setRange(1, 16384)
        sb_w.setValue(source_vis_w)
        btn_keep_aspect = QToolButton(dlg)
        btn_keep_aspect.setCheckable(True)
        btn_keep_aspect.setChecked(True)
        btn_keep_aspect.setText("🔒")
        btn_keep_aspect.setToolTip(tr("export.options.keep_aspect_on"))
        btn_keep_aspect.setFixedWidth(34)
        sb_h = QSpinBox(dlg)
        sb_h.setRange(1, 16384)
        sb_h.setValue(source_vis_h)
        cb_inherit_res = QCheckBox(
            tr("export.options.inherit_resolution_inline", width=source_vis_w, height=source_vis_h),
            dlg,
        )
        row_res_title = QWidget(dlg)
        row_res_title_lay = QHBoxLayout(row_res_title)
        row_res_title_lay.setContentsMargins(0, 0, 0, 0)
        row_res_title_lay.setSpacing(4)
        row_res_title_lay.addWidget(QLabel(tr("export.options.content_resolution"), row_res_title))
        lb_res_info = QLabel("ℹ️", row_res_title)
        lb_res_info.setToolTip(tr("export.options.content_resolution_tip"))
        row_res_title_lay.addWidget(lb_res_info)
        row_res_title_lay.addStretch(1)
        row_res_lay.addWidget(QLabel(tr("export.options.width_label"), dlg))
        row_res_lay.addWidget(sb_w)
        row_res_lay.addWidget(btn_keep_aspect)
        row_res_lay.addWidget(QLabel(tr("export.options.height_label"), dlg))
        row_res_lay.addWidget(sb_h)
        row_res_lay.addWidget(cb_inherit_res)
        row_res_lay.addStretch(1)
        form.addRow(row_res_title, row_res)

        row_multiple = QWidget(dlg)
        row_multiple_lay = QHBoxLayout(row_multiple)
        row_multiple_lay.setContentsMargins(0, 0, 0, 0)
        row_multiple_lay.setSpacing(8)
        cb_multiple = QCheckBox(tr("export.options.multiple_enable"), dlg)
        sb_multiple = QSpinBox(dlg)
        sb_multiple.setRange(1, 4096)
        sb_multiple.setValue(preset_multiple_value)
        cb_multiple.setChecked(preset_multiple_enabled)
        row_multiple_lay.addWidget(cb_multiple)
        row_multiple_lay.addWidget(sb_multiple)
        row_multiple_lay.addWidget(QLabel(tr("export.options.multiple_suffix"), dlg))
        lb_multiple_info = QLabel("ℹ️", dlg)
        lb_multiple_info.setToolTip(tr("export.options.multiple_tip"))
        row_multiple_lay.addWidget(lb_multiple_info)
        row_multiple_lay.addStretch(1)
        form.addRow(row_multiple)
        lay.addLayout(form)

        gb_balance = QGroupBox(tr("export.options.edge_adjust.title"), dlg)
        gb_balance_lay = QFormLayout(gb_balance)
        lb_edge_hint = QLabel(tr("export.options.edge_adjust.hint"), gb_balance)
        lb_edge_hint.setWordWrap(True)
        gb_balance_lay.addRow(lb_edge_hint)

        row_h_adjust = QWidget(gb_balance)
        row_h_adjust_lay = QHBoxLayout(row_h_adjust)
        row_h_adjust_lay.setContentsMargins(14, 0, 0, 0)
        row_h_adjust_lay.setSpacing(0)
        left_group = QWidget(row_h_adjust)
        left_group_lay = QHBoxLayout(left_group)
        left_group_lay.setContentsMargins(0, 0, 0, 0)
        left_group_lay.setSpacing(4)
        lb_left = QLabel(tr("export.options.left"), left_group)
        sb_left = QSpinBox(left_group)
        sb_left.setRange(-16384, 16384)
        left_group_lay.addWidget(lb_left)
        left_group_lay.addWidget(sb_left)

        right_group = QWidget(row_h_adjust)
        right_group_lay = QHBoxLayout(right_group)
        right_group_lay.setContentsMargins(0, 0, 0, 0)
        right_group_lay.setSpacing(4)
        lb_right = QLabel(tr("export.options.right"), right_group)
        sb_right = QSpinBox(right_group)
        sb_right.setRange(-16384, 16384)
        right_group_lay.addWidget(lb_right)
        right_group_lay.addWidget(sb_right)

        row_h_adjust_lay.addWidget(left_group)
        row_h_adjust_lay.addSpacing(24)
        row_h_adjust_lay.addWidget(right_group)
        row_h_adjust_lay.addStretch(1)
        gb_balance_lay.addRow(tr("export.options.edge_adjust.horizontal"), row_h_adjust)

        row_v_adjust = QWidget(gb_balance)
        row_v_adjust_lay = QHBoxLayout(row_v_adjust)
        row_v_adjust_lay.setContentsMargins(14, 0, 0, 0)
        row_v_adjust_lay.setSpacing(0)
        top_group = QWidget(row_v_adjust)
        top_group_lay = QHBoxLayout(top_group)
        top_group_lay.setContentsMargins(0, 0, 0, 0)
        top_group_lay.setSpacing(4)
        lb_top = QLabel(tr("export.options.top"), top_group)
        sb_top = QSpinBox(top_group)
        sb_top.setRange(-16384, 16384)
        top_group_lay.addWidget(lb_top)
        top_group_lay.addWidget(sb_top)

        bottom_group = QWidget(row_v_adjust)
        bottom_group_lay = QHBoxLayout(bottom_group)
        bottom_group_lay.setContentsMargins(0, 0, 0, 0)
        bottom_group_lay.setSpacing(4)
        lb_bottom = QLabel(tr("export.options.bottom"), bottom_group)
        sb_bottom = QSpinBox(bottom_group)
        sb_bottom.setRange(-16384, 16384)
        bottom_group_lay.addWidget(lb_bottom)
        bottom_group_lay.addWidget(sb_bottom)

        row_v_adjust_lay.addWidget(top_group)
        row_v_adjust_lay.addSpacing(24)
        row_v_adjust_lay.addWidget(bottom_group)
        row_v_adjust_lay.addStretch(1)
        gb_balance_lay.addRow(tr("export.options.edge_adjust.vertical"), row_v_adjust)
        lay.addWidget(gb_balance)

        row_name_tpl = QWidget(dlg)
        row_name_tpl_lay = QHBoxLayout(row_name_tpl)
        row_name_tpl_lay.setContentsMargins(0, 0, 0, 0)
        row_name_tpl_lay.setSpacing(8)
        edit_filename_template = QLineEdit(dlg)
        edit_filename_template.setPlaceholderText(tr("export.filename_template.placeholder"))
        edit_filename_template.setText(preset_filename_template)
        btn_name_tpl_help = QToolButton(dlg)
        btn_name_tpl_help.setText("ℹ")
        btn_name_tpl_help.setToolTip(tr("export.filename_template.help.tip"))
        btn_name_tpl_help.clicked.connect(lambda: self._show_filename_template_help(dlg))
        row_name_tpl_lay.addWidget(edit_filename_template, stretch=1)
        row_name_tpl_lay.addWidget(btn_name_tpl_help)
        lay.addWidget(QLabel(tr("export.filename_template.label"), dlg))
        lay.addWidget(row_name_tpl)

        lay.addSpacing(8)
        lb_preview = QLabel("", dlg)
        lb_preview.setWordWrap(True)
        lay.addWidget(lb_preview)

        row_cmd_head = QWidget(dlg)
        row_cmd_head_lay = QHBoxLayout(row_cmd_head)
        row_cmd_head_lay.setContentsMargins(0, 0, 0, 0)
        row_cmd_head_lay.setSpacing(6)
        btn_cmd_drawer = QToolButton(dlg)
        btn_cmd_drawer.setCheckable(True)
        btn_cmd_drawer.setChecked(False)
        btn_cmd_drawer.setArrowType(Qt.ArrowType.RightArrow)
        btn_cmd_drawer.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn_cmd_drawer.setText(tr("export.options.cmd_drawer"))
        btn_copy_cmd = QToolButton(dlg)
        btn_copy_cmd.setText(tr("export.options.copy"))
        btn_copy_cmd.setToolTip(tr("export.options.copy"))
        row_cmd_head_lay.addWidget(btn_cmd_drawer)
        row_cmd_head_lay.addWidget(btn_copy_cmd)
        row_cmd_head_lay.addStretch(1)
        lay.addWidget(row_cmd_head)

        terminal_frame = QFrame(dlg)
        terminal_frame.setFrameShape(QFrame.Shape.StyledPanel)
        terminal_frame.setStyleSheet(
            "QFrame { background:#0f1115; border:1px solid #3a3f4b; border-radius:6px; }"
            "QLabel { color:#cfd3dc; border:none; }"
            "QPlainTextEdit { background:#10151d; color:#a8e6a1; border:1px solid #2f3542; "
            "font-family:Consolas, 'Courier New', monospace; }"
            "QToolButton { color:#cfd3dc; border:none; padding:2px 6px; }"
            "QToolButton:hover { background:#2b313c; border-radius:4px; }"
        )
        terminal_lay = QVBoxLayout(terminal_frame)
        terminal_lay.setContentsMargins(8, 8, 8, 8)
        terminal_lay.setSpacing(6)
        te_cmd = QPlainTextEdit(terminal_frame)
        te_cmd.setReadOnly(True)
        te_cmd.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        te_cmd.setFixedHeight(132)
        terminal_lay.addWidget(te_cmd)
        terminal_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        terminal_frame.setMinimumHeight(0)
        terminal_frame.setMaximumHeight(0)
        terminal_frame.setVisible(False)
        lay.addWidget(terminal_frame)

        row_save_preset = QWidget(dlg)
        row_save_preset_lay = QHBoxLayout(row_save_preset)
        row_save_preset_lay.setContentsMargins(0, 0, 0, 0)
        row_save_preset_lay.setSpacing(8)
        cb_overwrite_preset = QCheckBox(tr("export.options.save_to_loaded_preset"), dlg)
        cb_save_preset = QCheckBox(tr("export.options.save_as_preset"), dlg)
        edit_preset_name = QLineEdit(dlg)
        edit_preset_name.setPlaceholderText(tr("export.options.save_as_preset_placeholder"))
        edit_preset_name.setEnabled(False)
        row_save_preset_lay.addWidget(cb_overwrite_preset)
        row_save_preset_lay.addWidget(cb_save_preset)
        row_save_preset_lay.addWidget(edit_preset_name, stretch=1)
        lay.addWidget(row_save_preset)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            dlg,
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        syncing_dim = [False]
        syncing_edges = [False]
        command_only = [""]
        fallback_note = [""]

        def _resolved_codec() -> str:
            requested = str(cb_codec.currentData() or "auto").strip().lower()
            try:
                return resolve_video_codec(ffmpeg_bin, requested)
            except Exception:
                return requested

        def _quality_symbol_for_codec(codec_key: str) -> str:
            sym = quantizer_symbol_for_codec(codec_key)
            return sym

        def _refresh_quality_annotation() -> None:
            qname = _quality_symbol_for_codec(_resolved_codec())
            rb_quality.setText(tr("export.options.rate_mode.quality_inline", qname=qname))
            lb_quality_info.setToolTip(tr("export.options.video_quality_tip", qname=qname))

        def _apply_source_bitrate_if_needed() -> None:
            if cb_bitrate_match_source.isChecked():
                sb_bitrate.setValue(source_bitrate_kbps)

        def _set_delta_suffix(spin: QSpinBox) -> None:
            v = int(spin.value())
            if v < 0:
                spin.setSuffix(tr("export.options.delta_suffix_crop"))
            elif v > 0:
                spin.setSuffix(tr("export.options.delta_suffix_pad"))
            else:
                spin.setSuffix(tr("export.options.delta_suffix_none"))

        def nearest_multiple(v: int, m: int) -> int:
            if m <= 1:
                return max(1, v)
            low = (v // m) * m
            high = low + m
            if low <= 0:
                return max(1, high)
            return low if (v - low) <= (high - v) else high

        def _content_resolution() -> tuple[int, int]:
            if cb_inherit_res.isChecked():
                return source_vis_w, source_vis_h
            return max(1, sb_w.value()), max(1, sb_h.value())

        def _sync_aspect_from_width() -> None:
            if syncing_dim[0] or not btn_keep_aspect.isChecked() or cb_inherit_res.isChecked():
                return
            syncing_dim[0] = True
            try:
                h = max(1, int(round(sb_w.value() / max(1e-9, source_aspect))))
                sb_h.setValue(h)
            finally:
                syncing_dim[0] = False

        def _sync_aspect_from_height() -> None:
            if syncing_dim[0] or not btn_keep_aspect.isChecked() or cb_inherit_res.isChecked():
                return
            syncing_dim[0] = True
            try:
                w = max(1, int(round(sb_h.value() * source_aspect)))
                sb_w.setValue(w)
            finally:
                syncing_dim[0] = False

        def _clamp_crop_pair(cur: int, crop_a: int, crop_b: int) -> tuple[int, int]:
            crop_a = max(0, int(crop_a))
            crop_b = max(0, int(crop_b))
            total = crop_a + crop_b
            max_total = max(0, cur - 1)
            if total <= max_total:
                return crop_a, crop_b
            if total <= 0:
                return 0, 0
            ratio = max_total / float(total)
            a2 = int(round(crop_a * ratio))
            a2 = max(0, min(max_total, a2))
            b2 = max_total - a2
            return a2, b2

        def _axis_needs(cur: int, m: int) -> tuple[int, int]:
            cur = max(1, int(cur))
            m = max(1, int(m))
            pad_need = (m - (cur % m)) % m
            crop_need = min(cur % m, max(0, cur - 1))
            return pad_need, crop_need

        def _apply_prefer_pad_solution() -> None:
            """Auto-fill edge deltas with a pad-first solution on both axes."""
            if not cb_multiple.isChecked():
                return
            bw, bh = _content_resolution()
            m = max(1, sb_multiple.value())
            pad_x, _ = _axis_needs(bw, m)
            pad_y, _ = _axis_needs(bh, m)
            l = pad_x // 2
            r = pad_x - l
            t = pad_y // 2
            b = pad_y - t
            syncing_edges[0] = True
            try:
                sb_left.setValue(l)
                sb_right.setValue(r)
                sb_top.setValue(t)
                sb_bottom.setValue(b)
            finally:
                syncing_edges[0] = False
            _set_delta_suffix(sb_left)
            _set_delta_suffix(sb_right)
            _set_delta_suffix(sb_top)
            _set_delta_suffix(sb_bottom)

        def _apply_runtime_preset(preset: dict[str, Any], *, recompute_padding: bool) -> None:
            codec = str(preset.get("export_video_codec", "auto") or "auto").strip().lower()
            idx_codec = cb_codec.findData(codec)
            cb_codec.setCurrentIndex(idx_codec if idx_codec >= 0 else 0)
            mode = str(preset.get("export_video_rate_mode", "bitrate") or "bitrate").strip().lower()
            rb_quality.setChecked(mode == "quality")
            rb_bitrate.setChecked(mode != "quality")
            sb_bitrate.setValue(max(300, min(200000, int(preset.get("export_video_bitrate_kbps", source_bitrate_kbps)))))
            cb_bitrate_match_source.setChecked(bool(preset.get("bitrate_match_source", False)))
            sb_quality.setValue(max(0, min(51, int(preset.get("export_video_quality", 23)))))
            sb_fps.setValue(max(1, min(240, int(preset.get("export_fps", self._settings.export_fps())))))
            cb_inherit_fps.setChecked(bool(preset.get("inherit_fps", False)))
            edit_filename_template.setText(
                str(preset.get("export_filename_template", DEFAULT_EXPORT_FILENAME_TEMPLATE) or "").strip()
                or DEFAULT_EXPORT_FILENAME_TEMPLATE
            )
            cb_multiple.setChecked(bool(preset.get("size_multiple_enabled", False)))
            sb_multiple.setValue(max(1, min(4096, int(preset.get("size_multiple_value", 2)))))
            axp = max(1, min(1024, int(preset.get("align_x", 8))))
            ayp = int(preset.get("align_y", 1)) % axp
            arp = str(preset.get("align_round", "ceil") or "ceil")
            if arp not in {"ceil", "floor"}:
                arp = "ceil"
            aap = str(preset.get("align_apply", "tail") or "tail")
            if aap not in {"tail", "head", "symmetric"}:
                aap = "tail"
            cb_export_align_enabled.setChecked(bool(preset.get("align_enabled", True)))
            sb_export_align_x.setValue(axp)
            sb_export_align_y.setValue(ayp)
            cb_export_align_round.setCurrentIndex(0 if arp == "ceil" else 1)
            iap = cb_export_align_apply.findData(aap)
            cb_export_align_apply.setCurrentIndex(iap if iap >= 0 else 0)
            _sync_export_align_y_max()
            if recompute_padding and cb_multiple.isChecked():
                _apply_prefer_pad_solution()
            _refresh_enabled()
            _refresh_export_align_ui_mismatch()

        def _refresh_aspect_lock_ui() -> None:
            if btn_keep_aspect.isChecked():
                btn_keep_aspect.setText("🔒")
                btn_keep_aspect.setToolTip(tr("export.options.keep_aspect_on"))
            else:
                btn_keep_aspect.setText("🔓")
                btn_keep_aspect.setToolTip(tr("export.options.keep_aspect_off"))

        def _sync_axis_partner(driver: QSpinBox, partner: QSpinBox, cur: int) -> None:
            if syncing_edges[0]:
                return
            if not cb_multiple.isChecked():
                return
            m = max(1, sb_multiple.value())
            pad_need, crop_need = _axis_needs(cur, m)
            v = int(driver.value())
            pv = int(partner.value())

            if v > 0:
                v2 = min(v, pad_need)
                p2 = pad_need - v2
            elif v < 0:
                abs_v = min(-v, crop_need)
                v2 = -abs_v
                p2 = -(crop_need - abs_v)
            else:
                if pv < 0 and crop_need > 0:
                    v2 = 0
                    p2 = -crop_need
                elif pv > 0 and pad_need > 0:
                    v2 = 0
                    p2 = pad_need
                else:
                    # Choose the smaller adjustment branch by default.
                    if crop_need > 0 and crop_need < pad_need:
                        v2 = 0
                        p2 = -crop_need
                    else:
                        v2 = 0
                        p2 = pad_need
            syncing_edges[0] = True
            try:
                if v2 != v:
                    driver.setValue(v2)
                partner.setValue(p2)
            finally:
                syncing_edges[0] = False

        def _refresh_adjust_ui() -> None:
            res_related_enabled = not cb_inherit_res.isChecked()
            btn_keep_aspect.setEnabled(res_related_enabled)
            cb_multiple.setEnabled(True)
            sb_multiple.setEnabled(cb_multiple.isChecked())
            gb_balance.setEnabled(cb_multiple.isChecked())
            lb_edge_hint.setEnabled(gb_balance.isEnabled())

        def _build_vf_chain(export_fps: int) -> tuple[str, int, int, int, int]:
            vf_parts = [f"fps={export_fps}", "setpts=PTS-STARTPTS", "scale='round(iw*sar)':ih", "setsar=1"]
            base_w, base_h = _content_resolution()
            if not cb_inherit_res.isChecked():
                vf_parts.append(f"scale={base_w}:{base_h}")
            cur_w, cur_h = base_w, base_h
            if cb_multiple.isChecked():
                m = max(1, sb_multiple.value())
                left_delta = int(sb_left.value())
                right_delta = int(sb_right.value())
                top_delta = int(sb_top.value())
                bottom_delta = int(sb_bottom.value())

                crop_left = max(0, -left_delta)
                crop_right = max(0, -right_delta)
                crop_top = max(0, -top_delta)
                crop_bottom = max(0, -bottom_delta)
                crop_left, crop_right = _clamp_crop_pair(cur_w, crop_left, crop_right)
                crop_top, crop_bottom = _clamp_crop_pair(cur_h, crop_top, crop_bottom)
                if crop_left or crop_right or crop_top or crop_bottom:
                    out_w = max(1, cur_w - crop_left - crop_right)
                    out_h = max(1, cur_h - crop_top - crop_bottom)
                    vf_parts.append(f"crop={out_w}:{out_h}:{crop_left}:{crop_top}")
                    cur_w, cur_h = out_w, out_h

                pad_left = max(0, left_delta)
                pad_right = max(0, right_delta)
                pad_top = max(0, top_delta)
                pad_bottom = max(0, bottom_delta)
                if pad_left or pad_right or pad_top or pad_bottom:
                    vf_parts.append(
                        f"pad=iw+{pad_left + pad_right}:ih+{pad_top + pad_bottom}:{pad_left}:{pad_top}:black"
                    )
                    cur_w, cur_h = max(1, cur_w + pad_left + pad_right), max(1, cur_h + pad_top + pad_bottom)
            return ",".join(vf_parts), base_w, base_h, cur_w, cur_h

        def _refresh_preview() -> None:
            fps_value = source_fps_int if cb_inherit_fps.isChecked() else sb_fps.value()
            vf_chain, content_w, content_h, fw, fh = _build_vf_chain(fps_value)
            codec_req = str(cb_codec.currentData() or "auto").strip().lower()
            codec_resolved = _resolved_codec()
            codec_label = cb_codec.currentText()
            fallback_note[0] = ""
            if codec_req != codec_resolved:
                fallback_note[0] = tr(
                    "export.options.codec_fallback_note",
                    requested=codec_req,
                    resolved=codec_resolved,
                )
                codec_label = tr(
                    "export.options.codec_display_resolved",
                    requested=codec_label,
                    resolved=codec_resolved,
                )
            rate_mode = "quality" if rb_quality.isChecked() else "bitrate"
            qname = _quality_symbol_for_codec(codec_resolved)
            preview_key = (
                "export.options.preview.bitrate"
                if rate_mode == "bitrate"
                else "export.options.preview.quality"
            )
            lb_preview.setText(
                tr(
                    preview_key,
                    fps=fps_value,
                    content_width=content_w,
                    content_height=content_h,
                    width=fw,
                    height=fh,
                    codec=codec_label,
                    mode=tr("export.options.rate_mode.label_bitrate")
                    if rate_mode == "bitrate"
                    else tr("export.options.rate_mode.label_quality"),
                    bitrate=sb_bitrate.value(),
                    quality=sb_quality.value(),
                    qname=qname,
                )
            )
            af_chain = "none"
            if source_has_audio:
                if strong_av_sync:
                    af_chain = "aresample=async=1000:min_hard_comp=0.100:first_pts=0,asetpts=PTS-STARTPTS"
                else:
                    af_chain = "aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS"
            cmd_parts = [
                "ffmpeg",
                "-hide_banner",
                "-fflags",
                "+genpts",
                "-i",
                '"<source>"',
                "-ss",
                "<start_sec>",
                "-t",
                "<duration_sec>",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                f'"{vf_chain}"',
            ]
            v_args = video_codec_args_with_rate_control(
                codec_resolved,
                rate_mode=rate_mode,
                bitrate_kbps=sb_bitrate.value(),
                quality=sb_quality.value(),
                preset="medium",
            )
            cmd_parts.extend(v_args)
            cmd_parts.extend(
                [
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                ]
            )
            if source_has_audio:
                cmd_parts.extend(["-af", f'"{af_chain}"', "-c:a", "aac"])
            if strong_av_sync:
                cmd_parts.extend(["-fps_mode", "cfr"])
            cmd_parts.append('"<output.mp4>"')
            command_only[0] = ("# " + fallback_note[0] + "\n" if fallback_note[0] else "") + " ".join(cmd_parts)
            te_cmd.setPlainText(command_only[0])
            btn_copy_cmd.setToolTip(tr("export.options.copy"))

        def _refresh_enabled() -> None:
            sb_fps.setEnabled(not cb_inherit_fps.isChecked())
            sb_w.setEnabled(not cb_inherit_res.isChecked())
            sb_h.setEnabled(not cb_inherit_res.isChecked())
            mode = "quality" if rb_quality.isChecked() else "bitrate"
            bitrate_mode = mode == "bitrate"
            if bitrate_mode and cb_bitrate_match_source.isChecked() and sb_bitrate.value() != source_bitrate_kbps:
                sb_bitrate.setValue(source_bitrate_kbps)
            sb_bitrate.setEnabled(bitrate_mode and not cb_bitrate_match_source.isChecked())
            cb_bitrate_match_source.setEnabled(bitrate_mode)
            sb_quality.setEnabled(mode == "quality")
            _refresh_adjust_ui()
            _refresh_preview()

        def _set_cmd_drawer_visible(checked: bool) -> None:
            terminal_frame.setVisible(bool(checked))
            terminal_frame.setMinimumHeight(0)
            terminal_frame.setMaximumHeight(16777215 if checked else 0)
            btn_cmd_drawer.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
            )
            dlg.layout().activate()
            dlg.adjustSize()

        def _on_quick_preset_changed() -> None:
            selected = str(cb_quick_preset.currentData() or "").strip()
            if not selected:
                return
            for p in export_presets:
                if str(p.get("id", "") or "").strip() == selected:
                    _apply_runtime_preset(p, recompute_padding=True)
                    break

        def _loaded_preset_id() -> str:
            return str(cb_quick_preset.currentData() or "").strip()

        def _refresh_save_preset_controls() -> None:
            has_loaded = bool(_loaded_preset_id())
            cb_overwrite_preset.setEnabled(has_loaded)
            if not has_loaded:
                cb_overwrite_preset.setChecked(False)
            edit_preset_name.setEnabled(bool(cb_save_preset.isChecked()))

        cb_inherit_fps.toggled.connect(_refresh_enabled)
        cb_inherit_fps.toggled.connect(lambda _c: _refresh_export_align_ui_mismatch())
        cb_save_preset.toggled.connect(
            lambda checked: (
                cb_overwrite_preset.setChecked(False) if checked and cb_overwrite_preset.isChecked() else None,
                _refresh_save_preset_controls(),
            )
        )
        cb_overwrite_preset.toggled.connect(
            lambda checked: cb_save_preset.setChecked(False) if checked and cb_save_preset.isChecked() else None
        )
        cb_inherit_res.toggled.connect(
            lambda _checked: (
                _apply_prefer_pad_solution(),
                _refresh_enabled(),
            )
        )
        btn_keep_aspect.toggled.connect(
            lambda _checked: (
                _refresh_aspect_lock_ui(),
                _refresh_enabled(),
            )
        )
        cb_multiple.toggled.connect(
            lambda checked: (
                _apply_prefer_pad_solution() if checked else None,
                _refresh_enabled(),
            )
        )
        sb_multiple.valueChanged.connect(
            lambda _v: (
                _apply_prefer_pad_solution(),
                _refresh_adjust_ui(),
                _refresh_preview(),
            )
        )
        cb_codec.currentIndexChanged.connect(lambda _i: _refresh_preview())
        cb_codec.currentIndexChanged.connect(lambda _i: _refresh_quality_annotation())
        rb_bitrate.toggled.connect(lambda _checked: _refresh_enabled())
        rb_quality.toggled.connect(lambda _checked: _refresh_enabled())
        cb_bitrate_match_source.toggled.connect(
            lambda checked: (
                _apply_source_bitrate_if_needed() if checked else None,
                _refresh_enabled(),
            )
        )
        sb_bitrate.valueChanged.connect(lambda _v: _refresh_preview())
        sb_quality.valueChanged.connect(lambda _v: _refresh_preview())
        sb_fps.valueChanged.connect(lambda _v: (_refresh_preview(), _refresh_export_align_ui_mismatch()))
        sb_w.valueChanged.connect(
            lambda _v: (
                _sync_aspect_from_width(),
                _apply_prefer_pad_solution(),
                _refresh_adjust_ui(),
                _refresh_preview(),
            )
        )
        sb_h.valueChanged.connect(
            lambda _v: (
                _sync_aspect_from_height(),
                _apply_prefer_pad_solution(),
                _refresh_adjust_ui(),
                _refresh_preview(),
            )
        )
        sb_left.valueChanged.connect(
            lambda _v: (
                _sync_axis_partner(sb_left, sb_right, _content_resolution()[0]),
                _refresh_preview(),
            )
        )
        sb_right.valueChanged.connect(
            lambda _v: (
                _sync_axis_partner(sb_right, sb_left, _content_resolution()[0]),
                _refresh_preview(),
            )
        )
        sb_top.valueChanged.connect(
            lambda _v: (
                _sync_axis_partner(sb_top, sb_bottom, _content_resolution()[1]),
                _refresh_preview(),
            )
        )
        sb_bottom.valueChanged.connect(
            lambda _v: (
                _sync_axis_partner(sb_bottom, sb_top, _content_resolution()[1]),
                _refresh_preview(),
            )
        )
        sb_left.valueChanged.connect(lambda _v: _set_delta_suffix(sb_left))
        sb_right.valueChanged.connect(lambda _v: _set_delta_suffix(sb_right))
        sb_top.valueChanged.connect(lambda _v: _set_delta_suffix(sb_top))
        sb_bottom.valueChanged.connect(lambda _v: _set_delta_suffix(sb_bottom))
        btn_cmd_drawer.toggled.connect(_set_cmd_drawer_visible)
        sb_export_align_x.valueChanged.connect(
            lambda _v: (_sync_export_align_y_max(), _refresh_export_align_ui_mismatch())
        )
        sb_export_align_y.valueChanged.connect(lambda _v: _refresh_export_align_ui_mismatch())
        cb_export_align_round.currentIndexChanged.connect(lambda _i: _refresh_export_align_ui_mismatch())
        cb_export_align_apply.currentIndexChanged.connect(lambda _i: _refresh_export_align_ui_mismatch())
        cb_quick_preset.currentIndexChanged.connect(lambda _i: _on_quick_preset_changed())
        cb_quick_preset.currentIndexChanged.connect(lambda _i: _refresh_save_preset_controls())
        btn_copy_cmd.clicked.connect(
            lambda: (
                QApplication.clipboard().setText(command_only[0]),
                btn_copy_cmd.setToolTip(tr("export.options.copied")),
                QTimer.singleShot(1200, lambda: btn_copy_cmd.setToolTip(tr("export.options.copy"))),
            )
        )

        if cb_multiple.isChecked():
            _apply_prefer_pad_solution()
        _refresh_enabled()
        _apply_source_bitrate_if_needed()
        _refresh_quality_annotation()
        _refresh_aspect_lock_ui()
        _set_delta_suffix(sb_left)
        _set_delta_suffix(sb_right)
        _set_delta_suffix(sb_top)
        _set_delta_suffix(sb_bottom)
        _set_cmd_drawer_visible(False)
        _refresh_save_preset_controls()
        _refresh_export_align_ui_mismatch()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        axk = max(1, sb_export_align_x.value())
        align_export_enabled = cb_export_align_enabled.isChecked()
        align_export_x = axk
        align_export_y = int(sb_export_align_y.value()) % axk
        align_export_round = str(cb_export_align_round.currentData() or "ceil")
        align_export_apply = str(cb_export_align_apply.currentData() or "tail")

        export_video_codec = str(cb_codec.currentData() or "auto").strip().lower()
        export_video_rate_mode = "quality" if rb_quality.isChecked() else "bitrate"
        export_video_bitrate_kbps = max(
            300,
            min(200000, int(source_bitrate_kbps if cb_bitrate_match_source.isChecked() else sb_bitrate.value())),
        )
        export_video_quality = max(0, min(51, int(sb_quality.value())))
        export_filename_template = (
            edit_filename_template.text().strip() or DEFAULT_EXPORT_FILENAME_TEMPLATE
        )
        export_fps = source_fps_int if cb_inherit_fps.isChecked() else max(1, min(240, int(sb_fps.value())))
        if cb_overwrite_preset.isChecked():
            target_id = _loaded_preset_id()
            target_name = tr("export.preset.default_name")
            for p in export_presets:
                if str(p.get("id", "") or "").strip() == target_id:
                    target_name = str(p.get("name", "") or "").strip() or tr("export.preset.default_name")
                    break
            if target_id:
                payload = self._make_export_preset_payload(
                    name=target_name,
                    export_fps=max(1, min(240, int(sb_fps.value()))),
                    inherit_fps=cb_inherit_fps.isChecked(),
                    export_video_codec=export_video_codec,
                    export_video_rate_mode=export_video_rate_mode,
                    export_video_bitrate_kbps=export_video_bitrate_kbps,
                    bitrate_match_source=cb_bitrate_match_source.isChecked(),
                    export_video_quality=export_video_quality,
                    export_filename_template=export_filename_template,
                    size_multiple_enabled=cb_multiple.isChecked(),
                    size_multiple_value=sb_multiple.value(),
                    align_enabled=align_export_enabled,
                    align_x=align_export_x,
                    align_y=align_export_y,
                    align_round=align_export_round,
                    align_apply=align_export_apply,
                    preset_id=target_id,
                )
                self._save_export_preset(payload, set_as_default=False)
        elif cb_save_preset.isChecked():
            preset_name = edit_preset_name.text().strip() or tr("export.preset.default_name")
            payload = self._make_export_preset_payload(
                name=preset_name,
                export_fps=max(1, min(240, int(sb_fps.value()))),
                inherit_fps=cb_inherit_fps.isChecked(),
                export_video_codec=export_video_codec,
                export_video_rate_mode=export_video_rate_mode,
                export_video_bitrate_kbps=export_video_bitrate_kbps,
                bitrate_match_source=cb_bitrate_match_source.isChecked(),
                export_video_quality=export_video_quality,
                export_filename_template=export_filename_template,
                size_multiple_enabled=cb_multiple.isChecked(),
                size_multiple_value=sb_multiple.value(),
                align_enabled=align_export_enabled,
                align_x=align_export_x,
                align_y=align_export_y,
                align_round=align_export_round,
                align_apply=align_export_apply,
                preset_id=None,
            )
            self._save_export_preset(payload, set_as_default=True)

        vf_chain, _, _, _, _ = _build_vf_chain(export_fps)
        return ExportTaskOptions(
            export_fps=export_fps,
            export_video_codec=export_video_codec,
            export_video_bitrate_kbps=export_video_bitrate_kbps,
            export_video_rate_mode=export_video_rate_mode,
            export_video_quality=export_video_quality,
            export_filename_template=export_filename_template,
            video_filter=vf_chain,
            align_enabled=align_export_enabled,
            align_x=align_export_x,
            align_y=align_export_y,
            align_round=align_export_round,
            align_apply=align_export_apply,
        )

    def _start_export_clips(self, *, strong_av_sync: bool) -> None:
        if not self._data.source_path:
            return
        export_title = tr("menu.export_strong") if strong_av_sync else tr("menu.export")
        self._export_action_title = export_title
        if self._export_thread and self._export_thread.isRunning():
            QMessageBox.information(self, export_title, tr("export.busy"))
            return
        opts = self._show_export_task_options(strong_av_sync=strong_av_sync)
        if opts is None:
            return
        start = self._settings.last_export_dir() or ""
        d = QFileDialog.getExistingDirectory(self, export_title, start)
        if not d:
            return
        self._settings.set_last_export_dir(d)
        if not any(c.end_frame is not None for c in self._data.clips):
            QMessageBox.information(self, export_title, tr("export.no_clips"))
            return
        n_clips = sum(1 for c in self._data.clips if c.end_frame is not None)
        export_debug_log(
            "ui.export.start",
            source=Path(self._data.source_path).name if self._data.source_path else "",
            clips=n_clips,
            fps=opts.export_fps,
            video_codec=opts.export_video_codec,
            video_rate_mode=opts.export_video_rate_mode,
            video_bitrate_kbps=opts.export_video_bitrate_kbps,
            video_quality=opts.export_video_quality,
            filename_template=opts.export_filename_template,
            video_filter=opts.video_filter,
            log_path=str(export_debug_log_path()),
        )
        cancel_flag = [False]
        prog = QProgressDialog(self)
        prog.setWindowTitle(export_title)
        prog.setLabelText(tr("export.progress_detail", current=1, total=max(1, n_clips)))
        prog.setRange(0, 1000)
        prog.setValue(0)
        prog.setMinimumDuration(0)
        prog.setCancelButtonText(tr("export.cancel"))
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.canceled.connect(lambda: cancel_flag.__setitem__(0, True))
        prog.canceled.connect(lambda: export_debug_log("ui.export.cancel_requested"))

        self._export_progress = prog
        self._btn_export_sidebar.setEnabled(False)
        if self._act_export is not None:
            self._act_export.setEnabled(False)
        if self._act_export_strong is not None:
            self._act_export_strong.setEnabled(False)

        thread = QThread(self)
        worker = ExportWorker(
            self._data,
            self._tb,
            Path(d),
            self._settings.warn_align_8n1(),
            opts.export_fps,
            opts.export_video_codec,
            opts.export_video_bitrate_kbps,
            opts.export_video_rate_mode,
            opts.export_video_quality,
            opts.export_filename_template,
            opts.video_filter,
            opts.align_enabled,
            opts.align_x,
            opts.align_y,
            opts.align_round,
            opts.align_apply,
            strong_av_sync,
            cancel_flag,
            thread,
            self._export_progress_bridge,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run_export)
        worker.progress.connect(
            self._on_export_progress,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.succeeded.connect(self._on_export_succeeded)
        worker.failed.connect(self._on_export_failed)
        worker.cancelled.connect(self._on_export_cancelled)
        worker.finished_done.connect(thread.quit, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_export_thread_finished)

        self._export_thread = thread
        self._export_worker = worker
        thread.start()
        prog.show()

    @Slot()
    def _on_export_thread_finished(self) -> None:
        sender = self.sender()
        if sender is self._export_thread:
            self._export_thread = None
            self._export_worker = None

    @Slot(int, int, int, float)
    def _on_export_clip_meta(self, current: int, total: int, clip_frames: int, duration_sec: float) -> None:
        self._export_clip_ui = (current, total, clip_frames, duration_sec)
        self._export_stderr_last_ff = None
        if current != self._export_ui_last_current:
            self._export_ui_last_current = current
            self._export_ui_last_frame = 0
        export_debug_log(
            "ui.export.clip_begin",
            current=current,
            total=total,
            clip_frames=clip_frames,
            duration_sec=f"{duration_sec:.6f}",
        )

    @Slot(str)
    def _on_export_stderr_line(self, line: str) -> None:
        ctx = self._export_clip_ui
        if ctx is None:
            return
        current, total, L, dur = ctx
        tick = stderr_encoding_stats_tick(line, dur)
        if tick is None:
            return
        export_debug_log(
            "ui.export.stderr_tick",
            current=current,
            total=total,
            frame=tick.out_frame,
            out_time_sec=f"{tick.out_time_sec:.6f}",
        )
        last_ff = self._export_stderr_last_ff
        if tick.out_frame is not None:
            ff = min(L, max(0, tick.out_frame))
            if ff > 0:
                last_ff = max(last_ff or 0, ff)
        self._export_stderr_last_ff = last_ff
        if last_ff is not None and last_ff > 0:
            frac = min(1.0, last_ff / float(L)) if L > 0 else 1.0
            f_show = last_ff
        else:
            frac = min(1.0, max(0.0, tick.out_time_sec / dur)) if dur > 1e-12 else 0.0
            est = int(round(tick.out_time_sec / dur * float(L))) if dur > 1e-12 else 0
            f_show = min(L, max(0, est))
        idx0 = current - 1
        n = max(1, total)
        overall = (idx0 + frac) / n
        self._on_export_progress(overall, current, total, f_show, L)

    def _finish_export_ui(self) -> None:
        export_debug_log("ui.export.finish_ui")
        if self._export_progress is not None:
            self._export_progress.close()
            self._export_progress.deleteLater()
            self._export_progress = None
        self._export_clip_ui = None
        self._export_stderr_last_ff = None
        self._export_ui_last_current = 0
        self._export_ui_last_frame = 0
        self._export_ui_last_overall = 0.0
        self._btn_export_sidebar.setEnabled(True)
        if self._act_export is not None:
            self._act_export.setEnabled(True)
        if self._act_export_strong is not None:
            self._act_export_strong.setEnabled(True)

    @Slot(float, int, int, int, int)
    def _on_export_progress(
        self, overall: float, current: int, total: int, frame: int, frame_total: int
    ) -> None:
        dlg = self._export_progress
        if dlg is None:
            return
        if current == self._export_ui_last_current:
            frame = max(frame, self._export_ui_last_frame)
        else:
            self._export_ui_last_current = current
            self._export_ui_last_frame = 0
        self._export_ui_last_frame = frame
        overall = max(overall, self._export_ui_last_overall)
        self._export_ui_last_overall = overall
        if frame < 0:
            dlg.setLabelText(
                tr(
                    "export.progress_detail_seeking",
                    current=current,
                    total=total,
                )
            )
        else:
            dlg.setLabelText(
                tr(
                    "export.progress_detail_frames",
                    current=current,
                    total=total,
                    frame=frame,
                    frame_total=frame_total,
                )
            )
        dlg.setValue(int(max(0.0, min(1.0, overall)) * 1000))
        now = time.monotonic()
        if now - self._export_debug_last_ui_tick_mono >= 0.2:
            self._export_debug_last_ui_tick_mono = now
            export_debug_log(
                "ui.export.progress_tick",
                overall=f"{max(0.0, min(1.0, overall)):.4f}",
                current=current,
                total=total,
                frame=frame,
                frame_total=frame_total,
            )
        QCoreApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents,
            4,
        )

    @Slot(list)
    def _on_export_succeeded(self, msgs: list[Any]) -> None:
        export_debug_log("ui.export.succeeded", warnings=len(msgs))
        self._finish_export_ui()
        text = tr("export.done")
        pairs = [(str(a), str(b)) for a, b in msgs]
        if pairs:
            text += "\n" + tr("export.warn") + ":\n" + "\n".join(f"{a}: {b}" for a, b in pairs)
        QMessageBox.information(self, self._export_action_title, text)

    @Slot(str)
    def _on_export_failed(self, err: str) -> None:
        export_debug_log("ui.export.failed", error=err)
        self._finish_export_ui()
        QMessageBox.critical(self, tr("export.failed"), err)

    @Slot()
    def _on_export_cancelled(self) -> None:
        export_debug_log("ui.export.cancelled")
        self._finish_export_ui()
        QMessageBox.information(self, self._export_action_title, tr("export.cancelled"))

    def _status(self, s: str) -> None:
        self._status_label.setText(s)
