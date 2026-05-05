"""FFmpeg / ffprobe subprocess helpers."""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import threading
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from typing import Any, Iterator

from easyclip.core.export_debug import export_debug_log

logger = logging.getLogger(__name__)

_FFPROBE_META_TIMEOUT_SEC = 20
_FFPROBE_AUDIO_TIMEOUT_SEC = 20
_FFPROBE_BITRATE_TIMEOUT_SEC = 12
# Demux walk for -count_packets / -count_frames (full file scan; OK for exported clips).
_FFPROBE_MUX_COUNT_TIMEOUT_SEC = 180.0


def _subprocess_platform_flags() -> dict[str, Any]:
    """Hide transient console windows for ffmpeg/ffprobe on Windows."""
    if os.name != "nt":
        return {}
    extra: dict[str, Any] = {}
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


def _pair_in_dir(d: Path) -> tuple[str, str] | None:
    for ff_name, pr_name in (("ffmpeg.exe", "ffprobe.exe"), ("ffmpeg", "ffprobe")):
        ffc, prb = d / ff_name, d / pr_name
        if ffc.is_file() and prb.is_file():
            return str(ffc), str(prb)
    return None


def _repo_vendor_pair() -> tuple[str, str] | None:
    """Optional ``<repo>/vendor`` pair (``ffmpeg_util`` lives under ``src/easyclip/core``)."""
    try:
        root = Path(__file__).resolve().parents[3]
    except IndexError:
        return None
    return _pair_in_dir(root / "vendor")


