"""Waveform peaks: optional showwavespic (mid/long files), vectorized PCM fallback.

PCM path decodes mono float samples and records **per-time-bin min, max and
RMS** (the same dual-envelope family used by Audacity-style displays). Peak
(min/max) is the outer envelope; RMS is the inner envelope that conveys
perceived loudness. For heavily compressed / limited audio the peak envelope
collapses to a solid block (samples reach ±full scale in every bin), but RMS
still reveals structural variation, so the renderer draws both.

The UI merges bins per **screen pixel column** again (min of mins, max of
maxs, max of rms) so zoom matches common editors.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from easyclip.core.ffmpeg_util import find_ffmpeg, probe_has_audio

# Beyond this, showwavespic (full decode + huge PNG) is skipped — streaming is often smoother.
_SHOWWAVESPIC_MAX_DURATION_SEC = 2700.0  # 45 min
# Below this, skip showwavespic: FFmpeg draws the trace using nearly the full image height per
# column, so per-column min/max on the PNG reads as "always full scale" for short clips.
_SHOWWAVESPIC_MIN_DURATION_SEC = 30.0
_SHOWWAVESPIC_MAX_WIDTH = 16384
_SHOWWAVESPIC_HEIGHT = 192

# Higher rate = finer peaks per time bin (slower decode); tuned for frame-accurate cutting.
STREAM_SAMPLE_RATE = 96000

# Bumped when peak algorithm or cache semantics change (invalidates waveform.npz without bins/sr change).
# v5: cache now includes per-bin RMS array alongside mins/maxs (dual envelope rendering).
# v6: showwavespic dispatch now treats "uniformly mid-tall" envelopes (compressed
#     masters) as uninformative and falls through to streaming. Pre-v6 v5 caches
#     could have been written with rms=zeros for those files; bumping forces a
#     recompute so the renderer actually receives a real RMS envelope.
# v7: showwavespic fast path is disabled at dispatch (see compute_waveform_peaks).
#     Any v6 cache that was generated *before* the dispatch was disabled may still
#     hold an unreliable peak envelope (PNG-derived amp formula collapses to
#     ~constant for heavily mastered audio); v7 forces those to recompute via the
#     streaming path so peaks and RMS are both physically meaningful.
WAVE_NPZ_VERSION = 7


def _subprocess_platform_flags() -> dict[str, object]:
    """Hide transient console windows for ffmpeg on Windows."""
    if os.name != "nt":
        return {}
    extra: dict[str, object] = {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        extra["creationflags"] = create_no_window
    startup_cls = getattr(subprocess, "STARTUPINFO", None)
    startf_use_showwindow = getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    if startup_cls and startf_use_showwindow:
        si = startup_cls()
        si.dwFlags |= startf_use_showwindow
        si.wShowWindow = 0
        extra["startupinfo"] = si
    return extra


def prefer_showwavespic(duration_sec: float) -> bool:
    """Use PNG showwavespic path only for mid/long media; short clips use PCM streaming."""
    d = float(duration_sec)
    return _SHOWWAVESPIC_MIN_DURATION_SEC <= d <= _SHOWWAVESPIC_MAX_DURATION_SEC


def recommended_peak_bins(window_width: int, duration_sec: float) -> int:
    """Bins for cache key and analysis: strong temporal + horizontal oversampling vs UI width."""
    w = max(1, window_width)
    d = max(0.001, float(duration_sec))
    by_time = int(d * 220) + 1
    by_px = w * 36
    return int(min(65536, max(8000, max(by_time, by_px))))


def _peaks_from_showwavespic_png(
    gray: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """gray shape (H, W) -> symmetric mins/maxs per column (fast min/max, no percentile)."""
    if gray.size == 0:
        return np.array([], np.float32), np.array([], np.float32)
    p_lo = gray.min(axis=0)
    p_hi = gray.max(axis=0)
    amp = np.clip((p_hi.astype(np.float32) - p_lo.astype(np.float32)) / 255.0, 0.0, 1.0)
    half = amp * 0.5
    mins = -half
    maxs = half
    return mins.astype(np.float32), maxs.astype(np.float32)


def _showwavespic_envelope_is_uninformative(
    mins: np.ndarray, maxs: np.ndarray
) -> bool:
    """Reject showwavespic results that won't render with usable structure.

    Two failure modes are folded into a single criterion:

    1. **Truly saturated** PNGs — the trace is so tall that every column is
       near-full amplitude. Per-column min/max from the PNG collapses to a
       constant block (the original "blocky waveform" complaint).
    2. **Uniformly mid-tall** PNGs — for heavily compressed / mastered audio
       (typical of TV / film / mastered music masters) the showwavespic trace
       has a roughly constant height over the entire video. The column-wise
       amp formula in :func:`_peaks_from_showwavespic_png` then yields a
       near-constant value (~0.5) regardless of any underlying loudness
       variation. The user sees a solid mid-height block with no structure.

    Both cases share one signature: low variance of the per-column span. We
    detect that and fall back to the PCM streaming path, which produces a
    real RMS inner envelope that *does* reveal structure even when peaks are
    uniform.
    """
    if mins.size == 0 or maxs.size == 0:
        return False
    span = np.clip(maxs.astype(np.float64) - mins.astype(np.float64), 0.0, 1.0)
    if span.size < 64:
        return False
    p10 = float(np.percentile(span, 10.0))
    p50 = float(np.percentile(span, 50.0))
    p90 = float(np.percentile(span, 90.0))
    # Low spread + non-trivial median → block-like envelope, regardless of how
    # absolutely tall it is. (Old threshold required p10>0.70 / p50>0.82, which
    # missed the "uniformly mid-tall" case where compressed audio masters land.)
    if p50 > 0.30 and (p90 - p10) < 0.22:
        return True
    return False


def _compute_waveform_showwavespic(
    video_path: str | Path,
    num_bins: int,
    ffmpeg: str,
    interrupt_check: Callable[[], bool] | None = None,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Single-pass FFmpeg draw; returns None on failure. Raises InterruptedError if cancelled."""
    w = max(320, min(_SHOWWAVESPIC_MAX_WIDTH, int(num_bins)))
    h = _SHOWWAVESPIC_HEIGHT
    fc = (
        f"[0:a]aformat=channel_layouts=mono,aresample=8000,"
        f"showwavespic=s={w}x{h}:colors=0x50d090|0x1e1e24"
    )
    fd, out_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-filter_complex",
            fc,
            "-frames:v",
            "1",
            out_path,
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **_subprocess_platform_flags(),
        )
        while proc.poll() is None:
            if interrupt_check and interrupt_check():
                proc.kill()
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                raise InterruptedError
            time.sleep(0.05)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        if proc.stdout:
            proc.stdout.read()
        if proc.stderr:
            proc.stderr.read()
        if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) < 32:
            return None
        with Image.open(out_path) as im:
            gray = np.asarray(im.convert("L"), dtype=np.float32)
        mins, maxs = _peaks_from_showwavespic_png(gray)
        if mins.shape[0] != num_bins and mins.shape[0] > 0:
            x_old = np.linspace(0.0, 1.0, mins.shape[0], dtype=np.float64)
            x_new = np.linspace(0.0, 1.0, num_bins, dtype=np.float64)
            mins = np.interp(x_new, x_old, mins.astype(np.float64)).astype(np.float32)
            maxs = np.interp(x_new, x_old, maxs.astype(np.float64)).astype(np.float32)
        return mins, maxs
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _compute_waveform_streaming_vectorized(
    video_path: str | Path,
    num_bins: int,
    duration_sec: float,
    sample_rate: int,
    ffmpeg: str,
    interrupt_check: Callable[[], bool] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode mono f32le at sample_rate; bin with numpy ufunc.at.

    Returns (mins, maxs, rms) per bin in float32. RMS is computed as
    sqrt(mean(x^2)) per bin; it gives an inner envelope that survives heavy
    dynamic-range compression (where min/max collapse to ±full-scale in every
    bin and would otherwise paint as a solid block).
    """
    total_samples = max(1, int(duration_sec * sample_rate))
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        **_subprocess_platform_flags(),
    )
    assert proc.stdout is not None
    assert num_bins >= 1
    mins = np.full(num_bins, np.inf, dtype=np.float64)
    maxs = np.full(num_bins, -np.inf, dtype=np.float64)
    sq_sum = np.zeros(num_bins, dtype=np.float64)
    counts = np.zeros(num_bins, dtype=np.int64)
    block = 262144
    idx = 0
    while True:
        if interrupt_check and interrupt_check():
            proc.kill()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.terminate()
            raise InterruptedError
        raw = proc.stdout.read(block * 4)
        if not raw:
            break
        n = len(raw) // 4
        if n == 0:
            continue
        arr = np.frombuffer(raw[: n * 4], dtype="<f4")
        arr_d = arr.astype(np.float64)
        global_idx = np.arange(idx, idx + n, dtype=np.int64)
        bin_idx = np.minimum(num_bins - 1, (global_idx * num_bins) // total_samples).astype(np.int64)
        np.minimum.at(mins, bin_idx, arr_d)
        np.maximum.at(maxs, bin_idx, arr_d)
        np.add.at(sq_sum, bin_idx, arr_d * arr_d)
        np.add.at(counts, bin_idx, 1)
        idx += n
    proc.wait()
    mins = np.where(np.isfinite(mins), mins, 0.0).astype(np.float32)
    maxs = np.where(np.isfinite(maxs), maxs, 0.0).astype(np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean_sq = np.divide(sq_sum, counts, out=np.zeros_like(sq_sum), where=counts > 0)
    rms = np.sqrt(np.maximum(mean_sq, 0.0)).astype(np.float32)
    return mins, maxs, rms


def compute_waveform_peaks(
    video_path: str | Path,
    num_bins: int,
    duration_sec: float,
    sample_rate: int | None = None,
    ffmpeg: str | None = None,
    *,
    has_audio: bool | None = None,
    interrupt_check: Callable[[], bool] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Return (min_peaks, max_peaks, rms) float32 of length num_bins.

    ``rms`` is the per-bin RMS amplitude (>=0). When the showwavespic fast path
    is used, RMS is not natively available from the PNG, so a zero array is
    returned and the renderer falls back to a peak-only display for that file.
    The streaming path always provides a real RMS envelope, which is what the
    UI uses to render the inner / "perceived loudness" envelope.

    If ``has_audio`` is None, ffprobe is used once to detect audio.
    """
    ffmpeg = ffmpeg or find_ffmpeg()[0]
    sr = int(sample_rate) if sample_rate is not None else STREAM_SAMPLE_RATE
    if has_audio is None:
        has_audio = probe_has_audio(video_path, None)
    if not has_audio:
        z = np.zeros(num_bins, dtype=np.float32)
        return z, z, z

    # NOTE: showwavespic fast path is intentionally disabled.
    #
    # The PNG-derived ``amp`` formula in :func:`_peaks_from_showwavespic_png`
    # measures whether each column has *any* mix of foreground and background
    # pixels, not the actual trace height; for heavily mastered / compressed
    # audio (a common case for film, TV, music masters and adult content) the
    # PNG renders as a uniformly tall band, so amp collapses to a near-constant
    # value across columns and the resulting peak envelope is uninformative
    # *and* missing the RMS layer the dual-envelope renderer needs.
    #
    # Streaming costs ~10–30 s of one-time decode for typical mid-length media
    # but produces (a) physically correct min/max peaks and (b) a real per-bin
    # RMS envelope. The result is cached on disk via WAVE_NPZ_VERSION-keyed
    # ``waveform.npz``, so subsequent loads of the same file are instant.
    #
    # Helpers ``_compute_waveform_showwavespic`` and
    # ``_showwavespic_envelope_is_uninformative`` are kept (and used by
    # ``prefer_showwavespic`` tests) for any future re-enable, but they no
    # longer dispatch.
    _ = prefer_showwavespic  # silence "unused" linters for the public helper

    return _compute_waveform_streaming_vectorized(
        video_path, num_bins, duration_sec, sr, ffmpeg, interrupt_check=interrupt_check
    )
