from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from app.config import get_settings

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_LAST_TIMESTAMP: datetime | None = None


class UnsafePathError(ValueError):
    pass


def _timestamp_now() -> datetime:
    global _LAST_TIMESTAMP
    current = datetime.now(timezone(timedelta(hours=9))).replace(microsecond=0)
    if _LAST_TIMESTAMP is not None and current <= _LAST_TIMESTAMP:
        current = _LAST_TIMESTAMP + timedelta(seconds=1)
    _LAST_TIMESTAMP = current
    return current


def generate_version_id(now: datetime | None = None) -> str:
    timestamp = now or _timestamp_now()
    return f"v{timestamp.strftime('%Y%m%d_%H%M%S')}"


def generate_build_id(version_id: str) -> str:
    validate_safe_token(version_id)
    return f"build_{version_id.removeprefix('v')}"


def generate_file_version_id(kind: str, logical_id: str, now: datetime | None = None) -> str:
    kind_token = validate_safe_token(kind)
    logical_token = validate_safe_token(logical_id)
    return f"fv_{generate_version_id(now).removeprefix('v')}_{kind_token}_{logical_token}_{uuid4().hex[:8]}"


def generate_audio_version_id(stem: str, now: datetime | None = None) -> str:
    stem_token = validate_safe_token(stem)
    return f"av_{generate_version_id(now).removeprefix('v')}_{stem_token}_{uuid4().hex[:8]}"


def storage_base_prefix() -> str:
    base = get_settings().gcs_prefix.strip("/") or "sokqa"
    if base == "sokqa/packs":
        base = "sokqa"
    validate_relative_path(base)
    return base


def pack_root_prefix(creator_id: str, content_id: str, *, base_prefix: str | None = None) -> str:
    base = validate_relative_path(base_prefix or storage_base_prefix())
    return f"{base}/creators/{validate_safe_token(creator_id)}/packs/{validate_safe_token(content_id)}"


def manifest_relative_path(version_id: str) -> str:
    return f"versions/{validate_safe_token(version_id)}/manifest.json"


def doc_object_relative_path(file_version_id: str) -> str:
    return f"objects/doc/{validate_safe_token(file_version_id)}.json"


def quiz_object_relative_path(file_version_id: str) -> str:
    return f"objects/quiz/{validate_safe_token(file_version_id)}.json"


def audio_object_relative_path(audio_version_id: str) -> str:
    return f"objects/audio/{validate_safe_token(audio_version_id)}.mp3"


def asset_base_url(public_base_url: str, creator_id: str, content_id: str, *, base_prefix: str | None = None) -> str:
    base = public_base_url.rstrip("/")
    if not base:
        raise UnsafePathError("public_base_url must not be empty")
    return f"{base}/{pack_root_prefix(creator_id, content_id, base_prefix=base_prefix)}"


def resolve_asset_url(asset_base: str, relative_path: str) -> str:
    return f"{asset_base.rstrip('/')}/{validate_relative_path(relative_path)}"


def validate_safe_token(value: str) -> str:
    if value is None:
        raise UnsafePathError("token must not be None")
    token = str(value)
    decoded = unquote(token)
    lower = decoded.lower()
    if (
        not decoded
        or decoded.startswith("/")
        or "\\" in decoded
        or ".." in decoded
        or lower.startswith("http://")
        or lower.startswith("https://")
        or _CONTROL_RE.search(decoded)
        or not _SAFE_TOKEN_RE.fullmatch(decoded)
    ):
        raise UnsafePathError(f"unsafe token: {value!r}")
    return decoded


def validate_relative_path(value: str) -> str:
    if value is None:
        raise UnsafePathError("relative path must not be None")
    path = str(value)
    decoded = unquote(path)
    lower = decoded.lower()
    if (
        not decoded
        or decoded.startswith("/")
        or "\\" in decoded
        or ".." in decoded
        or lower.startswith("http://")
        or lower.startswith("https://")
        or _CONTROL_RE.search(decoded)
    ):
        raise UnsafePathError(f"unsafe relative path: {value!r}")
    parts = decoded.split("/")
    if any(not part or not _SAFE_TOKEN_RE.fullmatch(part) for part in parts):
        raise UnsafePathError(f"unsafe relative path: {value!r}")
    return decoded


def assert_resolved_under_root(root: Path, relative_path: str) -> Path:
    root_resolved = root.resolve()
    target = (root_resolved / validate_relative_path(relative_path)).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise UnsafePathError(f"path escapes root: {relative_path!r}") from exc
    return target


def allocate_logical_id(suggested: str, existing: set[str]) -> str:
    base = validate_safe_token(suggested)
    if base not in existing:
        return base
    index = 2
    while True:
        candidate = f"{base}_{index}"
        if candidate not in existing:
            return candidate
        index += 1
