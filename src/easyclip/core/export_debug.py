"""Lightweight opt-in diagnostics for export progress pipeline."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()


def export_debug_enabled() -> bool:
    v = (os.environ.get("EASYCLIP_EXPORT_PROGRESS_DEBUG") or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def export_debug_log_path() -> Path:
    p = (os.environ.get("EASYCLIP_EXPORT_DEBUG_LOG") or "").strip()
    if p:
        return Path(p)
    return Path(tempfile.gettempdir()) / "easyclip_export_progress_debug.log"


def export_debug_log(event: str, **fields: object) -> None:
    if not export_debug_enabled():
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    ms = int((time.time() % 1.0) * 1000)
    parts = [f"{ts}.{ms:03d}", event]
    for k, v in fields.items():
        if v is None:
            continue
        parts.append(f"{k}={v}")
    line = " | ".join(parts) + "\n"
    path = export_debug_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    with _LOCK:
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            return
