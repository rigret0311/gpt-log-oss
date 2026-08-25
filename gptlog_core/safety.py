from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

from .parser import MAX_JSON_BYTES


_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


class LocalPathError(ValueError):
    """Raised when a path could use a network or non-local transport."""


def sanitize_diagnostic(message: str, paths: Mapping[str, str | Path | None]) -> str:
    """Replace caller-provided machine paths with stable logical placeholders."""
    sanitized = message
    for label, raw in paths.items():
        if raw is None:
            continue
        text = str(raw)
        if not text:
            continue
        path = Path(text).expanduser()
        name = path.name
        replacement = f"<{label}>/{name}" if name and (path.suffix or not path.exists() or path.is_file()) else f"<{label}>"
        candidates = {text, str(path)}
        try:
            candidates.add(str(path.resolve(strict=False)))
        except OSError:
            pass
        for candidate in sorted(candidates, key=len, reverse=True):
            if candidate:
                sanitized = sanitized.replace(candidate, replacement)

    sanitized = sanitized.replace(str(Path.home()), "<HOME>")
    sanitized = re.sub(r"(?i)[A-Z]:\\Users\\[^\\\s:]+", "<HOME>", sanitized)
    sanitized = re.sub(r"/Users/[^/\s:]+", "<HOME>", sanitized)
    return sanitized


def local_path(raw: str | Path, *, must_exist: bool = False) -> Path:
    text = str(raw)
    if not text.strip():
        raise LocalPathError("path must not be empty")
    if _URI_RE.match(text) or text.lower().startswith("file:"):
        raise LocalPathError("URL and URI inputs are not supported; use a local path")
    if text.startswith("\\\\") or text.startswith("//"):
        raise LocalPathError("UNC/network-share paths are not supported")

    path = Path(text).expanduser()
    if must_exist and not path.exists():
        raise FileNotFoundError(f"local path does not exist: {path}")
    if path.exists() and path.is_symlink():
        raise LocalPathError(f"symbolic-link inputs are rejected: {path}")
    return path.resolve()


def _looks_like_conversation_export(path: Path) -> bool:
    """Keep malformed candidates visible, but reject valid sidecar-only JSON."""
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return True
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return True

    def is_conversation(value: object) -> bool:
        return isinstance(value, dict) and (
            isinstance(value.get("mapping"), dict) or isinstance(value.get("messages"), list)
        )

    if is_conversation(payload):
        return True
    if isinstance(payload, list):
        return any(is_conversation(item) for item in payload)
    if isinstance(payload, dict):
        for key in ("conversations", "items", "data", "chats", "threads"):
            candidate = payload.get(key)
            if isinstance(candidate, list) and any(is_conversation(item) for item in candidate):
                return True
    return False


def discover_json_exports(raw: str | Path) -> list[Path]:
    path = local_path(raw, must_exist=True)
    if path.is_file():
        if path.suffix.lower() != ".json":
            raise LocalPathError(f"input must be JSON: {path.name}")
        return [path]
    if not path.is_dir():
        raise LocalPathError(f"input is not a file or directory: {path}")

    candidates = sorted(
        candidate
        for candidate in path.iterdir()
        if candidate.is_file()
        and not candidate.is_symlink()
        and candidate.suffix.lower() == ".json"
    )
    split = [candidate for candidate in candidates if candidate.name.startswith("conversations-")]
    if split:
        return split
    usable = [candidate for candidate in candidates if _looks_like_conversation_export(candidate)]
    if not usable:
        raise FileNotFoundError(f"no JSON export files found directly under: {path}")
    return usable
