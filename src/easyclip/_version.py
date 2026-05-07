import os
import re
import subprocess
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
            return "0.0.0"
        raw = proc.stdout.strip()
    except Exception:
        return "0.0.0"
    return _to_pep440(raw)


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
