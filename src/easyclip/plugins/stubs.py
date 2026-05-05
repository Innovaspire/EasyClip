"""Future hooks: transcribe, omni caption, VLM expand (MVP stubs)."""

from __future__ import annotations

from typing import Any


def transcribe_clip_stub(clip_id: str, audio_path: str) -> dict[str, Any]:
    """Reserved: return transcript text or async job id."""
    return {"status": "not_implemented", "clip_id": clip_id, "audio_path": audio_path}


def auto_caption_omni_stub(clip_id: str, video_path: str) -> dict[str, Any]:
    """Reserved: omni-model auto caption."""
    return {"status": "not_implemented", "clip_id": clip_id, "video_path": video_path}


def vlm_expand_stub(frame_paths: list[str], prompt: str) -> dict[str, Any]:
    """Reserved: VLM expansion from key frames + prompt."""
    return {
        "status": "not_implemented",
        "frames": len(frame_paths),
        "prompt": prompt,
    }
