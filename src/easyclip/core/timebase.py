"""Logical frame index ↔ time mapping (CFR-oriented MVP)."""

from __future__ import annotations

from dataclasses import dataclass

from easyclip.core.ffmpeg_util import VideoProbe


@dataclass
class Timebase:
    fps: float
    duration_sec: float
    total_frames: int

    @classmethod
    def from_probe(cls, p: VideoProbe) -> Timebase:
        fps = max(p.fps, 1e-6)
        # ffprobe stream nb_frames is often wrong for MP4/H.264 (undercounts). Qt
        # decodes by timestamps, so trusting nb_frames alone shrinks the timeline:
        # playhead vs. bar disagree, and pause/seek snaps to the short end.
        if p.duration_sec > 0:
            total = max(1, int(round(p.duration_sec * fps)))
        elif p.nb_frames is not None and p.nb_frames > 0:
            total = int(p.nb_frames)
        else:
            total = 1
        return cls(fps=fps, duration_sec=p.duration_sec, total_frames=total)

    def frame_to_time(self, frame: int) -> float:
        frame = max(0, min(frame, self.total_frames - 1))
        return frame / self.fps

    def time_to_frame(self, t: float) -> int:
        if self.duration_sec > 0:
            t = max(0.0, min(t, self.duration_sec - 1e-6))
        f = int(round(t * self.fps))
        return max(0, min(f, self.total_frames - 1))

    def frame_count_inclusive(self, start: int, end: int) -> int:
        return max(0, end - start + 1)
