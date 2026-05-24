"""Annotation project data model: folder = project, with per-clip prompts."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class FrameAnnotation:
    """A keyframe snapshot with manual draft and optional VLM-refined text."""

    frame_index: int
    timestamp_sec: float
    image_path: str = ""            # relative to project dir
    draft_text: str = ""
    transition_text: str = ""       # transition FROM this frame TO the next frame
    refined_text: str | None = None

    def to_dict(self) -> dict:
        return {
            "frame_index": self.frame_index,
            "timestamp_sec": self.timestamp_sec,
            "image_path": self.image_path,
            "draft_text": self.draft_text,
            "transition_text": self.transition_text,
            "refined_text": self.refined_text,
        }

    @classmethod
    def from_dict(cls, d: dict) -> FrameAnnotation:
        return cls(
            frame_index=d["frame_index"],
            timestamp_sec=d["timestamp_sec"],
            image_path=d.get("image_path", ""),
            draft_text=d.get("draft_text", ""),
            transition_text=d.get("transition_text", ""),
            refined_text=d.get("refined_text"),
        )


@dataclass
class AnnotatedClip:
    """One video clip within an annotation project.

    Version system (v3):
        Each LLM response creates a new *version* — an immutable snapshot
        of the generated prompt.  Users navigate versions with ← → buttons.
        Manual edits within a version are tracked by undo/redo (per-clip
        undo_history/redo_history).  Deleting a version moves it to
        ``deleted_versions`` so the deletion can be undone.

        ``prompt`` is always kept in sync with the current version's prompt.
        Call ``_sync_prompt_to_version()`` before switching versions and
        ``_sync_version_to_prompt()`` after.
    """

    clip_path: str                  # relative to project dir
    prompt: str = ""                # mirrors versions[current_version].prompt
    draft_prompt: str = ""          # manual draft before LLM
    annotations: list[FrameAnnotation] = field(default_factory=list)
    subtitle_path: str | None = None
    state: str = "pending"          # "pending" | "drafted" | "completed"

    # ── version system (v3+) ────────────────────────────────────
    # Each version dict: {"prompt": str, "source": str, "timestamp": str}
    #   source ∈ {"initial", "manual", "llm"}
    # Starts empty — the first manual edit or LLM response creates version 1.
    versions: list[dict] = field(default_factory=list)
    current_version: int = 0
    deleted_versions: list[dict] = field(default_factory=list)

    # ── undo/redo (v2+) ─────────────────────────────────────────
    undo_history: list[dict] = field(default_factory=list)
    redo_history: list[dict] = field(default_factory=list)

    def _sync_prompt_to_version(self) -> None:
        """Write ``self.prompt`` into the current version dict (if any)."""
        if self.versions and 0 <= self.current_version < len(self.versions):
            self.versions[self.current_version]["prompt"] = self.prompt

    def _sync_version_to_prompt(self) -> None:
        """Read the current version's prompt into ``self.prompt`` (if any)."""
        if self.versions and 0 <= self.current_version < len(self.versions):
            self.prompt = self.versions[self.current_version]["prompt"]

    @property
    def version_count(self) -> int:
        return len(self.versions)

    @property
    def version_label(self) -> str:
        """Human-readable label like '3/5', or '—' when no versions exist."""
        if not self.versions:
            return "—"
        return f"{self.current_version + 1}/{len(self.versions)}"

    def _make_version_snapshot(self) -> dict:
        """Return a deep copy of versions + metadata for undo snapshots."""
        import copy
        return {
            "versions": copy.deepcopy(self.versions),
            "current_version": self.current_version,
            "deleted_versions": copy.deepcopy(self.deleted_versions),
        }

    def _restore_version_snapshot(self, snap: dict) -> None:
        """Restore versions + metadata from an undo snapshot."""
        import copy
        self.versions = copy.deepcopy(snap["versions"])
        self.current_version = snap["current_version"]
        self.deleted_versions = copy.deepcopy(snap.get("deleted_versions", []))
        self._sync_version_to_prompt()

    def to_dict(self) -> dict:
        return {
            "clip_path": self.clip_path,
            "prompt": self.prompt,
            "draft_prompt": self.draft_prompt,
            "annotations": [a.to_dict() for a in self.annotations],
            "subtitle_path": self.subtitle_path,
            "state": self.state,
            "undo_history": list(self.undo_history),
            "redo_history": list(self.redo_history),
            "versions": list(self.versions),
            "current_version": self.current_version,
            "deleted_versions": list(self.deleted_versions),
        }

    @classmethod
    def from_dict(cls, d: dict) -> AnnotatedClip:
        # Backward compat: upgrade old saved_versions (v2) to new versions list
        versions = d.get("versions")
        if versions is None:
            prompt = d.get("prompt", "")
            versions = []
            if prompt.strip():
                versions = [{"prompt": prompt, "source": "initial", "timestamp": ""}]
            current = 0
            deleted = []
        else:
            versions = list(versions)
            current = d.get("current_version", 0)
            deleted = d.get("deleted_versions", [])
            # v3 format but prompt may have content without versions (upgrade edge case)
            prompt = d.get("prompt", "")
            if prompt.strip() and not versions:
                versions = [{"prompt": prompt, "source": "initial", "timestamp": ""}]
            # Purge empty initial-version residue left by older code
            versions = [v for v in versions if v.get("prompt", "").strip()]
            if current >= len(versions):
                current = max(0, len(versions) - 1) if versions else 0

        return cls(
            clip_path=d["clip_path"],
            prompt=d.get("prompt", ""),
            draft_prompt=d.get("draft_prompt", ""),
            annotations=[FrameAnnotation.from_dict(a) for a in d.get("annotations", [])],
            subtitle_path=d.get("subtitle_path"),
            state=d.get("state", "pending"),
            undo_history=d.get("undo_history", []),
            redo_history=d.get("redo_history", []),
            versions=versions,
            current_version=current,
            deleted_versions=deleted,
        )


