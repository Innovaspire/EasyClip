"""Subtitle parsing, cutting, and writing for SRT / VTT / ASS / SSA formats."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

_SUBTITLE_EXTENSIONS = frozenset({".srt", ".vtt", ".ssa", ".ass", ".sub"})
_EXT_PRIORITY = [".srt", ".vtt", ".ass", ".ssa", ".sub"]

# Match timestamps: HH:MM:SS,mmm or HH:MM:SS.mmm (SRT/VTT) or H:MM:SS.mm (ASS)
_SRT_TIME_RE = re.compile(r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{2,3})")
_VTT_TIME_RE = re.compile(r"(\d{1,3}):(\d{2}):(\d{2})\.(\d{3})")
_ASS_TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[.](\d+)")
_ASS_DIALOGUE_RE = re.compile(r"^Dialogue:\s*\d+,")
_VTT_CUE_RE = re.compile(r"^[\d:]+\.\d{3}\s+-->\s+[\d:]+\.\d{3}")
_SRT_INDEX_RE = re.compile(r"^\d+$")
_VTT_HEADER_END_RE = re.compile(r"^\s*$")
_HTML_TAG_RE = re.compile(r"<[^>]*>")


class SubtitleFormat(StrEnum):
    SRT = "srt"
    VTT = "vtt"
    ASS = "ass"
    SSA = "ssa"


@dataclass
class SubtitleEntry:
    index: int
    start_sec: float
    end_sec: float
    text: str


@dataclass
class SubtitleTrack:
    source_path: str
    format: SubtitleFormat
    entries: list[SubtitleEntry] = field(default_factory=list)

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    def find_active(self, time_sec: float) -> SubtitleEntry | None:
        lo, hi = 0, len(self.entries)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.entries[mid].start_sec <= time_sec:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            return None
        candidate = self.entries[lo - 1]
        if candidate.start_sec <= time_sec < candidate.end_sec:
            return candidate
        # Handle exactly-on-end-boundary: if time == end_sec of one and == start_sec of next,
        # prefer the next entry's start.
        if lo < len(self.entries) and self.entries[lo].start_sec <= time_sec < self.entries[lo].end_sec:
            return self.entries[lo]
        return None


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

def _parse_srt_timestamp(s: str) -> float:
    m = _SRT_TIME_RE.match(s.strip())
    if not m:
        raise ValueError(f"invalid SRT timestamp: {s!r}")
    h, minute, sec, frac = int(m[1]), int(m[2]), int(m[3]), m[4]
    ms = int(frac) if len(frac) == 3 else int(frac) * 10
    return h * 3600.0 + minute * 60.0 + sec + ms / 1000.0


def _parse_vtt_timestamp(s: str) -> float:
    m = _VTT_TIME_RE.match(s.strip())
    if not m:
        raise ValueError(f"invalid VTT timestamp: {s!r}")
    h, minute, sec, ms = int(m[1]), int(m[2]), int(m[3]), int(m[4])
    return h * 3600.0 + minute * 60.0 + sec + ms / 1000.0


def _parse_ass_timestamp(s: str) -> float:
    s = s.strip()
    if "." in s:
        whole, frac = s.rsplit(".", 1)
    else:
        whole, frac = s, "0"
    colon = whole.count(":")
    if colon == 0:
        raise ValueError(f"invalid ASS timestamp: {s!r}")
    elif colon == 1:
        m, sec = whole.split(":")
        h = 0
    else:
        h, m, sec = whole.split(":", 2)
    frac_padded = (frac + "00")[:3]
    return int(h) * 3600.0 + int(m) * 60.0 + int(sec) + int(frac_padded) / 1000.0


# ---------------------------------------------------------------------------
# Encoding detection
# ---------------------------------------------------------------------------

def _detect_encoding(raw: bytes) -> str:
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    if raw[:2] == b"\xff\xfe":
        return "utf-16-le"
    if raw[:2] == b"\xfe\xff":
        return "utf-16-be"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    for enc in ("gb2312", "gbk", "shift-jis", "cp932", "cp1252", "latin-1"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8"


# ---------------------------------------------------------------------------
# SRT parser
# ---------------------------------------------------------------------------

def _parse_srt(text: str) -> list[SubtitleEntry]:
    entries: list[SubtitleEntry] = []
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if not _SRT_INDEX_RE.match(line):
            i += 1
            continue
        idx_str = line
        i += 1
        if i >= n:
            break
        ts_line = lines[i].strip()
        i += 1
        parts = ts_line.split("-->")
        if len(parts) != 2:
            continue
        try:
            start = _parse_srt_timestamp(parts[0])
            end = _parse_srt_timestamp(parts[1])
        except ValueError:
            continue
        text_lines: list[str] = []
        while i < n and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        text = "\n".join(text_lines)
        text = _strip_html(text)
        entries.append(SubtitleEntry(
            index=len(entries) + 1,
            start_sec=start,
            end_sec=end,
            text=text,
        ))
    entries.sort(key=lambda e: e.start_sec)
    for idx, e in enumerate(entries):
        e.index = idx + 1
    return entries


# ---------------------------------------------------------------------------
# VTT parser
# ---------------------------------------------------------------------------

def _parse_vtt(text: str) -> list[SubtitleEntry]:
    entries: list[SubtitleEntry] = []
    lines = text.splitlines()
    i = 0
    n = len(lines)
    # Skip the WEBVTT header block
    while i < n:
        stripped = lines[i].strip()
        if stripped.startswith("WEBVTT") or stripped == "" or stripped.startswith("Kind:") or stripped.startswith("Language:"):
            i += 1
            continue
        break
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        m = _VTT_CUE_RE.match(line)
        if not m:
            i += 1
            continue
        parts = line.split("-->")
        if len(parts) < 2:
            i += 1
            continue
        try:
            start = _parse_vtt_timestamp(parts[0].strip())
            end_raw = parts[1].strip()
            # VTT may have cue settings after timestamp (e.g. "00:01.000 --> 00:02.000 align:start")
            end_str = end_raw.split()[0]
            end = _parse_vtt_timestamp(end_str)
        except ValueError:
            i += 1
            continue
        i += 1
        text_lines: list[str] = []
        while i < n and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        text = "\n".join(text_lines)
        text = _strip_html(text)
        # Also strip VTT voice tags like <v Speaker>text</v>
        text = re.sub(r"</?v[^>]*>", "", text)
        entries.append(SubtitleEntry(
            index=len(entries) + 1,
            start_sec=start,
            end_sec=end,
            text=text,
        ))
    entries.sort(key=lambda e: e.start_sec)
    for idx, e in enumerate(entries):
        e.index = idx + 1
    return entries


# ---------------------------------------------------------------------------
# ASS / SSA parser
# ---------------------------------------------------------------------------

def _parse_ass(text: str) -> list[SubtitleEntry]:
    entries: list[SubtitleEntry] = []
    in_events = False
    fmt_order: dict[str, int] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("[EVENTS]"):
            in_events = True
            continue
        if in_events and stripped.upper().startswith("[") and stripped.endswith("]"):
            in_events = False
            continue
        if not in_events:
            continue
        if stripped.upper().startswith("FORMAT:"):
            fields = [f.strip() for f in stripped.split(":", 1)[1].split(",")]
            fmt_order = {f.upper(): idx for idx, f in enumerate(fields)}
            continue
        if not stripped.startswith("Dialogue:"):
            continue
        if not fmt_order:
            continue
        parts = stripped.split(":", 1)[1].split(",", maxsplit=len(fmt_order) - 1)
        if len(parts) < max(3, max(fmt_order.values(), default=0) + 1):
            continue
        try:
            start_idx = fmt_order.get("START")
            end_idx = fmt_order.get("END")
            text_idx = fmt_order.get("TEXT")
            if start_idx is None or end_idx is None or text_idx is None:
                continue
            start = _parse_ass_timestamp(parts[start_idx])
            end = _parse_ass_timestamp(parts[end_idx])
            if start >= end:
                continue
            raw_text = ",".join(parts[text_idx:]) if text_idx == len(parts) - 1 else parts[text_idx]
        except (ValueError, IndexError):
            continue
        raw_text = raw_text.replace("\\N", "\n").replace("\\n", "\n")
        raw_text = re.sub(r"\{[^}]*\}", "", raw_text)  # Strip ASS override tags
        raw_text = _strip_html(raw_text)
        text = raw_text.strip()
        if not text:
            continue
        entries.append(SubtitleEntry(
            index=len(entries) + 1,
            start_sec=start,
            end_sec=end,
            text=text,
        ))
    entries.sort(key=lambda e: e.start_sec)
    for idx, e in enumerate(entries):
        e.index = idx + 1
    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _HTML_TAG_RE.sub("", text)
    return text


def parse_subtitle_file(path: str | Path) -> SubtitleTrack:
    p = Path(path)
    suffix = p.suffix.lower()
    raw = p.read_bytes()
    encoding = _detect_encoding(raw)
    text = raw.decode(encoding, errors="replace")

    if suffix == ".srt":
        fmt = SubtitleFormat.SRT
        entries = _parse_srt(text)
    elif suffix == ".vtt":
        fmt = SubtitleFormat.VTT
        entries = _parse_vtt(text)
    elif suffix in (".ass", ".ssa"):
        fmt = SubtitleFormat.ASS if suffix == ".ass" else SubtitleFormat.SSA
        entries = _parse_ass(text)
    elif suffix == ".sub":
        # MicroDVD .sub: {start}{end}text — rarely used, try SRT as fallback
        fmt = SubtitleFormat.SRT
        entries = _parse_srt(text)
        if not entries:
            # Try MicroDVD format
            entries = _parse_microdvd(text)
            if entries:
                fmt = SubtitleFormat.SRT  # represent as SRT-like track
    else:
        raise ValueError(f"unsupported subtitle format: {suffix}")

    if not entries:
        return SubtitleTrack(source_path=str(p.resolve()), format=fmt, entries=[])

    return SubtitleTrack(source_path=str(p.resolve()), format=fmt, entries=entries)


def _parse_microdvd(text: str) -> list[SubtitleEntry]:
    """Parse MicroDVD .sub format: {start_frame}{end_frame}text"""
    entries: list[SubtitleEntry] = []
    microdvd_re = re.compile(r"\{(\d+)\}\{(\d+)\}(.*)")
    for line in text.splitlines():
        m = microdvd_re.match(line.strip())
        if not m:
            continue
        try:
            start_frame = int(m[1])
            end_frame = int(m[2])
            text = m[3].strip()
            if not text:
                continue
        except ValueError:
            continue
        text = text.replace("|", "\n")
        entries.append(SubtitleEntry(
            index=len(entries) + 1,
            start_sec=start_frame / 23.976,  # Standard MicroDVD reference FPS
            end_sec=end_frame / 23.976,
            text=text,
        ))
    return entries


def find_matching_subtitle(video_path: str | Path) -> Path | None:
    """Find a subtitle file in the same directory matching the video's stem name.

    Priority: .srt > .vtt > .ass > .ssa > .sub
    """
    vp = Path(video_path)
    if not vp.is_file():
        return None
    parent = vp.parent
    stem = vp.stem
    for ext in _EXT_PRIORITY:
        candidate = parent / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


def cut_subtitle_track(track: SubtitleTrack, start_sec: float, end_sec: float) -> SubtitleTrack:
    """Return a new SubtitleTrack with entries overlapping [start_sec, end_sec), re-timed to 0."""
    clipped: list[SubtitleEntry] = []
    for e in track.entries:
        if e.end_sec <= start_sec:
            continue
        if e.start_sec >= end_sec:
            break
        new_start = max(0.0, e.start_sec - start_sec)
        new_end = min(end_sec - start_sec, e.end_sec - start_sec)
        if new_end - new_start < 0.001:
            continue
        clipped.append(SubtitleEntry(
            index=len(clipped) + 1,
            start_sec=new_start,
            end_sec=new_end,
            text=e.text,
        ))
    return SubtitleTrack(source_path=track.source_path, format=track.format, entries=clipped)


def _format_srt_timestamp(sec: float) -> str:
    ms = max(0, int(round(sec * 1000.0)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms_rem = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms_rem:03d}"


def _format_vtt_timestamp(sec: float) -> str:
    ms = max(0, int(round(sec * 1000.0)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms_rem = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms_rem:03d}"


def write_subtitle_file(track: SubtitleTrack, out_path: str | Path) -> None:
    out = Path(out_path)
    if not track.entries:
        return  # Don't write empty subtitle files
    if track.format in (SubtitleFormat.SRT, SubtitleFormat.VTT):
        _write_srt_vtt(track, out)
    elif track.format in (SubtitleFormat.ASS, SubtitleFormat.SSA):
        _write_ass(track, out)
    else:
        _write_srt_vtt(track, out)


def _write_srt_vtt(track: SubtitleTrack, out: Path) -> None:
    lines: list[str] = []
    if track.format == SubtitleFormat.VTT:
        lines.append("WEBVTT\n")
        ts_fn = _format_vtt_timestamp
        sep = " --> "
    else:
        ts_fn = _format_srt_timestamp
        sep = " --> "
    for e in track.entries:
        lines.append(str(e.index))
        lines.append(f"{ts_fn(e.start_sec)}{sep}{ts_fn(e.end_sec)}")
        lines.append(e.text)
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


def _write_ass(track: SubtitleTrack, out: Path) -> None:
    """Write a minimal ASS file preserving only the timing and text."""
    lines: list[str] = []
    lines.append("[Script Info]")
    lines.append("ScriptType: v4.00+")
    lines.append("PlayResX: 384")
    lines.append("PlayResY: 288")
    lines.append("WrapStyle: 2")
    lines.append("")
    lines.append("[V4+ Styles]")
    lines.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
    lines.append("Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1")
    lines.append("")
    lines.append("[Events]")
    lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
    for e in track.entries:
        text = e.text.replace("\n", "\\N")
        start = _format_ass_timestamp(e.start_sec)
        end = _format_ass_timestamp(e.end_sec)
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    out.write_text("\r\n".join(lines), encoding="utf-8", newline="\r\n")


def _format_ass_timestamp(sec: float) -> str:
    h, rem = divmod(max(0, int(round(sec * 100.0))), 360_000)
    m, rem2 = divmod(rem, 6000)
    s, cs = divmod(rem2, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
