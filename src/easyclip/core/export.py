"""Batch export clips to files."""

from __future__ import annotations

import re
import string
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from easyclip.core.align_io import (
    AlignConstraint,
    AlignWarning,
    apply_align_constraint,
    is_valid_length,
    normalize_xy,
    output_frame_count,
)
from easyclip.core.ffmpeg_util import FfmpegEncodeProgress, export_clip_mp4, probe_has_audio
from easyclip.core.project import ProjectData
from easyclip.core.subtitle import SubtitleTrack, cut_subtitle_track, parse_subtitle_file, write_subtitle_file
from easyclip.core.timebase import Timebase

DEFAULT_EXPORT_FILENAME_TEMPLATE = "{source_name}_{clip_index:03d}"
_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _safe_timecode(sec: float) -> str:
    ms_all = max(0, int(round(float(sec) * 1000.0)))
    h, rem = divmod(ms_all, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}-{m:02d}-{s:02d}.{ms:03d}"


def _sanitize_filename_stem(stem: str) -> str:
    s = _INVALID_FILENAME_CHARS_RE.sub("_", str(stem))
    s = s.strip().rstrip(".")
    if not s:
        s = "clip"
    if s.upper() in _WINDOWS_RESERVED_NAMES:
        s = f"_{s}"
    return s


def _unique_output_path(output_dir: Path, stem: str, suffix: str, used: set[str]) -> Path:
    base = _sanitize_filename_stem(stem)
    name = f"{base}{suffix}"
    if name not in used and not (output_dir / name).exists():
        used.add(name)
        return output_dir / name
    i = 1
    while True:
        cand = f"{base} ({i}){suffix}"
        if cand not in used and not (output_dir / cand).exists():
            used.add(cand)
            return output_dir / cand
        i += 1


def _render_filename_stem(template: str, values: dict[str, object]) -> str:
    return string.Formatter().vformat(template, (), values)


def _default_align_warning_text(w: AlignWarning) -> str:
    """Fallback formatter used when callers don't supply a localized one.

    Keeps :mod:`export` UI-framework-free: ``main_window`` provides a real ``i18n.tr``-backed
    formatter, while CLI / tests get a readable English fallback. The wording here is
    deliberately concise — verbose explanations belong in the i18n table.
    """
    p = w.params
    if w.key == "export.align_warn.head_clamped":
        return f"start frame clamped to 0 (shifted by {p.get('shift', 0)})"
    if w.key == "export.align_warn.tail_clamped":
        return f"end frame clamped to last source frame (shifted by {p.get('shift', 0)})"
    if w.key == "export.align_warn.both_clamped":
        return "both endpoints clamped: source pinned to full clip length"
    if w.key == "export.align_warn.source_too_short":
        req = p.get("requested", "?")
        actual = p.get("actual", "?")
        feasible = p.get("feasible", 0)
        if feasible:
            return (
                f"source too short for {req} frames; downgraded to {feasible} "
                f"(max possible {actual})"
            )
        return f"source too short; output natural length {actual} (constraint not met)"
    if w.key == "export.align_warn.invalid_clip":
        return f"clip range invalid: {p}"
    if w.key == "export.align_warn.constraint_unsatisfied":
        return (
            f"constraint cannot be satisfied: requested {p.get('requested')}, "
            f"output {p.get('actual')}"
        )
    if w.key == "export.align_warn.floor_underflow_to_ceil":
        return f"floor mode underflowed; using smallest valid value {p.get('smallest')}"
    if w.key == "export.align_warn.single_frame":
        return "output is a single frame"
    if w.key == "export.align_warn.floor_fallback_minimum":
        return "floor mode: segment shorter than smallest valid length; using minimum valid frame count"
    if w.key == "export.align_warn.constraint_reduced_by_span":
        return "cannot reach target frame count within clip bounds; exporting fewer frames (source span limit)"
    if w.key == "export.align_warn.source_too_short_for_pattern":
        return "source span too short for a valid aligned length; exporting maximum possible frames"
    if w.key == "export.align_warn.output_not_matching_pattern":
        return "exported frame count does not match x·n+y pattern (see other messages)"
    if w.key == "export.align_warn.duration_insufficient":
        return "internal: duration vs frame target mismatch (downgraded frame count)"
    if w.key == "export.align_warn.zero_output_span":
        return (
            "source span shorter than one output frame at export CFR; "
            "strict frame count was not applied for this clip"
        )
    return f"{w.key}: {p}"


