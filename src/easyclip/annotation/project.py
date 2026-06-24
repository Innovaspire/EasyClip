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
    refined_text: str | None = None
    committed: bool = False         # True once sent in a message (locked from deletion)

    def to_dict(self) -> dict:
        return {
            "frame_index": self.frame_index,
            "timestamp_sec": self.timestamp_sec,
            "image_path": self.image_path,
            "refined_text": self.refined_text,
            "committed": self.committed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> FrameAnnotation:
        return cls(
            frame_index=d["frame_index"],
            timestamp_sec=d["timestamp_sec"],
            image_path=d.get("image_path", ""),
            refined_text=d.get("refined_text"),
            committed=d.get("committed", False),
        )


@dataclass
class ConversationNode:
    """A single message node in the conversation tree.

    Role is either "user" or "assistant". System prompt is stored
    separately on AnnotationProject.

    Parent/child links form a tree. When a node has multiple children,
    each child represents a different branch. The path from root to
    current_node_id defines the active conversation thread.
    """

    id: str                              # uuid
    role: str                            # "user" | "assistant"
    content: str = ""                    # text (assistant: cleaned, user: typed)
    reasoning: str = ""                  # assistant only: thinking content
    content_parts: list[dict] | None = None  # user only: raw OpenAI content array
    parent_id: str | None = None         # None for root node
    children_ids: list[str] = field(default_factory=list)
    timestamp: str = ""                  # ISO datetime
    source: str | None = None            # assistant only: "llm" | "manual"
    thinking_duration: float = 0.0
    annotations: list[str] = field(default_factory=list)
    annotation_selected: list[bool] = field(default_factory=list)
    annotation_snapshot: list[dict] | None = None  # user only: FrameAnnotation snapshots at send time

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "reasoning": self.reasoning,
            "content_parts": self.content_parts,
            "parent_id": self.parent_id,
            "children_ids": list(self.children_ids),
            "timestamp": self.timestamp,
            "source": self.source,
            "thinking_duration": self.thinking_duration,
            "annotations": list(self.annotations),
            "annotation_selected": list(self.annotation_selected),
            "annotation_snapshot": self.annotation_snapshot,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ConversationNode:
        return cls(
            id=d["id"],
            role=d["role"],
            content=d.get("content", ""),
            reasoning=d.get("reasoning", ""),
            content_parts=d.get("content_parts"),
            parent_id=d.get("parent_id"),
            children_ids=list(d.get("children_ids", [])),
            timestamp=d.get("timestamp", ""),
            source=d.get("source"),
            thinking_duration=d.get("thinking_duration", 0.0),
            annotations=list(d.get("annotations", [])),
            annotation_selected=[bool(x) for x in d.get("annotation_selected", [])],
            annotation_snapshot=d.get("annotation_snapshot"),
        )


def _upgrade_v4_conversation_to_tree(conversation: list[dict]) -> tuple[dict[str, ConversationNode], str | None, str | None]:
    """Convert a v4 flat conversation list into v5 tree nodes.

    Returns (tree_nodes, root_node_id, current_node_id).
    """
    import uuid as _uuid
    from datetime import datetime, timezone as _timezone

    tree_nodes: dict[str, ConversationNode] = {}
    root_node_id: str | None = None
    current_node_id: str | None = None
    parent_id: str | None = None

    for i, msg in enumerate(conversation):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        content_parts = None
        if isinstance(content, list):
            content_parts = content
            content = ""

        node = ConversationNode(
            id=str(_uuid.uuid4()),
            role=role,
            content=content if isinstance(content, str) else "",
            content_parts=content_parts,
            parent_id=parent_id,
            timestamp=datetime.now(_timezone.utc).isoformat(),
            source="llm" if role == "assistant" else None,
        )
        tree_nodes[node.id] = node
        if parent_id:
            tree_nodes[parent_id].children_ids.append(node.id)
        else:
            root_node_id = node.id
        parent_id = node.id
        current_node_id = node.id

    return tree_nodes, root_node_id, current_node_id


@dataclass
class AnnotatedClip:
    """One video clip within an annotation project.

    Tree-based conversation (v5+):
        Messages are stored as ConversationNode objects in a tree.
        Branches are created when the user regenerates from an earlier point.
        The active thread is defined by the path from root_node_id to
        current_node_id.

        ``prompt`` is derived from current_node.content (assistant role only).
    """

    clip_path: str                  # relative to project dir
    prompt: str = ""                # derived from current_node.content (assistant)
    annotations: list[FrameAnnotation] = field(default_factory=list)
    subtitle_path: str | None = None
    state: str = "pending"          # "pending" | "drafted" | "completed"

    # ── conversation tree (v5+) ──────────────────────────────────
    tree_nodes: dict[str, ConversationNode] = field(default_factory=dict)
    root_node_id: str | None = None
    current_node_id: str | None = None
    selected_annotation_node_id: str | None = None
    selected_annotation_index: int = -1

    # ── undo/redo (v2+) ─────────────────────────────────────────
    undo_history: list[dict] = field(default_factory=list)
    redo_history: list[dict] = field(default_factory=list)

    # ── tree navigation helpers ──────────────────────────────────

    @property
    def current_node(self) -> ConversationNode | None:
        if self.current_node_id and self.current_node_id in self.tree_nodes:
            return self.tree_nodes[self.current_node_id]
        return None

    def conversation_path(self) -> list[str]:
        """Ordered node ids from root to current_node_id (active thread)."""
        path: list[str] = []
        node_id = self.current_node_id
        while node_id:
            path.append(node_id)
            node = self.tree_nodes.get(node_id)
            if node is None:
                break
            node_id = node.parent_id
        path.reverse()
        return path

    def conversation_message_count(self) -> int:
        """Number of messages in the active conversation path."""
        return len(self.conversation_path())

    def has_conversation(self) -> bool:
        """Whether at least one assistant response exists."""
        if self.current_node_id is None:
            return False
        node = self.tree_nodes.get(self.current_node_id)
        return node is not None and node.role == "assistant"

    def _root_siblings(self) -> list[str]:
        """Return all root-level node ids (parent_id is None), sorted by timestamp."""
        roots = [nid for nid, n in self.tree_nodes.items() if n.parent_id is None]
        roots.sort(key=lambda nid: self.tree_nodes[nid].timestamp)
        return roots

    def branch_count(self, node_id: str) -> int:
        """Number of sibling branches at a node (children of its parent)."""
        node = self.tree_nodes.get(node_id)
        if node is None:
            return 1
        if node.parent_id is None:
            return max(len(self._root_siblings()), 1)
        parent = self.tree_nodes.get(node.parent_id)
        if parent is None:
            return 1
        return len(parent.children_ids)

    def sibling_branches(self, node_id: str) -> list[str]:
        """Sibling node ids (same parent, same role), ordered by insertion."""
        node = self.tree_nodes.get(node_id)
        if node is None:
            return [node_id] if node_id else []
        if node.parent_id is None:
            return self._root_siblings()
        parent = self.tree_nodes.get(node.parent_id)
        if parent is None:
            return [node_id]
        return list(parent.children_ids)

    def sibling_index(self, node_id: str) -> int:
        """0-based index among siblings."""
        siblings = self.sibling_branches(node_id)
        try:
            return siblings.index(node_id)
        except ValueError:
            return 0

    # ── child-level navigation (for nodes with multiple children) ──

    def children_count(self, node_id: str) -> int:
        """Number of children of *node_id*."""
        node = self.tree_nodes.get(node_id)
        return len(node.children_ids) if node else 0

    def child_index(self, node_id: str) -> int:
        """Which child of *node_id* is on the active conversation path."""
        node = self.tree_nodes.get(node_id)
        if node is None or not node.children_ids:
            return 0
        nid = self.current_node_id
        while nid:
            for i, cid in enumerate(node.children_ids):
                if cid == nid:
                    return i
            nd = self.tree_nodes.get(nid)
            if nd is None:
                break
            nid = nd.parent_id
        return 0

    def navigate_children(self, node_id: str, direction: int) -> str | None:
        """Switch current_node_id to prev/next child of *node_id*, then walk
        down the first-child chain to the deepest leaf."""
        node = self.tree_nodes.get(node_id)
        if node is None or len(node.children_ids) <= 1:
            return None
        ci = self.child_index(node_id)
        new_ci = (ci + direction) % len(node.children_ids)
        new_id = node.children_ids[new_ci]
        self.current_node_id = new_id
        # Walk down first-child to deepest leaf
        while True:
            child = self.tree_nodes.get(new_id)
            if child and child.children_ids:
                new_id = child.children_ids[0]
                self.current_node_id = new_id
            else:
                break
        cur = self.current_node
        if cur and cur.role == "assistant":
            self.prompt = cur.content
        return self.current_node_id

    def navigate_sibling(self, node_id: str, direction: int) -> str | None:
        """Switch current_node_id to a sibling branch. Returns new node_id or None."""
        siblings = self.sibling_branches(node_id)
        idx = self.sibling_index(node_id)
        new_idx = idx + direction
        if 0 <= new_idx < len(siblings):
            new_id = siblings[new_idx]
            self.current_node_id = new_id
            # Walk down to the last leaf along the first-child path
            while True:
                new_node = self.tree_nodes.get(new_id)
                if new_node and new_node.children_ids:
                    new_id = new_node.children_ids[0]
                    self.current_node_id = new_id
                else:
                    break
            # Update prompt from current node
            cur = self.current_node
            if cur and cur.role == "assistant":
                self.prompt = cur.content
            return self.current_node_id
        return None

    def add_child_node(self, parent_id: str, node: ConversationNode) -> None:
        """Add a child node. If a child with same role exists, becomes a sibling.
        Sets current_node_id to the new node."""
        node.parent_id = parent_id
        self.tree_nodes[node.id] = node
        parent = self.tree_nodes[parent_id]
        parent.children_ids.append(node.id)
        self.current_node_id = node.id

    def clear_tree(self) -> None:
        """Remove all tree nodes (preserves other clip data)."""
        self.tree_nodes.clear()
        self.root_node_id = None
        self.current_node_id = None

    # ── tree-aware annotation management ─────────────────────────

    def rebuild_annotations_from_path(self) -> None:
        """Rebuild self.annotations from committed snapshots on the active path.

        Walks the conversation path and collects annotation_snapshot from all
        user nodes. The most recent snapshot for each frame_index wins.
        Pending (uncommitted) frames from the current annotations list are
        preserved if they don't conflict with committed frames.
        """
        committed_map: dict[int, FrameAnnotation] = {}
        path = self.conversation_path()
        for node_id in path:
            node = self.tree_nodes.get(node_id)
            if node and node.annotation_snapshot:
                for fa_dict in node.annotation_snapshot:
                    fa = FrameAnnotation.from_dict(fa_dict)
                    fa.committed = True
                    committed_map[fa.frame_index] = fa
        # Preserve pending frames (committed=False, added after last commit)
        pending = [fa for fa in self.annotations if not fa.committed]
        for fa in pending:
            if fa.frame_index not in committed_map:
                committed_map[fa.frame_index] = fa
        self.annotations = sorted(
            committed_map.values(), key=lambda fa: fa.frame_index
        )

    def commit_annotations_snapshot(self) -> list[dict]:
        """Mark all annotations as committed and return a serializable snapshot.

        Called before sending a user message to snapshot the current annotation
        state into the ConversationNode.
        """
        snapshot = []
        for fa in self.annotations:
            fa.committed = True
            snapshot.append(fa.to_dict())
        return snapshot

    # ── undo/redo snapshot helpers ───────────────────────────────

    def _make_version_snapshot(self) -> dict:
        """Return a deep copy of tree state for undo snapshots."""
        import copy
        return {
            "tree_nodes": {
                k: v.to_dict() for k, v in self.tree_nodes.items()
            },
            "root_node_id": self.root_node_id,
            "current_node_id": self.current_node_id,
        }

    def _restore_version_snapshot(self, snap: dict) -> None:
        """Restore tree state from an undo snapshot."""
        import copy
        td = snap.get("tree_nodes", {})
        self.tree_nodes = {
            k: ConversationNode.from_dict(v) for k, v in td.items()
        }
        self.root_node_id = snap.get("root_node_id")
        self.current_node_id = snap.get("current_node_id")
        # Update prompt from current node
        cur = self.current_node
        self.prompt = cur.content if (cur and cur.role == "assistant") else ""

    # ── serialization ────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "clip_path": self.clip_path,
            "prompt": self.prompt,
            "annotations": [a.to_dict() for a in self.annotations],
            "subtitle_path": self.subtitle_path,
            "state": self.state,
            "undo_history": list(self.undo_history),
            "redo_history": list(self.redo_history),
            "tree_nodes": {k: v.to_dict() for k, v in self.tree_nodes.items()},
            "root_node_id": self.root_node_id,
            "current_node_id": self.current_node_id,
            "selected_annotation_node_id": self.selected_annotation_node_id,
            "selected_annotation_index": self.selected_annotation_index,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AnnotatedClip:
        # ── Load tree nodes (v5+) or upgrade from v4 conversation ──
        tree_nodes_raw = d.get("tree_nodes")
        if tree_nodes_raw is not None:
            # v5: direct tree structure
            tree_nodes = {k: ConversationNode.from_dict(v) for k, v in tree_nodes_raw.items()}
            root_node_id = d.get("root_node_id")
            current_node_id = d.get("current_node_id")
        else:
            # v4 backward compat: upgrade flat conversation list to tree
            conversation = d.get("conversation", [])
            if conversation:
                tree_nodes, root_node_id, current_node_id = _upgrade_v4_conversation_to_tree(conversation)
            else:
                # v3 or older: no conversation or tree
                tree_nodes = {}
                root_node_id = None
                current_node_id = None

        # Derive prompt from current assistant node
        prompt = d.get("prompt", "")
        if current_node_id and current_node_id in tree_nodes:
            cur = tree_nodes[current_node_id]
            if cur.role == "assistant" and cur.content:
                prompt = cur.content

        clip = cls(
            clip_path=d["clip_path"],
            prompt=prompt,
            annotations=[FrameAnnotation.from_dict(a) for a in d.get("annotations", [])],
            subtitle_path=d.get("subtitle_path"),
            state=d.get("state", "pending"),
            undo_history=d.get("undo_history", []),
            redo_history=d.get("redo_history", []),
            tree_nodes=tree_nodes,
            root_node_id=root_node_id,
            current_node_id=current_node_id,
            selected_annotation_node_id=d.get("selected_annotation_node_id"),
            selected_annotation_index=d.get("selected_annotation_index", -1),
        )
        # Rebuild annotations from the active conversation path
        if clip.tree_nodes and clip.root_node_id:
            clip.rebuild_annotations_from_path()
        return clip


@dataclass
class AnnotationProject:
    """Folder-based annotation project.

    One folder = one project. Contains a list of video clips,
    each with its own annotation data (prompts, keyframe annotations).
    Persisted as ``annotations.json`` inside the project folder.
    """

    schema_version: int = 5  # v4→v5: conversation tree (ConversationNode) replaces flat list
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
