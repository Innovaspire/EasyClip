"""Export / UI alignment in *output* frame space: lengths satisfying ``N = x*n + y`` (mod x)."""

from __future__ import annotations

from dataclasses import dataclass, field


def normalize_xy(x: int, y: int) -> tuple[int, int]:
    x = max(1, min(1024, int(x)))
    y = int(y) % x
    return x, y


def min_valid_n(x: int, y: int) -> int:
    x, y = normalize_xy(x, y)
    if y == 0:
        return x
    return y


def is_valid_length(n: int, x: int, y: int) -> bool:
    x, y = normalize_xy(x, y)
    n = int(n)
    if n < min_valid_n(x, y):
        return False
    return (n - y) % x == 0


def nearest_n_ceil(n_cur: int, x: int, y: int) -> int:
    x, y = normalize_xy(x, y)
    mv = min_valid_n(x, y)
    n_cur = max(0, int(n_cur))
    if n_cur <= mv:
        return mv
    r = (n_cur - y) % x
    if r == 0:
        return n_cur
    return n_cur + (x - r)


def nearest_n_floor(n_cur: int, x: int, y: int) -> int | None:
    x, y = normalize_xy(x, y)
    mv = min_valid_n(x, y)
    n_cur = max(0, int(n_cur))
    r = (n_cur - y) % x
    n = n_cur - r
    if n < mv:
        return None
    return n


def output_frame_count(start_f: int, end_f: int, fps_src: float, fps_out: float) -> int:
    """Predict inclusive output frame count for ``fps=fps_out:round=down:start_time=0`` + reset PTS.

    Uses segment ``[start_f/fps_src, (end_f+1)/fps_src)`` in seconds.
    """
    fs = max(1e-9, float(fps_src))
    fo = max(1e-9, float(fps_out))
    s = max(0, int(start_f))
    e = max(s, int(end_f))
    t0 = float(s) / fs
    t1 = float(e + 1) / fs
    dur = max(0.0, t1 - t0)
    # Half-open timeline [t0, t1) at output rate ``fo`` with ``round=down`` yields
    # ``floor(dur * fo)`` ticks when the first sample is pinned at ``t0``; for CFR resample
    # of an inclusive source span this matches ``end - start + 1`` when ``fs == fo``.
    # ``0`` means the span is shorter than one output frame at ``fo`` under this model;
    # strict export then needs a wider source span (see :func:`compute_export_plan`).
    return max(0, int(dur * fo + 1e-9))


def _clamp_frame(v: int, total_frames: int) -> int:
    t = max(1, int(total_frames))
    return max(0, min(int(v), t - 1))


def _expand_span_to_output_n(
    s0: int,
    e0: int,
    *,
    n_target: int,
    fps_src: float,
    fps_out: int,
    total_frames: int,
    apply: str,
) -> tuple[int, int]:
    fs = max(1e-9, float(fps_src))
    fo = max(1, int(fps_out))
    s0 = _clamp_frame(s0, total_frames)
    e0 = _clamp_frame(e0, total_frames)
    if e0 < s0:
        e0 = s0
    t0 = float(s0) / fs
    t1 = float(e0 + 1) / fs
    mid = (t0 + t1) / 2.0
    n_target = max(1, int(n_target))
    # Half-open span length ``[t, t+d)`` with ``d = n_target/fps`` ⇒ ``int(d*fps)=n_target``.
    duration_out = n_target / float(fo) - 1e-9
    ap = (apply or "tail").strip().lower()
    if ap not in {"tail", "head", "symmetric"}:
        ap = "tail"
    if ap == "tail":
        t_start = t0
        t_end = t_start + duration_out
    elif ap == "head":
        t_end = t1
        t_start = t_end - duration_out
    else:
        half = duration_out / 2.0
        t_start = mid - half
        t_end = mid + half

    s1 = _clamp_frame(int(round(t_start * fs)), total_frames)
    e1 = _clamp_frame(int(round(t_end * fs)) - 1, total_frames)
    if e1 < s1:
        e1 = s1

    def n_span(s: int, e: int) -> int:
        return output_frame_count(s, e, fs, fo)

    need = n_target
    cap = n_span(s1, e1)
    guard = 0
    while cap < need and guard < max(1, total_frames) * 4:
        guard += 1
        if ap == "tail" and e1 < total_frames - 1:
            e1 += 1
        elif ap == "head" and s1 > 0:
            s1 -= 1
        elif ap == "symmetric":
            moved = False
            if e1 < total_frames - 1:
                e1 += 1
                moved = True
            if s1 > 0:
                s1 -= 1
                moved = True
            if not moved:
                if s1 > 0:
                    s1 -= 1
                elif e1 < total_frames - 1:
                    e1 += 1
                else:
                    break
        else:
            if ap == "tail" and s1 > 0:
                s1 -= 1
            elif ap == "head" and e1 < total_frames - 1:
                e1 += 1
            else:
                break
        cap = n_span(s1, e1)
    return s1, e1