def export_all_clips(
    data: ProjectData,
    tb: Timebase,
    output_dir: str | Path,
    *,
    warn_non_8n1: bool = True,
    warn_pattern_x: int = 8,
    warn_pattern_y: int = 1,
    format_predicted_pattern_warn: Callable[[int, int, int, int], str] | None = None,
    export_fps: int = 24,
    export_video_codec: str = "auto",
    export_video_bitrate_kbps: int = 8000,
    export_video_rate_mode: str = "bitrate",
    export_video_quality: int = 23,
    export_filename_template: str = DEFAULT_EXPORT_FILENAME_TEMPLATE,
    strong_av_sync: bool = False,
    video_filter: str | None = None,
    align_constraint: AlignConstraint | None = None,
    verify_strict_output_frames: bool = True,
    format_align_warning: Callable[[AlignWarning], str] | None = None,
    on_progress: Callable[[float, int, int, int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    on_clip_begin: Callable[[int, int, int, float], None] | None = None,
    stderr_line_hook: Callable[[str], None] | None = None,
    subtitle_source_path: str | None = None,
) -> list[tuple[str, str]]:
    """
    Export each closed clip to output_dir / clip_<id>.mp4.
    Returns list of (output_name, message) for warnings or errors.

    When ``align_constraint`` is given (and enabled), each clip is run through
    :func:`apply_align_constraint` *first*: the resulting source frame range and
    ``n_target_frames`` are passed to :func:`export_clip_mp4` which enforces the exact frame
    count via ``-frames:v``. The original ``project.json`` clip data is **not** mutated; the
    resolved range is used only for this export. Any warnings produced by the resolver are
    formatted via ``format_align_warning`` (UI-localized) or a built-in English fallback and
    appended to the returned message list.

    Optional on_progress(overall_0_1, clip_index_1based, clip_total, out_frame, clip_total_frames):
    overall combines clip index with per-clip progress from ffmpeg stderr (frame= and/or time=).
    cancel_check, when true, stops between clips and inside each ffmpeg encode.
    on_clip_begin(idx_zero_based, clip_total, L, duration_sec) runs before each encode.
    stderr_line_hook receives each stderr line when set (enables live ``frame=``/``time=`` parsing).
    All exports use fast input seek (``-ss`` before ``-i``) to ensure real-time progress updates.
    ``verify_strict_output_frames`` (default True) runs an ffprobe packet/frame count pass after
    successful strict encodes.
    When strict align is off, ``warn_non_8n1`` (setting name legacy) together with
    ``warn_pattern_x`` / ``warn_pattern_y`` warns if :func:`~easyclip.core.align_io.output_frame_count`
    at ``export_fps`` does not satisfy the x·n+y pattern; ``x=1`` skips that check.
    ``format_predicted_pattern_warn(n_out, export_fps, x, y)`` supplies localized text when set.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    export_fps = max(1, min(240, int(export_fps)))
    template = (export_filename_template or "").strip() or DEFAULT_EXPORT_FILENAME_TEMPLATE
    fmt_warn = format_align_warning or _default_align_warning_text
    messages: list[tuple[str, str]] = []
    to_export = [c for c in data.clips if c.end_frame is not None]
    source_p = Path(data.source_path)
    source_name = source_p.stem
    source_ext = source_p.suffix.lower().lstrip(".")
    job_ts = int(time.time())
    job_tc = datetime.fromtimestamp(job_ts).strftime("%Y-%m-%d_%H-%M-%S")
    used_names: set[str] = set()
    try:
        source_has_audio = probe_has_audio(data.source_path)
    except Exception:  # noqa: BLE001
        source_has_audio = True
    # Cached subtitle track (parsed once, used per clip)
    _subtitle_track: SubtitleTrack | None = None
    _subtitle_track_cache_path: str | None = None
    if subtitle_source_path:
        try:
            _subtitle_track = parse_subtitle_file(subtitle_source_path)
        except Exception:
            _subtitle_track = None
        _subtitle_track_cache_path = subtitle_source_path
    n = len(to_export)
    for idx, clip in enumerate(to_export):
        if cancel_check and cancel_check():
            raise InterruptedError("export cancelled")
        end_f = clip.end_frame
        assert end_f is not None

        # ------------------------------------------------------------------
        # Resolve align constraint (if any). The resolver returns a possibly-adjusted source
        # frame range plus the exact output frame count we will hand to ``-frames:v``. We
        # compute *before* template/var rendering so file names see the resolved range.
        # ------------------------------------------------------------------
        eff_start = int(clip.start_frame)
        eff_end = int(end_f)
        n_target_frames: int | None = None
        align_warnings: list[AlignWarning] = []
        if align_constraint is not None and align_constraint.enabled:
            res = apply_align_constraint(
                start_frame=eff_start,
                end_frame=eff_end,
                constraint=align_constraint,
                fps_src=float(tb.fps),
                fps_out=export_fps,
                total_src_frames=int(tb.total_frames),
            )
            eff_start = int(res.new_start_frame)
            eff_end = int(res.new_end_frame)
            n_target_frames = res.n_target_frames
            align_warnings = list(res.warnings)

        L = eff_end - eff_start + 1
        L_for_progress = n_target_frames if n_target_frames is not None else L
        if on_progress:
            on_progress(idx / max(n, 1), idx + 1, n, 0, L_for_progress)
        t0 = tb.frame_to_time(eff_start)
        duration = L / tb.fps
        start_ms = max(0, int(round(t0 * 1000.0)))
        end_t = t0 + duration
        end_ms = max(start_ms, int(round(end_t * 1000.0)))
        duration_ms = max(0, end_ms - start_ms)
        vars_map: dict[str, object] = {
            "source_name": source_name,
            "source_ext": source_ext,
            "clip_index": idx + 1,
            "start_frame": eff_start,
            "end_frame": eff_end,
            "start_time_ms": start_ms,
            "end_time_ms": end_ms,
            "start_time_s": float(t0),
            "end_time_s": float(end_t),
            "start_tc": _safe_timecode(t0),
            "end_tc": _safe_timecode(end_t),
            "duration_frames": L,
            "duration_ms": duration_ms,
            "duration_s": float(duration),
            "job_ts": job_ts,
            "job_tc": job_tc,
        }
        template_error: str | None = None
        try:
            stem = _render_filename_stem(template, vars_map)
        except Exception as e:  # noqa: BLE001
            stem = _render_filename_stem(DEFAULT_EXPORT_FILENAME_TEMPLATE, vars_map)
            template_error = f"invalid filename template, fallback to default: {e}"
        dst = _unique_output_path(out, stem, ".mp4", used_names)
        output_name = dst.name
        # When strict align is off, optionally warn if predicted CFR output length does not match
        # ``x·n+y`` using the same x/y as the export dialog (``warn_pattern_*``), not a hardcoded
        # 8n+1 rule. ``x=1`` disables this modulus check.
        n_out = output_frame_count(eff_start, eff_end, float(tb.fps), export_fps)
        wx, wy = normalize_xy(warn_pattern_x, warn_pattern_y)
        if (
            align_constraint is None
            and warn_non_8n1
            and wx > 1
            and not is_valid_length(n_out, wx, wy)
        ):
            if format_predicted_pattern_warn is not None:
                warn_txt = format_predicted_pattern_warn(n_out, export_fps, wx, wy)
            else:
                warn_txt = (
                    f"predicted output length {n_out} frames at {export_fps} fps "
                    f"does not match x·n+y for x={wx}, y={wy}"
                )
            messages.append((output_name, warn_txt))
        for w in align_warnings:
            messages.append((output_name, fmt_warn(w)))
        if template_error:
            messages.append((output_name, template_error))
        if on_clip_begin is not None:
            on_clip_begin(idx, n, L, float(duration))

        # Progress UI is driven against ``L_for_progress`` (see above).
        # Largest output frame index seen from ffmpeg (>0 only). Initial ``frame=0`` must not
        # lock us into the frame branch, or later ticks with only ``out_time_*`` never update UI.
        last_ff_frame: int | None = None

        def clip_progress(tick: FfmpegEncodeProgress) -> None:
            nonlocal last_ff_frame
            if on_progress is None:
                return
            if tick.is_seeking:
                overall_0_1 = idx / max(n, 1)
                on_progress(overall_0_1, idx + 1, n, -1, L_for_progress)
                return

            if tick.out_frame is not None:
                ff = min(L_for_progress, max(0, tick.out_frame))
                if ff > 0:
                    last_ff_frame = max(last_ff_frame or 0, ff)
            if last_ff_frame is not None and last_ff_frame > 0:
                frac = min(1.0, last_ff_frame / float(L_for_progress)) if L_for_progress > 0 else 1.0
                f_show = last_ff_frame
            else:
                frac = min(1.0, max(0.0, tick.out_time_sec / duration)) if duration > 1e-12 else 1.0
                est = (
                    int(round(tick.out_time_sec / duration * float(L_for_progress)))
                    if duration > 1e-12
                    else 0
                )
                f_show = min(L_for_progress, max(0, est))
            overall = (idx + frac) / n
            on_progress(overall, idx + 1, n, f_show, L_for_progress)

        export_clip_mp4(
            data.source_path,
            t0,
            duration,
            dst,
            video_codec=export_video_codec,
            video_bitrate_kbps=export_video_bitrate_kbps,
            output_fps=export_fps,
            source_has_audio=source_has_audio,
            video_rate_mode=export_video_rate_mode,
            video_quality=export_video_quality,
            strong_av_sync=strong_av_sync,
            video_filter=video_filter,
            verify_strict_output_frames=verify_strict_output_frames,
            n_target_frames=n_target_frames,
            on_progress=clip_progress if on_progress else None,
            cancel_check=cancel_check,
            stderr_line_hook=stderr_line_hook,
        )
        # ------------------------------------------------------------------
        # Subtitle cutting: export matching subtitle file alongside the clip.
        # Use the actual output video duration (which may differ from the
        # source duration when export_fps != source_fps):
        #   - Strict mode:    n_target_frames / export_fps  (exact)
        #   - Non-strict:     round(duration * export_fps) / export_fps
        #     The default fps filter uses round=near, so we use round() to
        #     match; n_out from output_frame_count() uses floor() and can
        #     be 1 frame short.
        # ------------------------------------------------------------------
        if _subtitle_track is not None and _subtitle_track.entries:
            if n_target_frames is not None:
                output_duration_sec = n_target_frames / float(export_fps)
            else:
                output_duration_sec = round(duration * export_fps) / float(export_fps)
            clip_start_sec = float(t0)
            clip_end_sec = float(t0) + output_duration_sec
            cut_track = cut_subtitle_track(_subtitle_track, clip_start_sec, clip_end_sec)
            if cut_track.entries:
                sub_ext = ".vtt" if _subtitle_track.format.value == "vtt" else ".srt"
                if _subtitle_track.format.value in ("ass", "ssa"):
                    sub_ext = "." + _subtitle_track.format.value
                sub_path = dst.with_suffix(sub_ext)
                try:
                    write_subtitle_file(cut_track, sub_path)
                except Exception:
                    pass  # Subtitle export failure should not block video export
    return messages
