"""Application settings (persisted in QSettings)."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QLocale, QSettings

from easyclip.core.theme import Theme
from easyclip.i18n.strings import tr


class ProjectDirMode(StrEnum):
    HOME_DEFAULT = "home_default"
    NEXT_TO_SOURCE = "next_to_source"
    NEXT_TO_EXECUTABLE = "next_to_executable"
    CUSTOM = "custom"


class StartupBehavior(StrEnum):
    ASK = "ask"
    DO_NOTHING = "do_nothing"
    AUTO_LOAD_LAST_PROJECT = "auto_load_last_project"


def default_projects_root() -> Path:
    return Path.home() / ".easyclip" / "projects"


def detect_system_language() -> str:
    """Return 'zh_CN' for Chinese systems, 'en_US' otherwise."""
    name = QLocale.system().name()
    if name.startswith("zh"):
        return "zh_CN"
    return "en_US"


class AppSettings:
    def __init__(self) -> None:
        self._s = QSettings("Innovaspire", "EasyClip")

    def language(self) -> str:
        return str(self._s.value("language", detect_system_language()))

    def set_language(self, code: str) -> None:
        self._s.setValue("language", code)

    def theme(self) -> Theme:
        v = str(self._s.value("theme", Theme.SYSTEM.value))
        try:
            return Theme(v)
        except ValueError:
            return Theme.SYSTEM

    def set_theme(self, theme: Theme | str) -> None:
        if isinstance(theme, Theme):
            self._s.setValue("theme", theme.value)
        else:
            try:
                self._s.setValue("theme", Theme(str(theme)).value)
            except ValueError:
                self._s.setValue("theme", Theme.SYSTEM.value)

    def project_dir_mode(self) -> ProjectDirMode:
        v = str(self._s.value("project_dir_mode", ProjectDirMode.HOME_DEFAULT))
        try:
            return ProjectDirMode(v)
        except ValueError:
            return ProjectDirMode.HOME_DEFAULT

    def set_project_dir_mode(self, mode: ProjectDirMode | str) -> None:
        if isinstance(mode, ProjectDirMode):
            value = mode.value
        else:
            try:
                value = ProjectDirMode(str(mode)).value
            except ValueError:
                value = ProjectDirMode.HOME_DEFAULT.value
        self._s.setValue("project_dir_mode", value)

    def custom_project_root(self) -> Path | None:
        v = self._s.value("custom_project_root")
        if v:
            return Path(str(v))
        return None

    def set_custom_project_root(self, p: Path | None) -> None:
        if p is None:
            self._s.remove("custom_project_root")
        else:
            self._s.setValue("custom_project_root", str(p))

    def last_open_video_dir(self) -> str:
        return str(self._s.value("last_open_video_dir", "") or "")

    def set_last_open_video_dir(self, directory: str) -> None:
        if directory:
            self._s.setValue("last_open_video_dir", directory)

    def last_open_source_path(self) -> str:
        return str(self._s.value("last_open_source_path", "") or "")

    def set_last_open_source_path(self, source_path: str) -> None:
        if source_path:
            self._s.setValue("last_open_source_path", source_path)
        else:
            self._s.remove("last_open_source_path")

    def startup_behavior(self) -> StartupBehavior:
        v = str(self._s.value("startup_behavior", StartupBehavior.ASK.value) or "")
        try:
            return StartupBehavior(v)
        except ValueError:
            return StartupBehavior.ASK

    def set_startup_behavior(self, behavior: StartupBehavior | str) -> None:
        if isinstance(behavior, StartupBehavior):
            value = behavior.value
        else:
            try:
                value = StartupBehavior(str(behavior)).value
            except ValueError:
                value = StartupBehavior.ASK.value
        self._s.setValue("startup_behavior", value)

    def last_active_tab_index(self) -> int:
        return int(self._s.value("last_active_tab_index", 0))

    def set_last_active_tab_index(self, index: int) -> None:
        self._s.setValue("last_active_tab_index", max(0, index))

    def last_export_dir(self) -> str:
        return str(self._s.value("last_export_dir", "") or "")

    def set_last_export_dir(self, directory: str) -> None:
        if directory:
            self._s.setValue("last_export_dir", directory)

    def quick_slice_extend(self, idx: int) -> tuple[int, int]:
        default_val = idx * 60
        before = int(self._s.value(f"quick_slice_{idx}_before", default_val))
        after = int(self._s.value(f"quick_slice_{idx}_after", default_val))
        return before, after

    def set_quick_slice_extend(self, idx: int, before: int, after: int) -> None:
        self._s.setValue(f"quick_slice_{idx}_before", int(before))
        self._s.setValue(f"quick_slice_{idx}_after", int(after))

    def playback_seek_seconds(self) -> int:
        try:
            v = int(self._s.value("playback_seek_seconds", 5))
        except (TypeError, ValueError):
            v = 5
        return max(1, min(600, v))

    def set_playback_seek_seconds(self, v: int) -> None:
        self._s.setValue("playback_seek_seconds", max(1, min(600, int(v))))

    def export_fps(self) -> int:
        try:
            v = int(self._s.value("export_fps", 24))
        except (TypeError, ValueError):
            v = 24
        return max(1, min(240, v))

    def set_export_fps(self, v: int) -> None:
        self._s.setValue("export_fps", max(1, min(240, int(v))))

    def ui_align_fps_baseline(self) -> int:
        try:
            v = int(self._s.value("ui_align_fps_baseline", self.export_fps()))
        except (TypeError, ValueError):
            v = self.export_fps()
        return max(1, min(240, v))

    def set_ui_align_fps_baseline(self, v: int) -> None:
        self._s.setValue("ui_align_fps_baseline", max(1, min(240, int(v))))

    def ui_align_fps_match_source(self) -> bool:
        return bool(int(self._s.value("ui_align_fps_match_source", 0)))

    def set_ui_align_fps_match_source(self, v: bool) -> None:
        self._s.setValue("ui_align_fps_match_source", int(v))

    def ui_align_x(self) -> int:
        try:
            v = int(self._s.value("ui_align_x", 8))
        except (TypeError, ValueError):
            v = 8
        return max(1, min(1024, v))

    def set_ui_align_x(self, v: int) -> None:
        self._s.setValue("ui_align_x", max(1, min(1024, int(v))))

    def ui_align_y(self) -> int:
        try:
            v = int(self._s.value("ui_align_y", 1))
        except (TypeError, ValueError):
            v = 1
        xm = self.ui_align_x()
        return int(v) % xm

    def set_ui_align_y(self, v: int) -> None:
        xm = self.ui_align_x()
        self._s.setValue("ui_align_y", int(v) % max(1, xm))

    def clip_list_show_time(self) -> bool:
        return bool(int(self._s.value("clip_list_show_time", 0)))

    def set_clip_list_show_time(self, v: bool) -> None:
        self._s.setValue("clip_list_show_time", int(bool(v)))

    def _normalize_export_preset(self, raw: Any) -> dict[str, Any]:
        base = {
            "id": "",
            "name": tr("export.preset.default_name"),
            "export_fps": 24,
            "inherit_fps": False,
            "export_video_codec": "auto",
            "export_video_rate_mode": "quality",
            "export_video_bitrate_kbps": 8000,
            "bitrate_match_source": False,
            "export_video_quality": 20,
            "export_filename_template": "{source_name}_{clip_index:03d}",
            "size_multiple_enabled": False,
            "size_multiple_value": 32,
            "resolution_align_mode": "inherit",
            "preset_width": 0,
            "preset_height": 0,
            "align_enabled": False,
            "align_x": 32,
            "align_y": 1,
            "align_round": "ceil",
            "align_apply": "tail",
        }
        if not isinstance(raw, dict):
            return dict(base)
        data = dict(base)
        pid = str(raw.get("id", "") or "").strip()
        if pid:
            data["id"] = pid
        name = str(raw.get("name", "") or "").strip()
        if name:
            data["name"] = name
        try:
            fps = int(raw.get("export_fps", data["export_fps"]))
        except (TypeError, ValueError):
            fps = int(data["export_fps"])
        data["export_fps"] = max(1, min(240, fps))
        data["inherit_fps"] = bool(raw.get("inherit_fps", data["inherit_fps"]))
        codec = str(raw.get("export_video_codec", data["export_video_codec"]) or "").strip().lower()
        data["export_video_codec"] = codec or "auto"
        mode = str(raw.get("export_video_rate_mode", data["export_video_rate_mode"]) or "").strip().lower()
        data["export_video_rate_mode"] = mode if mode in {"bitrate", "quality"} else "bitrate"
        try:
            br = int(raw.get("export_video_bitrate_kbps", data["export_video_bitrate_kbps"]))
        except (TypeError, ValueError):
            br = int(data["export_video_bitrate_kbps"])
        data["export_video_bitrate_kbps"] = max(300, min(200000, br))
        data["bitrate_match_source"] = bool(raw.get("bitrate_match_source", data["bitrate_match_source"]))
        try:
            qv = int(raw.get("export_video_quality", data["export_video_quality"]))
        except (TypeError, ValueError):
            qv = int(data["export_video_quality"])
        data["export_video_quality"] = max(0, min(51, qv))
        data["export_filename_template"] = (
            str(raw.get("export_filename_template", data["export_filename_template"]) or "").strip()
            or str(data["export_filename_template"])
        )
        data["size_multiple_enabled"] = bool(raw.get("size_multiple_enabled", data["size_multiple_enabled"]))
        try:
            mv = int(raw.get("size_multiple_value", data["size_multiple_value"]))
        except (TypeError, ValueError):
            mv = int(data["size_multiple_value"])
        data["size_multiple_value"] = max(1, min(4096, mv))
        ram = str(raw.get("resolution_align_mode", data["resolution_align_mode"]) or "").strip().lower()
        data["resolution_align_mode"] = ram if ram in {"inherit", "align_width", "align_height"} else "inherit"
        try:
            pw = int(raw.get("preset_width", data["preset_width"]))
        except (TypeError, ValueError):
            pw = int(data["preset_width"])
        data["preset_width"] = max(0, min(16384, pw))
        try:
            ph = int(raw.get("preset_height", data["preset_height"]))
        except (TypeError, ValueError):
            ph = int(data["preset_height"])
        data["preset_height"] = max(0, min(16384, ph))
        data["align_enabled"] = bool(raw.get("align_enabled", data["align_enabled"]))
        try:
            ax = int(raw.get("align_x", data["align_x"]))
        except (TypeError, ValueError):
            ax = int(data["align_x"])
        data["align_x"] = max(1, min(1024, ax))
        try:
            ay = int(raw.get("align_y", data["align_y"]))
        except (TypeError, ValueError):
            ay = int(data["align_y"])
        data["align_y"] = int(ay) % data["align_x"]
        ar = str(raw.get("align_round", data["align_round"]) or "").strip().lower()
        data["align_round"] = ar if ar in {"ceil", "floor"} else "ceil"
        aa = str(raw.get("align_apply", data["align_apply"]) or "").strip().lower()
        data["align_apply"] = aa if aa in {"tail", "head", "symmetric"} else "tail"
        return data

    def export_presets(self) -> list[dict[str, Any]]:
        raw = self._s.value("export_presets", "")
        items: Any = None
        if isinstance(raw, str) and raw.strip():
            try:
                items = json.loads(raw)
            except json.JSONDecodeError:
                items = None
        elif isinstance(raw, list):
            items = raw
        presets: list[dict[str, Any]] = []
        if isinstance(items, list):
            for p in items:
                n = self._normalize_export_preset(p)
                if not n["id"]:
                    n["id"] = f"preset_{len(presets) + 1}"
                presets.append(n)
        if not presets:
            presets = [
                self._normalize_export_preset(
                    {
                        "id": "preset_default",
                        "name": tr("export.preset.default_name"),
                        "export_fps": self.export_fps(),
                        "inherit_fps": False,
                        "export_video_codec": "auto",
                        "export_video_rate_mode": "quality",
                        "export_video_bitrate_kbps": 8000,
                        "bitrate_match_source": False,
                        "export_video_quality": 20,
                        "export_filename_template": "{source_name}_{clip_index:03d}",
                        "size_multiple_enabled": False,
                        "size_multiple_value": 32,
                        "align_enabled": False,
                        "align_x": 32,
                        "align_y": 1,
                        "align_round": "ceil",
                        "align_apply": "tail",
                    }
                )
            ]
        # De-dup ids while preserving order.
        seen: set[str] = set()
        for i, p in enumerate(presets):
            pid = str(p.get("id", "") or "").strip()
            if not pid or pid in seen:
                pid = f"preset_{i + 1}"
                p["id"] = pid
            seen.add(pid)
        return presets

    def set_export_presets(self, presets: list[dict[str, Any]]) -> None:
        normed: list[dict[str, Any]] = []
        seen: set[str] = set()
        for i, p in enumerate(presets):
            n = self._normalize_export_preset(p)
            pid = str(n.get("id", "") or "").strip() or f"preset_{i + 1}"
            if pid in seen:
                pid = f"{pid}_{i + 1}"
            seen.add(pid)
            n["id"] = pid
            normed.append(n)
        if not normed:
            normed = self.export_presets()
        self._s.setValue("export_presets", json.dumps(normed, ensure_ascii=False))

    def default_export_preset_id(self) -> str:
        return str(self._s.value("default_export_preset_id", "") or "").strip()

    def set_default_export_preset_id(self, preset_id: str) -> None:
        pid = str(preset_id or "").strip()
        if pid:
            self._s.setValue("default_export_preset_id", pid)
        else:
            self._s.remove("default_export_preset_id")

    def default_export_preset(self) -> dict[str, Any]:
        presets = self.export_presets()
        target = self.default_export_preset_id()
        if target:
            for p in presets:
                if str(p.get("id", "")) == target:
                    return p
        first = presets[0]
        self.set_default_export_preset_id(str(first.get("id", "")))
        return first

    def experimental_pipeline(self) -> bool:
        return bool(int(self._s.value("experimental_pipeline", 0)))

    def set_experimental_pipeline(self, v: bool) -> None:
        self._s.setValue("experimental_pipeline", int(v))

    def warn_align_8n1(self) -> bool:
        return bool(int(self._s.value("warn_align_8n1", 1)))

    def set_warn_align_8n1(self, v: bool) -> None:
        self._s.setValue("warn_align_8n1", int(v))

    def auto_load_subtitle(self) -> bool:
        return bool(int(self._s.value("auto_load_subtitle", 1)))

    def set_auto_load_subtitle(self, v: bool) -> None:
        self._s.setValue("auto_load_subtitle", int(v))

    def undo_max_steps(self) -> int:
        try:
            v = int(self._s.value("undo_max_steps", 50))
        except (TypeError, ValueError):
            v = 50
        return max(10, min(500, v))

    def set_undo_max_steps(self, v: int) -> None:
        self._s.setValue("undo_max_steps", max(10, min(500, int(v))))

    def preview_wave_split_sizes(self) -> tuple[int, int] | None:
        """Heights (px) for [preview row, waveform] from last session, or None."""
        a = self._s.value("preview_wave_split_0")
        b = self._s.value("preview_wave_split_1")
        if a is None or b is None:
            return None
        try:
            return (int(a), int(b))
        except (TypeError, ValueError):
            return None

    def set_preview_wave_split_sizes(self, preview_row_h: int, waveform_h: int) -> None:
        self._s.setValue("preview_wave_split_0", int(preview_row_h))
        self._s.setValue("preview_wave_split_1", int(waveform_h))

    def preview_volume(self) -> float:
        v = float(self._s.value("preview_volume", 1.0))
        return max(0.0, min(1.0, v))

    def set_preview_volume(self, linear: float) -> None:
        self._s.setValue("preview_volume", max(0.0, min(1.0, float(linear))))
