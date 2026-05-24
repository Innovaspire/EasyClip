"""LLM interaction panel: preset selector, generate button, and API call worker.

API format extensibility:
    To add a new API format, add entries in:
    - ``_build_chat_url()`` — chat completions endpoint URL
    - ``_build_chat_body()`` — request body construction
    - ``_LLMCallWorker._call_api()`` — dispatch to a new ``_call_<format>()`` handler
"""

from __future__ import annotations

import json
import urllib.request

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QWidget,
)

from easyclip.annotation.settings import AnnotationSettings, LLMPreset
from easyclip.i18n.strings import tr


# ── API format dispatch: URL & body construction ─────────────────────


def _build_chat_url(base_url: str, api_format: str) -> str:
    """Build the chat completions endpoint URL for a given API format."""
    base = base_url.rstrip("/")
    if api_format == "openai_compatible":
        # Standard OpenAI API prefix: /v1/chat/completions
        if not base.endswith("/v1"):
            base += "/v1"
        return f"{base}/chat/completions"
    # Future formats:
    # elif api_format == "anthropic":
    #     return f"{base}/v1/messages"
    raise NotImplementedError(f"Chat API not implemented for format: {api_format}")


def _build_chat_body(
    model: str,
    messages: list[dict],
    api_format: str,
    *,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    stream: bool = False,
    enable_thinking: bool = True,
) -> str:
    """Build the JSON request body for a chat completion request.

    Returns a JSON-encoded byte string.
    """
    if api_format == "openai_compatible":
        body: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "enable_thinking": enable_thinking,
        }
        if stream:
            body["stream"] = True
        return json.dumps(body).encode("utf-8")
    raise NotImplementedError(f"Chat body not implemented for format: {api_format}")


class _LLMCallWorker(QThread):
    """Worker thread for LLM API calls.

    Receives a pre-built ``content`` list (OpenAI-compatible content array)
    instead of assembling it from raw frames/drafts.

    Two modes:
    - Non-streaming: emits ``result(str)`` with the full response.
    - Streaming: emits ``chunk(str, str)`` for each content/reasoning delta,
      then ``stream_done()`` when the stream ends.

    API format dispatch: ``_call_api()`` routes to format-specific handlers.
    To add a new format, add a ``_call_<format>()`` method and a branch in
    ``_call_api()``.
    """

    result = Signal(str)          # Non-streaming: full response text
    chunk = Signal(str, str)      # Streaming: (content_delta, reasoning_delta)
    stream_done = Signal()        # Streaming: emitted when stream completes
    error = Signal(str)

    def __init__(
        self,
        preset: LLMPreset,
        system_prompt: str,
        content: list[dict],
    ) -> None:
        super().__init__()
        self._preset = preset
        self._system_prompt = system_prompt
        self._content = content

    def run(self) -> None:
        try:
            if self._preset.streaming:
                self._call_streaming()
            else:
                text = self._call_api()
                self.result.emit(text)
        except Exception as e:
            self.error.emit(str(e))

    def _call_api(self) -> str:
        """Dispatch to the correct API handler based on ``api_format``."""
        fmt = self._preset.api_format
        if fmt == "openai_compatible":
            return self._call_openai_compatible()
        raise NotImplementedError(f"API format not implemented: {fmt}")

    def _call_streaming(self) -> None:
        """Streaming dispatch — calls format-specific streaming handler."""
        fmt = self._preset.api_format
        if fmt == "openai_compatible":
            self._call_openai_streaming()
        else:
            raise NotImplementedError(f"Streaming not implemented for format: {fmt}")

    def _build_messages(self) -> list[dict]:
        messages: list[dict] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": self._content})
        return messages

    def _call_openai_compatible(self) -> str:
        """Call an OpenAI-compatible chat completions endpoint (non-streaming)."""
        url = _build_chat_url(self._preset.base_url, self._preset.api_format)
        messages = self._build_messages()
        body = _build_chat_body(
            model=self._preset.model,
            messages=messages,
            api_format=self._preset.api_format,
            stream=False,
            enable_thinking=self._preset.enable_thinking,
        )
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self._preset.api_key:
            req.add_header("Authorization", f"Bearer {self._preset.api_key}")
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]

    def _call_openai_streaming(self) -> None:
        """Call OpenAI-compatible endpoint with SSE streaming."""
        url = _build_chat_url(self._preset.base_url, self._preset.api_format)
        messages = self._build_messages()
        body = _build_chat_body(
            model=self._preset.model,
            messages=messages,
            api_format=self._preset.api_format,
            stream=True,
            enable_thinking=self._preset.enable_thinking,
        )
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self._preset.api_key:
            req.add_header("Authorization", f"Bearer {self._preset.api_key}")

        with urllib.request.urlopen(req, timeout=300) as resp:
            buffer = b""
            while True:
                # Read in chunks to avoid blocking the event loop entirely;
                # QThread ensures signals are delivered between reads.
                chunk_data = resp.read(4096)
                if not chunk_data:
                    break
                if self.isInterruptionRequested():
                    return
                buffer += chunk_data
                while b"\n" in buffer:
                    line_bytes, buffer = buffer.split(b"\n", 1)
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            self.stream_done.emit()
                            return
                        try:
                            data = json.loads(data_str)
                            choices = data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                reasoning = delta.get("reasoning_content", "")
                                if content or reasoning:
                                    self.chunk.emit(content, reasoning)
                        except (json.JSONDecodeError, IndexError, KeyError):
                            pass  # Skip malformed SSE chunks
        self.stream_done.emit()