@dataclass(frozen=True)
class AlignExportPlan:
    start_frame: int
    end_frame: int
    start_sec: float
    duration_sec: float
    n_target_frames: int | None
    warnings: tuple[str, ...]


def compute_export_plan(
    *,
    start_frame: int,
    end_frame: int,
    total_frames: int,
    fps_src: float,
    fps_out: int,
    align_enabled: bool,
    align_x: int,
    align_y: int,
    align_round: str,
    align_apply: str,
) -> AlignExportPlan:
    fs = max(1e-9, float(fps_src))
    fo = max(1, min(240, int(fps_out)))
    tot = max(1, int(total_frames))
    s0 = _clamp_frame(int(start_frame), tot)
    e0 = _clamp_frame(int(end_frame), tot)
    if e0 < s0:
        e0 = s0

    warns: list[str] = []
    x, y = normalize_xy(align_x, align_y)

    if not align_enabled or x <= 1:
        dur = float(e0 - s0 + 1) / fs
        return AlignExportPlan(
            start_frame=s0,
            end_frame=e0,
            start_sec=float(s0) / fs,
            duration_sec=max(1e-9, dur),
            n_target_frames=None,
            warnings=tuple(warns),
        )

    n_cur = output_frame_count(s0, e0, fs, fo)
    rd = (align_round or "ceil").strip().lower()
    if rd == "floor":
        n_want = nearest_n_floor(n_cur, x, y)
        if n_want is None:
            n_want = min_valid_n(x, y)
            warns.append("export.align_warn.floor_fallback_minimum")
    else:
        n_want = nearest_n_ceil(n_cur, x, y)

    ap = (align_apply or "tail").strip().lower()
    s1, e1 = _expand_span_to_output_n(
        s0, e0, n_target=n_want, fps_src=fs, fps_out=fo, total_frames=tot, apply=ap
    )
    n_cap = output_frame_count(s1, e1, fs, fo)
    n_final = min(n_want, n_cap)
    if n_final < n_want:
        warns.append("export.align_warn.constraint_reduced_by_span")

    if n_final < min_valid_n(x, y):
        n_final = n_cap
        warns.append("export.align_warn.source_too_short_for_pattern")

    if n_final > 0 and not is_valid_length(n_final, x, y):
        warns.append("export.align_warn.output_not_matching_pattern")

    t_start = float(s1) / fs
    dur = float(e1 - s1 + 1) / fs

    n_strict: int | None = None
    if n_final > 0:
        need = n_final / float(fo) - 1e-9
        if dur + 1e-9 >= need and is_valid_length(n_final, x, y):
            n_strict = int(n_final)
        else:
            if dur + 1e-9 < need:
                warns.append("export.align_warn.duration_insufficient")
            if (
                n_cap > 0
                and is_valid_length(n_cap, x, y)
                and dur + 1e-9 >= n_cap / float(fo) - 1e-9
            ):
                n_strict = int(n_cap)
    elif s1 <= e1 and n_want > 0:
        warns.append("export.align_warn.zero_output_span")

    return AlignExportPlan(
        start_frame=s1,
        end_frame=e1,
        start_sec=t_start,
        duration_sec=max(1e-9, dur),
        n_target_frames=n_strict,
        warnings=tuple(warns),
    )


