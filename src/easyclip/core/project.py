"""Project JSON and per-project directory layout."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from easyclip.core.settings import AppSettings, ProjectDirMode, default_projects_root


class ClipState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class Clip:
    id: str
    start_frame: int
    end_frame: int | None = None
    state: str = ClipState.OPEN.value

    def length_frames(self) -> int | None:
        if self.end_frame is None:
            return None
        return self.end_frame - self.start_frame + 1


@dataclass
class UndoState:
    """Snapshot of clip-editing state at one point in time."""

    description: str
    current_frame: int
    clips_json: list[dict]

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "current_frame": self.current_frame,
            "clips_json": self.clips_json,
        }

    @classmethod
    def from_dict(cls, d: dict) -> UndoState:
        return cls(
            description=str(d.get("description", "")),
            current_frame=int(d.get("current_frame", 0)),
            clips_json=list(d.get("clips_json") or []),
        )


@dataclass
class ProjectData:
    schema_version: int = 1
    source_path: str = ""
    proxy_path: str | None = None
    fps: float = 30.0
    duration_sec: float = 0.0
    total_frames: int = 0
    width: int = 0
    height: int = 0
    current_frame: int = 0
    clips: list[Clip] = field(default_factory=list)
    undo_history: list[UndoState] = field(default_factory=list)
    redo_history: list[UndoState] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_path": self.source_path,
            "proxy_path": self.proxy_path,
            "fps": self.fps,
            "duration_sec": self.duration_sec,
            "total_frames": self.total_frames,
            "width": self.width,
            "height": self.height,
            "current_frame": self.current_frame,
            "clips": [asdict(c) for c in self.clips],
            "undo_history": [s.to_dict() for s in self.undo_history],
            "redo_history": [s.to_dict() for s in self.redo_history],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> ProjectData:
        clips_raw = d.get("clips") or []
        clips = [
            Clip(
                id=str(c.get("id", uuid.uuid4().hex)),
                start_frame=int(c["start_frame"]),
                end_frame=int(c["end_frame"]) if c.get("end_frame") is not None else None,
                state=str(c.get("state", ClipState.CLOSED.value)),
            )
            for c in clips_raw
        ]
        undo_raw = d.get("undo_history") or []
        undo_history = [UndoState.from_dict(s) for s in undo_raw if isinstance(s, dict)]
        redo_raw = d.get("redo_history") or []
        redo_history = [UndoState.from_dict(s) for s in redo_raw if isinstance(s, dict)]
        return cls(
            schema_version=int(d.get("schema_version", 1)),
            source_path=str(d.get("source_path", "")),
            proxy_path=d.get("proxy_path"),
            fps=float(d.get("fps", 30.0)),
            duration_sec=float(d.get("duration_sec", 0.0)),
            total_frames=int(d.get("total_frames", 0)),
            width=int(d.get("width", 0)),
            height=int(d.get("height", 0)),
            current_frame=int(d.get("current_frame", 0)),
            clips=clips,
            undo_history=undo_history,
            redo_history=redo_history,
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    h.update(str(path.resolve()).encode("utf-8"))
    return h.hexdigest()[:16]


def resolve_project_root(
    source: Path,
    settings: AppSettings,
    executable_dir: Path | None = None,
) -> Path:
    mode = settings.project_dir_mode()
    if mode == ProjectDirMode.HOME_DEFAULT:
        root = default_projects_root()
    elif mode == ProjectDirMode.NEXT_TO_SOURCE:
        root = source.parent / ".easyclip_projects"
    elif mode == ProjectDirMode.NEXT_TO_EXECUTABLE:
        root = (executable_dir or Path.cwd()) / ".easyclip_projects"
    elif mode == ProjectDirMode.CUSTOM:
        custom = settings.custom_project_root()
        root = custom if custom else default_projects_root()
    else:
        root = default_projects_root()
    sub = f"{source.stem}_{source_fingerprint(source)}"
    return root / sub


class ProjectStore:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.json_path = project_dir / "project.json"
        self.proxy_path = project_dir / "proxy.mp4"
        self.waveform_path = project_dir / "waveform.npz"

    def ensure_dir(self) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> ProjectData | None:
        if not self.json_path.is_file():
            return None
        with open(self.json_path, encoding="utf-8") as f:
            d = json.load(f)
        return ProjectData.from_json(d)

    def save(self, data: ProjectData) -> None:
        self.ensure_dir()
        data.updated_at = _utc_now()
        if not data.created_at:
            data.created_at = data.updated_at
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data.to_json(), f, ensure_ascii=False, indent=2)
        if data.proxy_path and Path(data.proxy_path).is_file():
            self.proxy_path = Path(data.proxy_path)