@dataclass
class AnnotationProject:
    """Folder-based annotation project.

    One folder = one project. Contains a list of video clips,
    each with its own annotation data (prompts, keyframe annotations).
    Persisted as ``annotations.json`` inside the project folder.
    """

    schema_version: int = 3  # v2→v3: inline versions (replaced file-based saved_versions)
    project_dir: str = ""
    project_name: str = ""
    clips: list[AnnotatedClip] = field(default_factory=list)
    system_prompt: str = ""         # project-level LLM system prompt
    created_at: str = ""
    updated_at: str = ""

    # ── factory ──────────────────────────────────────────────────

    @classmethod
    def create(cls, project_dir: str | Path) -> AnnotationProject:
        """Create a new project for a folder of video clips."""
        pd = Path(project_dir).resolve()
        videos = sorted(
            [
                p for p in pd.iterdir()
                if p.is_file() and p.suffix.lower() in _VIDEO_SUFFIXES
            ]
        )
        clips = [
            AnnotatedClip(
                clip_path=str(v.relative_to(pd)),
                state="pending",
            )
            for v in videos
        ]
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            project_dir=str(pd),
            project_name=pd.name,
            clips=clips,
            created_at=now,
            updated_at=now,
        )

    # ── serialization ────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "project_dir": self.project_dir,
            "project_name": self.project_name,
            "clips": [c.to_dict() for c in self.clips],
            "system_prompt": self.system_prompt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AnnotationProject:
        return cls(
            schema_version=d.get("schema_version", 1),
            project_dir=d.get("project_dir", ""),
            project_name=d.get("project_name", ""),
            clips=[AnnotatedClip.from_dict(c) for c in d.get("clips", [])],
            system_prompt=d.get("system_prompt", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )

    # ── persistence ──────────────────────────────────────────────

    def json_path(self) -> Path:
        return Path(self.project_dir) / "annotations.json"

    def save(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()
        data = self.to_dict()
        self.json_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, project_dir: str | Path) -> AnnotationProject | None:
        pd = Path(project_dir).resolve()
        jp = pd / "annotations.json"
        if not jp.is_file():
            return None
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
            proj = cls.from_dict(data)
            proj.project_dir = str(pd)
            return proj
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    # ── helpers ──────────────────────────────────────────────────

    def resolve_clip_path(self, clip: AnnotatedClip) -> Path:
        return Path(self.project_dir) / clip.clip_path

    def clip_by_path(self, clip_path: str) -> AnnotatedClip | None:
        for c in self.clips:
            if c.clip_path == clip_path:
                return c
        return None


_VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".flv", ".wmv", ".mpeg", ".mpg"}
)