class LLMPanel(QWidget):
    """LLM generate button (preset selector is in the left panel)."""

    generate_requested = Signal()
    preview_draft_requested = Signal()

    # Forwarded from worker for streaming
    chunk_received = Signal(str, str)   # (content_delta, reasoning_delta)
    stream_finished = Signal()

    def __init__(self, settings: AnnotationSettings, preset_combo: QComboBox | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._worker: _LLMCallWorker | None = None
        self._preset_combo = preset_combo
        self._elapsed_timer: QTimer | None = None
        self._elapsed_start: float = 0.0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addStretch()

        # ── Omni video transcode toggles (hidden when preset is not Omni) ──
        self._chk_reduce_resolution = QCheckBox(tr("annotation.omni.reduce_resolution"))
        self._chk_reduce_resolution.setToolTip(tr("annotation.omni.reduce_resolution_tip"))
        self._chk_reduce_bitrate = QCheckBox(tr("annotation.omni.reduce_bitrate"))
        self._chk_reduce_bitrate.setToolTip(tr("annotation.omni.reduce_bitrate_tip"))
        layout.addWidget(self._chk_reduce_resolution)
        layout.addWidget(self._chk_reduce_bitrate)
        layout.addSpacing(4)

        self._btn_preview = QPushButton(tr("annotation.preview_draft"))
        self._btn_preview.setEnabled(False)
        self._btn_preview.clicked.connect(self.preview_draft_requested.emit)
        layout.addWidget(self._btn_preview)

        self._btn_generate = QPushButton(tr("annotation.generate"))
        self._btn_generate.setEnabled(False)
        layout.addWidget(self._btn_generate)
        layout.addSpacing(8)

        if self._preset_combo is not None:
            self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        self._btn_generate.clicked.connect(self.generate_requested.emit)

        self._refresh_presets()

    def active_preset(self) -> LLMPreset | None:
        return self._settings.active_llm_preset()

    def call_llm(
        self,
        system_prompt: str,
        content: list[dict],
        on_result: callable,
        on_error: callable,
    ) -> None:
        """Call the LLM with a pre-built content array.

        In streaming mode, ``chunk_received`` and ``stream_finished`` signals
        are emitted during the response; callers should connect to those instead
        of relying solely on *on_result* (which is only called in non-streaming
        mode or for backward compatibility).

        Args:
            system_prompt: Project-level system prompt.
            content: OpenAI-compatible content array (interleaved images + text).
            on_result: Callback receiving the full response text (non-streaming only).
            on_error: Callback receiving an error message string.
        """
        preset = self.active_preset()
        if preset is None:
            on_error(tr("annotation.no_preset"))
            return

        is_streaming = preset.streaming

        self._enter_loading_state()

        self._worker = _LLMCallWorker(preset, system_prompt, content)

        if is_streaming:
            self._worker.chunk.connect(self.chunk_received.emit)
            self._worker.stream_done.connect(self._on_stream_done)
        self._worker.result.connect(lambda t: self._on_worker_done(t, on_result))
        self._worker.error.connect(lambda e: self._on_worker_error(e, on_error))
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _enter_loading_state(self) -> None:
        """Disable UI and start elapsed timer."""
        import time as _time
        self._elapsed_start = _time.monotonic()
        base = tr("annotation.llm_waiting")
        self._btn_generate.setText(base)
        self._btn_generate.setEnabled(False)
        self._btn_preview.setEnabled(False)
        if self._preset_combo is not None:
            self._preset_combo.setEnabled(False)
        # Start elapsed timer (0.1 s interval)
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed_label)
        self._elapsed_timer.start(100)

    def _update_elapsed_label(self) -> None:
        import time as _time
        elapsed = _time.monotonic() - self._elapsed_start
        base = tr("annotation.llm_waiting")
        self._btn_generate.setText(f"{base} ({elapsed:.1f}s)")

    def _stop_elapsed_timer(self) -> None:
        if self._elapsed_timer is not None:
            self._elapsed_timer.stop()
            self._elapsed_timer = None

    def _restore_ui_state(self) -> None:
        """Restore button and combo states after LLM call completes."""
        self._stop_elapsed_timer()
        self._btn_generate.setText(tr("annotation.generate"))
        self._btn_generate.setEnabled(True)
        self._btn_preview.setEnabled(True)
        if self._preset_combo is not None:
            self._preset_combo.setEnabled(True)

    def _connect_streaming(self, on_chunk: callable, on_done: callable) -> None:
        """Safely connect streaming signals, clearing any prior connections.

        Uses ``try/except`` + ``warnings`` suppression to avoid the
        ``RuntimeWarning`` PySide6 emits when disconnecting a signal that
        has no connected slots.
        """
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                self.chunk_received.disconnect()
                self.stream_finished.disconnect()
            except (RuntimeError, TypeError):
                pass
        self.chunk_received.connect(on_chunk)
        self.stream_finished.connect(on_done)

    def _on_stream_done(self) -> None:
        """Streaming completed — restore UI and emit signal."""
        self._restore_ui_state()
        self.stream_finished.emit()

    def _on_worker_done(self, text: str, on_result: callable) -> None:
        self._restore_ui_state()
        on_result(text)

    def _on_worker_error(self, error_msg: str, on_error: callable) -> None:
        self._restore_ui_state()
        on_error(error_msg)
        # Show copyable error dialog
        self._show_error_dialog(error_msg)

    def _on_worker_finished(self) -> None:
        """Clean up worker reference. Called whether result or error was emitted."""
        self._worker = None

    def _show_error_dialog(self, error_msg: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(tr("annotation.llm_error_title"))
        box.setText(error_msg)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        box.exec()

    def omni_video_options(self) -> dict:
        """Return the current Omni video transcode options.

        Returns a dict with keys ``reduce_resolution`` and ``reduce_bitrate``.
        Both are False when the active preset is not an Omni model.
        """
        preset = self.active_preset()
        if preset is None or not preset.is_omni_model:
            return {"reduce_resolution": False, "reduce_bitrate": False}
        return {
            "reduce_resolution": self._chk_reduce_resolution.isChecked(),
            "reduce_bitrate": self._chk_reduce_bitrate.isChecked(),
        }

    def _sync_omni_checkboxes(self) -> None:
        """Show/hide Omni video transcode checkboxes based on active preset."""
        preset = self.active_preset()
        visible = preset is not None and preset.is_omni_model
        self._chk_reduce_resolution.setVisible(visible)
        self._chk_reduce_bitrate.setVisible(visible)

    def _on_preset_selected(self, index: int) -> None:
        """Persist the user's preset selection to settings."""
        if index < 0 or self._preset_combo is None:
            return
        preset_id = self._preset_combo.itemData(index)
        if preset_id:
            self._settings.set_active_llm_preset_id(preset_id)
        self._sync_omni_checkboxes()

    def _refresh_presets(self) -> None:
        ok = False
        if self._preset_combo is not None:
            self._preset_combo.blockSignals(True)
            self._preset_combo.clear()
            presets = self._settings.llm_presets()
            active_id = self._settings.active_llm_preset_id()
            for i, p in enumerate(presets):
                self._preset_combo.addItem(p.name, p.id)
                if p.id == active_id:
                    self._preset_combo.setCurrentIndex(i)
            self._preset_combo.blockSignals(False)
            ok = len(presets) > 0
        self._btn_generate.setEnabled(ok)
        self._btn_preview.setEnabled(ok)
        self._sync_omni_checkboxes()

    def teardown(self) -> None:
        """Cancel any in-flight LLM request. Safe to call multiple times."""
        if self._worker is not None and self._worker.isRunning():
            try:
                self._worker.result.disconnect()
                self._worker.chunk.disconnect()
                self._worker.stream_done.disconnect()
                self._worker.error.disconnect()
                self._worker.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            self._worker.requestInterruption()
            self._worker.wait(3000)
            self._worker = None
        self._restore_ui_state()

    def refresh_language(self) -> None:
        self._btn_generate.setText(tr("annotation.generate"))
        self._btn_preview.setText(tr("annotation.preview_draft"))
        self._chk_reduce_resolution.setText(tr("annotation.omni.reduce_resolution"))
        self._chk_reduce_resolution.setToolTip(tr("annotation.omni.reduce_resolution_tip"))
        self._chk_reduce_bitrate.setText(tr("annotation.omni.reduce_bitrate"))
        self._chk_reduce_bitrate.setToolTip(tr("annotation.omni.reduce_bitrate_tip"))