def find_ffmpeg() -> tuple[str, str]:
    """Return (ffmpeg, ffprobe) executable paths.

    **Frozen (打包)**：优先 ``<exe-dir>/ffmpeg`` 子目录（自带或启动时下载的 ffmpeg/ffprobe），
    兼容旧版本也会回退检查 **可执行文件所在目录**；
    避免与系统环境中的其他 FFmpeg 配置混用。

    非 frozen（开发）与 frozen 的后续查找统一为：仓库 ``vendor/`` → **PATH**。
    不再读取 ``EASYCLIP_FFMPEG`` / ``EASYCLIP_FFPROBE`` / ``EASYCLIP_FFMPEG_BIN_DIR``。
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        pair = _pair_in_dir(base / "ffmpeg")
        if pair:
            logger.info("Using FFmpeg in app ffmpeg/ dir: %s", pair[0])
            return pair
        pair = _pair_in_dir(base)
        if pair:
            logger.info("Using FFmpeg next to executable: %s", pair[0])
            return pair

    pair = _repo_vendor_pair()
    if pair:
        return pair

    names = ("ffmpeg", "ffmpeg.exe")
    path_env = os.environ.get("PATH", "")
    sep = os.pathsep
    for d in path_env.split(sep):
        for n in names:
            candidate = Path(d) / n
            if candidate.is_file():
                ff = str(candidate)
                prob = candidate.parent / ("ffprobe.exe" if n.endswith(".exe") else "ffprobe")
                if prob.is_file():
                    return ff, str(prob)
    return "ffmpeg", "ffprobe"


@dataclass(frozen=True)
class FfmpegEncodeProgress:
    """One progress tick (``-progress`` protocol and/or legacy stderr stats)."""

    out_time_sec: float
    out_frame: int | None
    is_seeking: bool = False


_OUT_TIME_TS_RE = re.compile(r"(?P<h>\d+):(?P<m>\d+):(?P<s>\d+\.?\d*)")

# libx264-style encoding status on stderr (disabled by ``-nostats``).
_ENC_STDERR_FRAME_RE = re.compile(r"(?:^|\s)frame=\s*(\d+)")
_ENC_STDERR_TIME_RE = re.compile(r"\btime=(\d+):(\d+):(\d+\.?\d*)")

_VIDEO_ENCODERS_CACHE: dict[str, set[str]] = {}
_AUTO_VIDEO_CODEC_ORDER = (
    "libx264",
    "h264_nvenc",
    "h264_amf",
    "h264_qsv",
    "libopenh264",
    "mpeg4",
)


def list_video_encoders(ffmpeg: str) -> set[str]:
    """Return available video encoder names from ``ffmpeg -encoders`` (best effort)."""
    cached = _VIDEO_ENCODERS_CACHE.get(ffmpeg)
    if cached is not None:
        return cached
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_subprocess_platform_flags(),
        )
    except Exception:
        _VIDEO_ENCODERS_CACHE[ffmpeg] = set()
        return set()
    if proc.returncode != 0:
        _VIDEO_ENCODERS_CACHE[ffmpeg] = set()
        return set()
    enc: set[str] = set()
    for raw in (proc.stdout or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("Encoders:") or line.startswith("--"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        flags, name = parts[0], parts[1]
        if flags and flags[0] == "V":
            enc.add(name)
    _VIDEO_ENCODERS_CACHE[ffmpeg] = enc
    return enc


def resolve_video_codec(ffmpeg: str, requested: str) -> str:
    """Resolve requested codec against actual ffmpeg availability.

    ``requested="auto"`` picks from ``_AUTO_VIDEO_CODEC_ORDER``.
    """
    req = (requested or "auto").strip().lower()
    available = list_video_encoders(ffmpeg)
    if not available:
        return "libx264" if req == "auto" else req
    if req == "auto":
        for c in _AUTO_VIDEO_CODEC_ORDER:
            if c in available:
                return c
        return "mpeg4" if "mpeg4" in available else next(iter(sorted(available)))
    return req if req in available else resolve_video_codec(ffmpeg, "auto")


def _video_codec_args(codec: str, *, bitrate_kbps: int, preset: str) -> list[str]:
    """Build codec-specific args with bitrate control."""
    b = max(300, min(200000, int(bitrate_kbps)))
    if codec == "libx264":
        maxrate = int(b * 1.2)
        bufsize = int(b * 2.0)
        return [
            "-c:v",
            codec,
            "-preset",
            preset,
            "-b:v",
            f"{b}k",
            "-maxrate",
            f"{maxrate}k",
            "-bufsize",
            f"{bufsize}k",
        ]
    if codec == "libopenh264":
        return ["-c:v", codec, "-b:v", f"{b}k"]
    if codec in {"h264_nvenc", "hevc_nvenc"}:
        return ["-c:v", codec, "-preset", "p4", "-rc", "vbr", "-b:v", f"{b}k"]
    if codec in {"h264_amf", "hevc_amf"}:
        return ["-c:v", codec, "-rc", "vbr_peak", "-b:v", f"{b}k"]
    if codec in {"h264_qsv", "hevc_qsv"}:
        return ["-c:v", codec, "-b:v", f"{b}k"]
    if codec == "mpeg4":
        return ["-c:v", codec, "-b:v", f"{b}k"]
    return ["-c:v", codec]


def _quality_to_bitrate_kbps(quality: int) -> int:
    """Fallback mapping when a codec lacks quantizer-style controls."""
    q = max(0, min(51, int(quality)))
    # Lower quality value -> higher target bitrate.
    return max(400, min(50000, 20000 - int(round((q / 51.0) * 18000))))


def quantizer_symbol_for_codec(codec: str) -> str:
    """Return quantizer-like parameter name for a codec family."""
    c = (codec or "").strip().lower()
    if c == "libx264":
        return "CRF"
    if c == "mpeg4":
        return "q:v"
    if c in {"h264_nvenc", "hevc_nvenc"}:
        return "cq"
    if c in {"h264_qsv", "hevc_qsv"}:
        return "global_quality"
    return "quality"


def video_codec_args_with_rate_control(
    codec: str,
    *,
    rate_mode: str,
    bitrate_kbps: int,
    quality: int,
    preset: str,
) -> list[str]:
    """Build codec args for bitrate/quality modes."""
    mode = (rate_mode or "bitrate").strip().lower()
    b = max(300, min(200000, int(bitrate_kbps)))
    q = max(0, min(51, int(quality)))
    if mode != "quality":
        return _video_codec_args(codec, bitrate_kbps=b, preset=preset)

    if codec == "libx264":
        return ["-c:v", codec, "-preset", preset, "-crf", str(q)]
    if codec == "mpeg4":
        qv = int(round((q / 51.0) * 29.0)) + 2
        qv = max(2, min(31, qv))
        return ["-c:v", codec, "-q:v", str(qv)]
    if codec in {"h264_nvenc", "hevc_nvenc"}:
        return ["-c:v", codec, "-preset", "p4", "-rc", "vbr", "-cq", str(q), "-b:v", "0"]
    if codec in {"h264_qsv", "hevc_qsv"}:
        return ["-c:v", codec, "-global_quality", str(max(1, q))]
    # For codecs without stable quantizer controls across builds (e.g. libopenh264 / AMF),
    # degrade to a bitrate approximation so export remains reliable.
    return _video_codec_args(codec, bitrate_kbps=_quality_to_bitrate_kbps(q), preset=preset)


def _progress_state_to_tick(state: dict[str, str], duration_cap: float) -> FfmpegEncodeProgress | None:
    """Parse one ``-progress`` block (key=value lines ending with ``progress=continue``)."""
    fr: int | None = None
    raw_fr = state.get("frame")
    if raw_fr is not None:
        try:
            v = int(str(raw_fr).strip())
            if v >= 0:
                fr = v
        except ValueError:
            pass
    sec = 0.0
    got_time = False
    # Prefer ``out_time_us`` first: many builds duplicate the same integer under both keys; it is
    # microseconds, and treating it as ms (``/ 1000``) inflates time by ~1000×.
    for key, div in (("out_time_us", 1_000_000.0), ("out_time_ms", 1000.0)):
        if key not in state:
            continue
        try:
            v = int(str(state[key]).strip())
        except ValueError:
            continue
        if v < 0:
            continue
        sec = v / div
        got_time = True
        break
    if not got_time:
        ot = (state.get("out_time") or "").strip()
        # FFmpeg may emit invalid sentinel timestamps (e.g. INT64_MIN) as a negative wall-clock string;
        # do not parse those — they would match \d+:\d+:\d+ in the middle and corrupt progress.
        if ot and ot != "N/A" and not ot.startswith("-"):
            m = _OUT_TIME_TS_RE.search(ot)
            if m:
                sec = (
                    int(m.group("h")) * 3600
                    + int(m.group("m")) * 60
                    + float(m.group("s"))
                )
                got_time = True
    if duration_cap > 0.0 and got_time:
        sec = min(duration_cap, max(0.0, sec))
    elif got_time:
        sec = max(0.0, sec)
    if fr is None and not got_time:
        return None
    is_seeking = False
    if not got_time and fr == 0 and str(state.get("out_time", "")).strip() == "N/A":
        is_seeking = True
    if not got_time:
        return FfmpegEncodeProgress(0.0, fr, is_seeking=is_seeking)
    if fr is None:
        return FfmpegEncodeProgress(sec, None)
    return FfmpegEncodeProgress(sec, fr)


def stderr_encoding_stats_tick(line: str, duration_cap: float) -> FfmpegEncodeProgress | None:
    """Parse one libx264/ffmpeg encoding status line from stderr (``frame=`` / ``time=``)."""
    if "frame=" not in line and "time=" not in line:
        return None
    fr: int | None = None
    mf = _ENC_STDERR_FRAME_RE.search(line)
    if mf:
        try:
            v = int(mf.group(1))
            if v >= 0:
                fr = v
        except ValueError:
            pass
    sec = 0.0
    got_time = False
    mt = _ENC_STDERR_TIME_RE.search(line)
    if mt:
        try:
            sec = int(mt.group(1)) * 3600 + int(mt.group(2)) * 60 + float(mt.group(3))
            got_time = True
        except ValueError:
            pass
    if duration_cap > 0.0 and got_time:
        sec = min(duration_cap, max(0.0, sec))
    elif got_time:
        sec = max(0.0, sec)
    if fr is None and not got_time:
        return None
    if not got_time:
        return FfmpegEncodeProgress(0.0, fr)
    if fr is None:
        return FfmpegEncodeProgress(sec, None)
    return FfmpegEncodeProgress(sec, fr)


def _dispatch_ffmpeg_progress_line(
    raw_line: str,
    state: dict[str, str],
    *,
    duration_cap: float,
    on_progress: Callable[[FfmpegEncodeProgress], None] | None,
) -> None:
    """Handle one line of ffmpeg ``-progress`` key=value protocol."""
    line = raw_line.strip()
    if not line:
        return
    if line.startswith("progress="):
        if line in ("progress=continue", "progress=end"):
            tick = _progress_state_to_tick(state, duration_cap)
            if tick is not None and on_progress is not None:
                on_progress(tick)
                export_debug_log(
                    "ffmpeg.progress_tick",
                    source="marker",
                    marker=line,
                    frame=tick.out_frame,
                    out_time_sec=f"{tick.out_time_sec:.6f}",
                )
            state.clear()
        return
    if "=" in line and not line.startswith("["):
        k, _, v = line.partition("=")
        key = k.strip()
        state[key] = v.strip()
        # Some FFmpeg builds/environments may emit sparse or delayed ``progress=continue`` markers.
        # Emit partial ticks on key updates so UI can still move during encode.
        if on_progress is not None and key in {"frame", "out_time_us", "out_time_ms", "out_time"}:
            tick = _progress_state_to_tick(state, duration_cap)
            if tick is not None:
                on_progress(tick)
                export_debug_log(
                    "ffmpeg.progress_tick",
                    source="key",
                    key=key,
                    frame=tick.out_frame,
                    out_time_sec=f"{tick.out_time_sec:.6f}",
                )


def _drain_progress_text_buffer(
    pending: str,
    state: dict[str, str],
    *,
    duration_cap: float,
    on_progress: Callable[[FfmpegEncodeProgress], None] | None,
) -> tuple[str, int]:
    """Dispatch complete protocol records from ``pending`` using ``\\r``/``\\n`` delimiters."""
    start = 0
    i = 0
    n = len(pending)
    dispatched = 0
    while i < n:
        ch = pending[i]
        if ch == "\n" or ch == "\r":
            raw = pending[start:i]
            _dispatch_ffmpeg_progress_line(
                raw,
                state,
                duration_cap=duration_cap,
                on_progress=on_progress,
            )
            dispatched += 1
            # Collapse CRLF.
            if ch == "\r" and i + 1 < n and pending[i + 1] == "\n":
                i += 1
            start = i + 1
        i += 1
    return pending[start:], dispatched


def _drain_text_stream(stream: Any, sink: list[str]) -> None:
    try:
        for line in _iter_text_records(stream):
            sink.append(line)
    except Exception:
        pass


def _iter_text_records(stream: Any, chunk_size: int = 4096) -> Iterator[str]:
    """Yield text records split by ``\\n`` or ``\\r`` with low-latency reads.

    ``ffmpeg`` stderr stats are often carriage-return updates without newline; large-block reads
    can wait too long before returning and make UI progress appear frozen. Reading one character
    at a time keeps latency low enough for smooth progress updates.
    """
    _ = chunk_size  # kept for call compatibility
    buf: list[str] = []
    while True:
        ch = stream.read(1)
        if not ch:
            break
        if ch == "\n" or ch == "\r":
            if buf:
                yield "".join(buf)
                buf.clear()
            if ch == "\r":
                # Consume optional '\n' in CRLF without yielding an empty record.
                try:
                    nxt = stream.read(1)
                except Exception:
                    nxt = ""
                if nxt and nxt != "\n":
                    buf.append(nxt)
            continue
        buf.append(ch)
    if buf:
        yield "".join(buf)


def _drain_stderr_stream(
    stream: Any,
    sink: list[str],
    line_hook: Callable[[str], None] | None,
) -> None:
    try:
        for line in _iter_text_records(stream):
            sink.append(line)
            if line_hook is not None:
                line_hook(line)
    except Exception:
        pass


def _run_ffmpeg_pipe_progress(
    cmd: list[str],
    *,
    duration_cap: float,
    on_progress: Callable[[FfmpegEncodeProgress], None] | None,
    cancel_check: Callable[[], bool] | None,
    stderr_line_hook: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """Run ffmpeg with ``-nostats -progress …``.

    ``-progress pipe:1`` is replaced with ``tcp://127.0.0.1:<ephemeral>``: FFmpeg connects as a
    client and sends key=value lines over TCP. This avoids MSVC **full buffering** on anonymous
    pipes and on-disk progress files (common on Windows; UI then jumps once near completion).
    See FFmpeg docs: ``-progress`` update period follows ``-stats_period``.

    Optional ``stderr_line_hook`` forwards each stderr line from a drainer thread (used to parse
    live ``frame=`` / ``time=`` encoding stats when ``-nostats`` would hide them).
    """
    cmd = list(cmd)
    listen_sock: socket.socket | None = None
    try:
        export_debug_log("ffmpeg.run.start", cmd=" ".join(cmd))
        try:
            pi = cmd.index("-progress")
            if pi + 1 < len(cmd) and cmd[pi + 1] == "pipe:1":
                listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listen_sock.bind(("127.0.0.1", 0))
                listen_sock.listen(1)
                port = listen_sock.getsockname()[1]
                cmd[pi + 1] = f"tcp://127.0.0.1:{port}"
                export_debug_log("ffmpeg.run.progress_transport", transport="tcp", port=port)
                if "-stats_period" not in cmd:
                    try:
                        hi = cmd.index("-hide_banner")
                        cmd.insert(hi + 1, "-stats_period")
                        cmd.insert(hi + 2, "0.2")
                    except ValueError:
                        cmd.insert(1, "-stats_period")
                        cmd.insert(2, "0.2")
        except ValueError:
            pass

        use_tcp_progress = listen_sock is not None
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL if use_tcp_progress else subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **_subprocess_platform_flags(),
        )
        export_debug_log("ffmpeg.run.spawned", pid=proc.pid)
        err_lines: list[str] = []
        assert proc.stderr is not None
        if stderr_line_hook is None:
            drainer = threading.Thread(target=_drain_text_stream, args=(proc.stderr, err_lines), daemon=True)
        else:
            drainer = threading.Thread(
                target=_drain_stderr_stream,
                args=(proc.stderr, err_lines, stderr_line_hook),
                daemon=True,
            )
        drainer.start()
        state: dict[str, str] = {}
        try:
            if use_tcp_progress and listen_sock is not None:
                assert listen_sock is not None  # narrow for type checkers
                listen_sock.settimeout(0.25)
                conn: socket.socket | None = None
                try:
                    while conn is None:
                        if cancel_check and cancel_check():
                            proc.kill()
                            try:
                                proc.wait(timeout=30)
                            except subprocess.TimeoutExpired:
                                proc.terminate()
                            raise InterruptedError("ffmpeg cancelled")
                        try:
                            conn, _ = listen_sock.accept()
                            export_debug_log("ffmpeg.run.tcp_connected")
                        except (socket.timeout, TimeoutError):
                            if proc.poll() is not None:
                                proc.wait()
                                export_debug_log(
                                    "ffmpeg.run.exit_before_connect",
                                    returncode=proc.returncode,
                                )
                                return proc.returncode, "".join(err_lines)
                finally:
                    try:
                        listen_sock.close()
                    except OSError:
                        pass
                    listen_sock = None

                assert conn is not None
                conn.settimeout(0.25)
                pending_text = ""
                try:
                    while True:
                        if cancel_check and cancel_check():
                            proc.kill()
                            try:
                                proc.wait(timeout=30)
                            except subprocess.TimeoutExpired:
                                proc.terminate()
                            raise InterruptedError("ffmpeg cancelled")
                        try:
                            chunk = conn.recv(65536)
                        except (socket.timeout, TimeoutError):
                            if proc.poll() is not None:
                                break
                            continue
                        except OSError:
                            break
                        if chunk:
                            pending_text += chunk.decode("utf-8", errors="replace")
                            pending_text, _ = _drain_progress_text_buffer(
                                pending_text,
                                state,
                                duration_cap=duration_cap,
                                on_progress=on_progress,
                            )
                        else:
                            # Progress connection closed (normal); drain process below.
                            break
                    proc.wait()
                    export_debug_log("ffmpeg.run.wait_done", returncode=proc.returncode)
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass
                if pending_text.strip():
                    _dispatch_ffmpeg_progress_line(
                        pending_text,
                        state,
                        duration_cap=duration_cap,
                        on_progress=on_progress,
                    )
            else:
                assert proc.stdout is not None
                for raw in _iter_text_records(proc.stdout):
                    if cancel_check and cancel_check():
                        proc.kill()
                        try:
                            proc.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            proc.terminate()
                        raise InterruptedError("ffmpeg cancelled")
                    _dispatch_ffmpeg_progress_line(
                        raw,
                        state,
                        duration_cap=duration_cap,
                        on_progress=on_progress,
                    )
                proc.wait()
                export_debug_log("ffmpeg.run.wait_done", returncode=proc.returncode)
        finally:
            try:
                drainer.join(timeout=60.0)
            except Exception:
                pass
        return proc.returncode, "".join(err_lines)
    finally:
        if listen_sock is not None:
            try:
                listen_sock.close()
            except OSError:
                pass
        export_debug_log("ffmpeg.run.finish")


@dataclass
class VideoProbe:
    duration_sec: float
    fps: float
    width: int
    height: int
    nb_frames: int | None
    sample_aspect_ratio: str | None
    display_aspect_ratio: str | None
    v_index: int


def _parse_fraction(s: str) -> float:
    if not s or s == "0/0":
        return 0.0
    if "/" in s:
        a, b = s.split("/", 1)
        return float(a) / float(b) if float(b) != 0 else 0.0
    return float(s)


def probe_video(path: str | Path, ffprobe: str | None = None) -> VideoProbe:
    """Parse primary video stream metadata."""
    _, default_probe = find_ffmpeg()
    ffprobe = ffprobe or default_probe
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration,"
        "sample_aspect_ratio,display_aspect_ratio,index",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_FFPROBE_META_TIMEOUT_SEC,
            **_subprocess_platform_flags(),
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffprobe timeout after {_FFPROBE_META_TIMEOUT_SEC}s: {path}") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"ffprobe failed: {err}")
    data: dict[str, Any] = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise ValueError("No video stream found")
    st = streams[0]
    fmt = data.get("format") or {}
    dur_s = float(fmt.get("duration") or st.get("duration") or 0.0)
    r_fps = _parse_fraction(str(st.get("r_frame_rate") or "0/0"))
    a_fps = _parse_fraction(str(st.get("avg_frame_rate") or "0/0"))
    fps = r_fps if r_fps > 0 else a_fps
    if fps <= 0:
        fps = 30.0
    nb = st.get("nb_frames")
    nb_frames: int | None = None
    if nb is not None and str(nb).isdigit():
        nb_frames = int(nb)
    elif dur_s > 0 and fps > 0:
        nb_frames = max(1, int(round(dur_s * fps)))
    return VideoProbe(
        duration_sec=dur_s,
        fps=float(fps),
        width=int(st.get("width") or 0),
        height=int(st.get("height") or 0),
        nb_frames=nb_frames,
        sample_aspect_ratio=st.get("sample_aspect_ratio"),
        display_aspect_ratio=st.get("display_aspect_ratio"),
        v_index=int(st.get("index", 0)),
    )


def _probe_stream_int_field(
    path: str | Path,
    ffprobe: str,
    *,
    count_mode: str,
    field: str,
    timeout: float,
) -> int:
    """Run ffprobe with ``-count_packets`` or ``-count_frames`` and read one integer stream field."""
    if count_mode not in {"packets", "frames"}:
        raise ValueError(count_mode)
    flag = "-count_packets" if count_mode == "packets" else "-count_frames"
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        flag,
        "-show_entries",
        f"stream={field}",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            **_subprocess_platform_flags(),
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffprobe {count_mode} count timeout after {timeout}s: {path}") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"ffprobe failed: {err}")
    try:
        data: dict[str, Any] = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ffprobe invalid json for {path}") from e
    streams = data.get("streams") or []
    if not streams:
        raise ValueError("no video stream")
    raw = streams[0].get(field)
    if raw is None:
        raise ValueError(f"missing {field}")
    s = str(raw).strip()
    if not s or s.upper() == "N/A":
        raise ValueError(f"invalid {field}: {raw!r}")
    try:
        n = int(s)
    except ValueError as e:
        raise ValueError(f"non-integer {field}: {raw!r}") from e
    if n < 0:
        raise ValueError(f"negative {field}: {n}")
    return n


def probe_video_nb_read_packets(
    path: str | Path,
    ffprobe: str | None = None,
    *,
    timeout: float = _FFPROBE_MUX_COUNT_TIMEOUT_SEC,
) -> int:
    """Return video stream packet count (``-count_packets``, matches frame count for typical CFR exports)."""
    _, default_probe = find_ffmpeg()
    ffprobe = ffprobe or default_probe
    return _probe_stream_int_field(path, ffprobe, count_mode="packets", field="nb_read_packets", timeout=timeout)


def probe_video_nb_read_frames(
    path: str | Path,
    ffprobe: str | None = None,
    *,
    timeout: float = _FFPROBE_MUX_COUNT_TIMEOUT_SEC,
) -> int:
    """Return decoded frame count (``-count_frames``); slower fallback when packet count is unavailable."""
    _, default_probe = find_ffmpeg()
    ffprobe = ffprobe or default_probe
    return _probe_stream_int_field(path, ffprobe, count_mode="frames", field="nb_read_frames", timeout=timeout)


def probe_output_video_frame_count(path: str | Path, ffprobe: str | None = None) -> int:
    """Best-effort output frame count: packet scan first, then ``nb_read_frames``."""
    try:
        return probe_video_nb_read_packets(path, ffprobe)
    except Exception:  # noqa: BLE001
        logger.debug("nb_read_packets probe failed; trying nb_read_frames", exc_info=True)
    return probe_video_nb_read_frames(path, ffprobe)


def probe_video_bitrate_kbps(path: str | Path, ffprobe: str | None = None) -> int | None:
    """Return container/video bitrate in kbps if available."""
    _, default_probe = find_ffmpeg()
    ffprobe = ffprobe or default_probe
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=bit_rate",
        "-show_entries",
        "format=bit_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_FFPROBE_BITRATE_TIMEOUT_SEC,
            **_subprocess_platform_flags(),
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    cand_bits: int | None = None
    streams = data.get("streams") or []
    if streams:
        raw = streams[0].get("bit_rate")
        if raw is not None:
            try:
                v = int(str(raw))
                if v > 0:
                    cand_bits = v
            except ValueError:
                cand_bits = None
    if cand_bits is None:
        fmt = data.get("format") or {}
        raw = fmt.get("bit_rate")
        if raw is not None:
            try:
                v = int(str(raw))
                if v > 0:
                    cand_bits = v
            except ValueError:
                cand_bits = None
    if cand_bits is None:
        return None
    return max(300, min(200000, int(round(cand_bits / 1000.0))))


def probe_has_audio_status(path: str | Path, ffprobe: str | None = None) -> bool | None:
    """Return audio-stream presence: ``True``/``False`` or ``None`` when probe is inconclusive."""
    _, default_probe = find_ffmpeg()
    ffprobe = ffprobe or default_probe
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=index",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_FFPROBE_AUDIO_TIMEOUT_SEC,
            **_subprocess_platform_flags(),
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return bool(data.get("streams"))


def probe_has_audio(path: str | Path, ffprobe: str | None = None, *, unknown_as: bool = True) -> bool:
    """Return True/False for audio presence.

    When ffprobe is slow/unavailable and result is inconclusive, returns ``unknown_as``.
    Default is True to avoid false-negative "no audio" decisions on slow/network storage.
    """
    status = probe_has_audio_status(path, ffprobe)
    if status is None:
        return bool(unknown_as)
    return status


def list_keyframe_times(
    path: str | Path,
    ffprobe: str | None = None,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[float]:
    """Keyframe timestamps in seconds (video stream 0)."""
    _, default_probe = find_ffmpeg()
    ffprobe = ffprobe or default_probe
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-skip_frame",
        "nokey",
        "-show_entries",
        "frame=pkt_pts_time,best_effort_timestamp_time",
        "-of",
        "csv=p=0",
        str(path),
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_subprocess_platform_flags(),
    )
    times: list[float] = []
    assert proc.stdout is not None
    while True:
        if cancel_check and cancel_check():
            proc.kill()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.terminate()
            return []
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        t = None
        for p in parts:
            p = p.strip()
            if p and p != "N/A":
                try:
                    t = float(p)
                    break
                except ValueError:
                    continue
        if t is not None:
            times.append(t)
    proc.wait(timeout=600)
    err = proc.stderr.read() if proc.stderr else ""
    if proc.returncode != 0:
        logger.warning("ffprobe keyframes failed: %s", err)
        return []
    return sorted(set(times))


def extract_frame_png(
    path: str | Path,
    time_sec: float,
    ffmpeg: str | None = None,
    max_side: int = 720,
) -> bytes:
    """Decode one frame at timestamp (fast input seek before -i)."""
    ffmpeg = ffmpeg or find_ffmpeg()[0]
    vf = f"scale='min({max_side},iw)':-2,setsar=1"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{time_sec:.6f}",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-vf",
        vf,
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, **_subprocess_platform_flags())
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace") or "extract_frame failed")
    return proc.stdout


def build_proxy_mp4(
    src: str | Path,
    dst: str | Path,
    ffmpeg: str | None = None,
    max_side: int = 960,
    on_progress: Any | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    """Transcode a lower-res preview MP4 (H.264 + AAC)."""
    ffmpeg = ffmpeg or find_ffmpeg()[0]
    codec = resolve_video_codec(ffmpeg, "auto")
    if codec != "libx264":
        logger.info("Proxy encoder auto-selected: %s", codec)
    # Keep source frame rate for time alignment with logical frame indices
    vf = f"scale='min({max_side},iw)':-2,setsar=1"
    # Keep optional audio to preserve audible preview;
    # ``0:a:0?`` safely skips when source has no audio stream.
    tail = [
        "-y",
        "-fflags",
        "+genpts",
        "-i",
        str(src),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        vf,
        *_video_codec_args(codec, bitrate_kbps=2800, preset="veryfast"),
        "-pix_fmt",
        "yuv420p",
        "-af",
        "aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,apad",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(dst),
    ]
    cmd_quiet = [ffmpeg, "-hide_banner", *tail]
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-progress", "pipe:1", *tail]
    if on_progress is None and cancel_check is None:
        proc = subprocess.run(
            cmd_quiet,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_subprocess_platform_flags(),
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or "proxy encode failed")
        return

    def _emit_proxy(tick: FfmpegEncodeProgress) -> None:
        if on_progress is not None:
            on_progress(max(0.0, tick.out_time_sec))

    try:
        rc, err = _run_ffmpeg_pipe_progress(
            cmd,
            duration_cap=0.0,
            on_progress=_emit_proxy,
            cancel_check=cancel_check,
            stderr_line_hook=None,
        )
    except InterruptedError:
        raise
    if rc != 0:
        raise RuntimeError(err or "proxy encode failed")


_FPS_FILTER_HEAD_RE = re.compile(r"^\s*fps\s*=\s*([0-9.]+)([^,]*)")


def _normalize_fps_filter_strict(vf: str, target_fps: int) -> str:
    """Ensure the ``fps=...`` segment uses ``round=down`` and ``start_time=0``.

    Used when the caller requests a strict output frame count: predictable rounding is the only
    way our UI prediction (``floor(Δt * fps_out)`` at output rate) and FFmpeg's frame production
    stay in lock-step with ``round=down``.

    Behavior:
    - If ``vf`` starts with ``fps=<n>[options]``, replace the first ``fps`` segment with
      ``fps={target_fps}:round=down:start_time=0`` (existing options are dropped intentionally).
    - Otherwise prepend ``fps={target_fps}:round=down:start_time=0,setpts=PTS-STARTPTS``.
    """
    new_head = f"fps={target_fps}:round=down:start_time=0"
    m = _FPS_FILTER_HEAD_RE.match(vf)
    if m:
        return new_head + vf[m.end():]
    if not vf.strip():
        return f"{new_head},setpts=PTS-STARTPTS"
    return f"{new_head},setpts=PTS-STARTPTS,{vf}"


def export_clip_mp4(
    src: str | Path,
    start_sec: float,
    duration_sec: float,
    out_path: str | Path,
    ffmpeg: str | None = None,
    video_codec: str = "auto",
    audio_codec: str = "aac",
    output_fps: int = 24,
    video_bitrate_kbps: int = 8000,
    video_rate_mode: str = "bitrate",
    video_quality: int = 23,
    source_has_audio: bool | None = None,
    strong_av_sync: bool = False,
    video_filter: str | None = None,
    n_target_frames: int | None = None,
    *,
    verify_strict_output_frames: bool = True,
    on_progress: Callable[[FfmpegEncodeProgress], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    stderr_line_hook: Callable[[str], None] | None = None,
) -> None:
    """Export segment [start_sec, start_sec+duration).

    Always uses fast input seek: ``-ss`` before ``-i``. When transcoding, this is accurate
    and ensures real-time progress updates are emitted correctly.

    When ``n_target_frames`` is given (strict-frames mode):
    - ``-frames:v N`` forces FFmpeg to output exactly ``N`` video frames.
    - ``-fps_mode cfr`` keeps output pacing constant.
    - The ``fps`` filter is rewritten to ``fps=N:round=down:start_time=0`` so UI prediction and
      FFmpeg agree on rounding.
    - ``-t`` must be long enough for ``N/fps_out`` seconds of decoded media; otherwise
      :func:`export_clip_mp4` raises ``ValueError`` (the align resolver is responsible for
      meeting this bound).
    - Audio is trimmed and padded to exactly ``N/fps_out`` seconds (``atrim`` + ``apad``) so video
      and audio have identical durations regardless of source A/V drift.
    - ``strong_av_sync`` is ignored in this mode (the strict pipeline already pins both streams).
    - The encoded frame count is verified against ``N`` from the progress stream; mismatch raises
      ``RuntimeError`` and the partial output is removed.
    - If ``verify_strict_output_frames`` is true (default), a second check runs via ffprobe
      (``nb_read_packets``, then ``nb_read_frames``) so encoder/progress quirks cannot leave a
      wrong-length file on disk.
    """
    ffmpeg_exec, ffprobe_exec = find_ffmpeg()
    ffmpeg = ffmpeg or ffmpeg_exec
    picked_codec = resolve_video_codec(ffmpeg, video_codec)
    if picked_codec != video_codec:
        logger.warning("Export encoder fallback: %s unavailable, using %s", video_codec, picked_codec)
    output_fps = max(1, min(240, int(output_fps)))
    video_bitrate_kbps = max(300, min(200000, int(video_bitrate_kbps)))
    video_quality = max(0, min(51, int(video_quality)))
    if source_has_audio is None:
        try:
            source_has_audio = probe_has_audio(src)
        except Exception:  # noqa: BLE001
            source_has_audio = True
    duration = max(0.0, duration_sec)
    if duration <= 0:
        raise ValueError("Invalid segment duration")

    strict_frames = n_target_frames is not None and int(n_target_frames) > 0
    n_target = int(n_target_frames) if strict_frames else 0
    target_audio_seconds = (n_target / float(output_fps)) if strict_frames else 0.0
    if strict_frames:
        min_source_seconds = n_target / float(output_fps) - 1e-9
        if duration + 1e-9 < min_source_seconds:
            raise ValueError(
                f"export segment duration {duration:.6f}s is shorter than "
                f"{min_source_seconds:.6f}s required for {n_target} frames at {output_fps} fps"
            )

    out_p = Path(out_path)
    # Default chain: CFR + reset PTS + PAR 1:1.
    vf = video_filter or f"fps={output_fps},setpts=PTS-STARTPTS,scale='round(iw*sar)':ih,setsar=1"
    if strict_frames:
        vf = _normalize_fps_filter_strict(vf, output_fps)
    if strong_av_sync:
        tail = [
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(src),
            "-ss",
            f"{start_sec:.6f}",
            "-t",
            f"{duration:.6f}",
        ]
    else:
        tail = [
            "-y",
            "-fflags",
            "+genpts",
            "-ss",
            f"{start_sec:.6f}",
            "-i",
            str(src),
            "-t",
            f"{duration:.6f}",
        ]
    tail.extend(
        [
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        vf,
        *video_codec_args_with_rate_control(
            picked_codec,
            rate_mode=video_rate_mode,
            bitrate_kbps=video_bitrate_kbps,
            quality=video_quality,
            preset="medium",
        ),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        ]
    )
    if strict_frames:
        tail.extend(["-frames:v", str(n_target)])
    if source_has_audio:
        if strict_frames:
            # Pin audio to exactly N/fps_out seconds: ``aresample`` first to settle source
            # timing, then ``asetpts`` to zero-base, then ``atrim`` cuts overflow, then ``apad``
            # extends silence if the source is shorter than the target window.
            af = (
                f"aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,"
                f"atrim=0:{target_audio_seconds:.6f},"
                f"apad=whole_dur={target_audio_seconds:.6f}"
            )
        else:
            # Rebuild audio timestamps and force async resample to reduce A/V drift.
            af = "aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,apad"
            if strong_av_sync:
                # Experimental stronger correction: allow more aggressive async compensation.
                af = "aresample=async=1000:min_hard_comp=0.100:first_pts=0,asetpts=PTS-STARTPTS,apad"
        tail.extend(
            [
                "-af",
                af,
                "-c:a",
                audio_codec,
            ]
        )
    if strict_frames:
        # Strict mode always pins to CFR; ignore ``strong_av_sync`` here (logged once for
        # awareness — the strict pipeline already provides stronger guarantees).
        if strong_av_sync:
            logger.info(
                "export_clip_mp4: strong_av_sync ignored in strict-frames mode (%s frames)",
                n_target,
            )
        tail.extend(["-fps_mode", "cfr"])
    elif strong_av_sync:
        # Prefer CFR output pacing when user enables strong correction.
        tail.extend(["-fps_mode", "cfr"])
    tail.append(str(out_p))
    cmd_quiet = [ffmpeg, "-hide_banner", *tail]
    # Strict-frames mode uses -progress for a cheap frame= check, then optional ffprobe demux.
    use_progress_pipe = on_progress is not None or cancel_check is not None or strict_frames
    if stderr_line_hook is not None:
        cmd = [ffmpeg, "-hide_banner", "-stats_period", "0.2", "-progress", "pipe:1", *tail]
    elif use_progress_pipe:
        cmd = [ffmpeg, "-hide_banner", "-nostats", "-progress", "pipe:1", *tail]
    else:
        cmd = cmd_quiet
    if not use_progress_pipe:
        proc = subprocess.run(cmd_quiet, capture_output=True, **_subprocess_platform_flags())
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(err or "export failed")
        return

    # Track the largest ``frame=`` value seen from the progress stream so we can verify the
    # encoded frame count matches ``n_target_frames`` once ffmpeg exits.
    max_seen_frame = [0]
    user_on_progress = on_progress

    def tracking_progress(tick: FfmpegEncodeProgress) -> None:
        if tick.out_frame is not None and tick.out_frame > max_seen_frame[0]:
            max_seen_frame[0] = int(tick.out_frame)
        if user_on_progress is not None:
            user_on_progress(tick)

    progress_cb: Callable[[FfmpegEncodeProgress], None] | None
    if strict_frames or user_on_progress is not None:
        progress_cb = tracking_progress
    else:
        progress_cb = None

    try:
        rc, err = _run_ffmpeg_pipe_progress(
            cmd,
            duration_cap=duration,
            on_progress=progress_cb,
            cancel_check=cancel_check,
            stderr_line_hook=stderr_line_hook,
        )
        if rc != 0:
            raise RuntimeError(err or "export failed")
        if strict_frames and max_seen_frame[0] != n_target:
            try:
                if out_p.is_file():
                    out_p.unlink()
            except OSError:
                pass
            raise RuntimeError(
                f"strict-frames mismatch: requested {n_target} frames, ffmpeg produced "
                f"{max_seen_frame[0]} (likely source material too short or filter rounding "
                f"changed)"
            )
        if strict_frames and verify_strict_output_frames and out_p.is_file():
            try:
                probed = probe_output_video_frame_count(out_p, ffprobe_exec)
            except Exception as e:  # noqa: BLE001
                try:
                    if out_p.is_file():
                        out_p.unlink()
                except OSError:
                    pass
                raise RuntimeError(
                    f"strict-frames ffprobe verify failed for {out_p}: {e}"
                ) from e
            if probed != n_target:
                try:
                    if out_p.is_file():
                        out_p.unlink()
                except OSError:
                    pass
                raise RuntimeError(
                    f"strict-frames ffprobe mismatch: expected {n_target} video frames, "
                    f"file has {probed} (packet/frame count)"
                )
    except InterruptedError:
        try:
            if out_p.is_file():
                out_p.unlink()
        except OSError:
            pass
        raise


def run_command(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, **(_subprocess_platform_flags() | kwargs))


def temp_png_path() -> str:
    fd, p = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    return p
