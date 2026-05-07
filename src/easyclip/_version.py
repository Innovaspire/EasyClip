import os
import re
import subprocess
import sys
from pathlib import Path


def _git_version() -> str:
    env = os.environ.get("EASYCLIP_VERSION")
    if env:
        return env
    try:
        root = Path(__file__).resolve().parent.parent.parent
        proc = subprocess.run(
            ["git", "describe", "--tags", "--long", "--dirty", "--always"],
            capture_output=True,
            text=True,
            cwd=str(root),
        )
        if proc.returncode != 0:
            return _fallback_version()
        raw = proc.stdout.strip()
    except Exception:
        return _fallback_version()
    return _to_pep440(raw)


def _fallback_version() -> str:
    # Frozen app: version.txt is bundled alongside this module
    if getattr(sys, "frozen", False):
        bundled = Path(sys._MEIPASS) / "easyclip" / "_version.txt"
        if bundled.is_file():
            return bundled.read_text(encoding="utf-8").strip()
    return "0.0.0"


def _to_pep440(raw: str) -> str:
    # Plain hash with no reachable tag: "80dd2cc" or "80dd2cc-dirty"
    if re.match(r"^[0-9a-f]+(-dirty)?$", raw):
        dirty = raw.endswith("-dirty")
        base = raw.replace("-dirty", "")
        v = f"0.0.0+g{base}"
        return f"{v}.dirty" if dirty else v

    if raw.startswith("v"):
        raw = raw[1:]

    dirty = raw.endswith("-dirty")
    if dirty:
        raw = raw[:-6]

    # git describe --long: <tag>-<distance>-g<hash>
    m = re.match(r"^(.+)-(\d+)-g([0-9a-f]+)$", raw)
    if not m:
        return raw if not dirty else f"{raw}.dirty"

    tag, dist, sha = m.groups()
    if dist == "0":
        v = tag
    else:
        v = f"{tag}.post{dist}+g{sha}"
    return f"{v}.dirty" if dirty else v


__version__ = _git_version()