def snap_moving_end(
    *,
    start_f: int,
    playhead_f: int,
    fps_src: float,
    fps_out: int,
    total_frames: int,
    x: int,
    y: int,
    ceil: bool,
) -> int | None:
    """Closed or open clip: fix ``start_f``, snap end toward ``playhead_f`` (Ceil/F on length in output space)."""
    tot = max(1, int(total_frames))
    fs = max(1e-9, float(fps_src))
    fo = max(1e-9, float(fps_out))
    s = _clamp_frame(int(start_f), tot)
    ph = _clamp_frame(int(playhead_f), tot)
    if ph < s:
        return None
    L = output_frame_count(s, ph, fs, fo)
    if ceil:
        n_tgt = nearest_n_ceil(L, x, y)
    else:
        nf = nearest_n_floor(L, x, y)
        n_tgt = nf if nf is not None else min_valid_n(x, y)
    t0 = float(s) / fs
    dur_out = n_tgt / fo - 1e-9
    t1 = t0 + dur_out
    e = _clamp_frame(int(round(t1 * fs)) - 1, tot)
    if e < s:
        e = s
    while output_frame_count(s, e, fs, fo) < n_tgt and e < tot - 1:
        e += 1
    while e > s and output_frame_count(s, e - 1, fs, fo) >= n_tgt:
        e -= 1
    if output_frame_count(s, e, fs, fo) != n_tgt:
        return None
    return e


def snap_moving_start(
    *,
    end_f: int,
    playhead_f: int,
    fps_src: float,
    fps_out: int,
    total_frames: int,
    x: int,
    y: int,
    ceil: bool,
) -> int | None:
    """Fix ``end_f``, snap start toward ``playhead_f``."""
    tot = max(1, int(total_frames))
    fs = max(1e-9, float(fps_src))
    fo = max(1e-9, float(fps_out))
    e = _clamp_frame(int(end_f), tot)
    ph = _clamp_frame(int(playhead_f), tot)
    if ph > e:
        return None
    L = output_frame_count(ph, e, fs, fo)
    if ceil:
        n_tgt = nearest_n_ceil(L, x, y)
    else:
        nf = nearest_n_floor(L, x, y)
        n_tgt = nf if nf is not None else min_valid_n(x, y)
    t1 = float(e + 1) / fs
    dur_out = n_tgt / fo - 1e-9
    t0 = t1 - dur_out
    s = _clamp_frame(int(round(t0 * fs)), tot)
    if s > e:
        s = e
    while output_frame_count(s, e, fs, fo) < n_tgt and s > 0:
        s -= 1
    while s < e and output_frame_count(s + 1, e, fs, fo) >= n_tgt:
        s += 1
    if output_frame_count(s, e, fs, fo) != n_tgt:
        return None
    return s


@dataclass(frozen=True)
class AlignWarning:
    key: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AlignConstraint:
    enabled: bool
    x: int
    y: int
    round: str = "ceil"
    apply: str = "tail"


@dataclass(frozen=True)
class AlignResolveResult:
    new_start_frame: int
    new_end_frame: int
    n_target_frames: int | None
    warnings: list[AlignWarning]


def apply_align_constraint(
    *,
    start_frame: int,
    end_frame: int,
    constraint: AlignConstraint,
    fps_src: float,
    fps_out: int,
    total_src_frames: int,
) -> AlignResolveResult:
    tot = max(1, int(total_src_frames))
    s0 = _clamp_frame(int(start_frame), tot)
    e0 = _clamp_frame(int(end_frame), tot)
    if e0 < s0:
        e0 = s0
    if not constraint.enabled:
        return AlignResolveResult(
            new_start_frame=s0,
            new_end_frame=e0,
            n_target_frames=None,
            warnings=[],
        )
    x, _ = normalize_xy(constraint.x, constraint.y)
    if x <= 1:
        return AlignResolveResult(
            new_start_frame=s0,
            new_end_frame=e0,
            n_target_frames=None,
            warnings=[],
        )
    plan = compute_export_plan(
        start_frame=s0,
        end_frame=e0,
        total_frames=tot,
        fps_src=fps_src,
        fps_out=fps_out,
        align_enabled=True,
        align_x=constraint.x,
        align_y=constraint.y,
        align_round=constraint.round,
        align_apply=constraint.apply,
    )
    warns = [AlignWarning(key=k, params={}) for k in plan.warnings]
    return AlignResolveResult(
        new_start_frame=plan.start_frame,
        new_end_frame=plan.end_frame,
        n_target_frames=plan.n_target_frames,
        warnings=warns,
    )
