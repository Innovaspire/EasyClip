"""Annotation-specific settings stored in QSettings("Innovaspire", "EasyClip/Annotation")."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSettings


# ── Omni media format keys ──────────────────────────────────────
# When adding a new omni media format, add a key here and
# a corresponding branch in build_llm_content() for how
# audio/video content parts are assembled.

OMNI_MEDIA_FORMAT_QWEN = "qwen_omni"

_OMNI_MEDIA_FORMATS = frozenset({OMNI_MEDIA_FORMAT_QWEN})
# Future: "qwen_omni_v2", "gemini_multimodal", ...

# ── Omni video transcode thresholds ────────────────────────────
# Applied when the user enables the corresponding checkbox in the
# LLM panel.  Values are *ceilings* — content already below the
# threshold is left untouched.

OMNI_MAX_HEIGHT = 480           # px — scale down if vertical res exceeds this
OMNI_MAX_BITRATE_KBPS = 1500    # kbps — -maxrate cap (2× bufsize)


@dataclass
class LLMPreset:
    """A user-configured LLM API preset."""

    id: str = ""
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    api_format: str = "openai_compatible"
    model: str = ""
    streaming: bool = True
    enable_thinking: bool = True
    cached_models: list[str] = field(default_factory=list)
    is_omni_model: bool = False
    omni_media_format: str = OMNI_MEDIA_FORMAT_QWEN

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "api_format": self.api_format,
            "model": self.model,
            "streaming": self.streaming,
            "enable_thinking": self.enable_thinking,
            "cached_models": self.cached_models,
            "is_omni_model": self.is_omni_model,
            "omni_media_format": self.omni_media_format,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LLMPreset:
        omni_fmt = d.get("omni_media_format", OMNI_MEDIA_FORMAT_QWEN)
        if omni_fmt not in _OMNI_MEDIA_FORMATS:
            omni_fmt = OMNI_MEDIA_FORMAT_QWEN
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            base_url=d.get("base_url", ""),
            api_key=d.get("api_key", ""),
            api_format=d.get("api_format", "openai_compatible"),
            model=d.get("model", ""),
            streaming=d.get("streaming", True),
            enable_thinking=d.get("enable_thinking", True),
            cached_models=d.get("cached_models", []),
            is_omni_model=d.get("is_omni_model", False),
            omni_media_format=omni_fmt,
        )


class AnnotationSettings:
    """Typed getter/setter for annotation preferences."""

    def __init__(self) -> None:
        self._qs = QSettings("Innovaspire", "EasyClip/Annotation")

    # ── default system prompt ────────────────────────────────────

    def default_system_prompt(self) -> str:
        return self._qs.value("default_system_prompt", "", str)

    def set_default_system_prompt(self, text: str) -> None:
        self._qs.setValue("default_system_prompt", text)

    # ── LLM presets ──────────────────────────────────────────────

    def llm_presets(self) -> list[LLMPreset]:
        raw = self._qs.value("llm_presets", "[]", str)
        try:
            items = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        return [LLMPreset.from_dict(d) for d in items]

    def set_llm_presets(self, presets: list[LLMPreset]) -> None:
        data = json.dumps([p.to_dict() for p in presets], ensure_ascii=False)
        self._qs.setValue("llm_presets", data)

    def active_llm_preset_id(self) -> str:
        return self._qs.value("active_llm_preset_id", "", str)

    def set_active_llm_preset_id(self, preset_id: str) -> None:
        self._qs.setValue("active_llm_preset_id", preset_id)

    def active_llm_preset(self) -> LLMPreset | None:
        pid = self.active_llm_preset_id()
        for p in self.llm_presets():
            if p.id == pid:
                return p
        presets = self.llm_presets()
        return presets[0] if presets else None

    def save_llm_preset(self, preset: LLMPreset, *, set_as_default: bool = True) -> str:
        """Replace or append a preset. Returns the preset id."""
        import uuid as _uuid
        if not preset.id:
            preset.id = str(_uuid.uuid4())
        presets = self.llm_presets()
        replaced = False
        for i, p in enumerate(presets):
            if p.id == preset.id:
                presets[i] = preset
                replaced = True
                break
        if not replaced:
            presets.append(preset)
        self.set_llm_presets(presets)
        if set_as_default:
            self.set_active_llm_preset_id(preset.id)
        return preset.id

    def new_llm_preset_template(self) -> LLMPreset:
        """Return a blank LLMPreset for the 'new preset' form."""
        return LLMPreset(
            id="",
            name="",
            base_url="",
            api_key="",
            api_format="openai_compatible",
            model="",
            streaming=True,
            enable_thinking=True,
            is_omni_model=False,
            omni_media_format=OMNI_MEDIA_FORMAT_QWEN,
        )

    # ── project directory mode ───────────────────────────────────

    def project_dir_mode(self) -> str:
        return self._qs.value("project_dir_mode", "home_default", str)

    def set_project_dir_mode(self, mode: str) -> None:
        self._qs.setValue("project_dir_mode", mode)

    # ── startup behavior ─────────────────────────────────────────

    def startup_behavior(self) -> str:
        return self._qs.value("startup_behavior", "ask", str)

    def set_startup_behavior(self, behavior: str) -> None:
        self._qs.setValue("startup_behavior", behavior)

    # ── undo max steps ───────────────────────────────────────────

    def undo_max_steps(self) -> int:
        return self._qs.value("undo_max_steps", 50, int)

    def set_undo_max_steps(self, steps: int) -> None:
        self._qs.setValue("undo_max_steps", max(10, min(500, steps)))

    # ── last open directory ──────────────────────────────────────

    def last_open_dir(self) -> str:
        return self._qs.value("last_open_dir", "", str)

    def set_last_open_dir(self, path: str) -> None:
        self._qs.setValue("last_open_dir", path)

    # ── last open annotation project dir ─────────────────────────

    def last_open_project_dir(self) -> str:
        return self._qs.value("last_open_project_dir", "", str)

    def set_last_open_project_dir(self, path: str) -> None:
        self._qs.setValue("last_open_project_dir", path)

    # ── last selected clip path ──────────────────────────────────

    def last_selected_clip_path(self) -> str:
        return self._qs.value("last_selected_clip_path", "", str)

    def set_last_selected_clip_path(self, path: str) -> None:
        self._qs.setValue("last_selected_clip_path", path)

    # ── splitter layout persistence ──────────────────────────────

    def save_splitter_state(self, name: str, state: bytes) -> None:
        """Save a QSplitter state (from saveState().data()) under *name*."""
        import base64
        encoded = base64.b64encode(state).decode("ascii")
        self._qs.setValue(f"splitter/{name}", encoded)

    def restore_splitter_state(self, name: str) -> bytes | None:
        """Return the saved QSplitter state bytes, or None if no saved state."""
        import base64
        encoded = self._qs.value(f"splitter/{name}", "", str)
        if not encoded:
            return None
        try:
            return base64.b64decode(encoded)
        except Exception:
            return None
