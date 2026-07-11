"""In-memory, non-public review storage for blocked generated packs."""
from __future__ import annotations

import copy
import json
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.schemas.sokqa import CoursePlan, GeneratedFile, ValidationResult

logger = logging.getLogger(__name__)
_TTL = timedelta(minutes=45)
_MAX_ENTRIES = 25
_MAX_BYTES = 20 * 1024 * 1024
_lock = threading.RLock()


class TemporaryGenerationError(RuntimeError):
    status_code = 404


class TemporaryGenerationExpired(TemporaryGenerationError):
    status_code = 410


class TemporaryGenerationPromoted(TemporaryGenerationError):
    status_code = 409


@dataclass
class TemporaryGeneration:
    id: str
    creator_id: str
    files: list[GeneratedFile]
    validation: ValidationResult
    plan: CoursePlan
    created_at: datetime
    expires_at: datetime
    generation: int = 1
    promoted: bool = False


_items: dict[str, TemporaryGeneration] = {}


def create_temporary_generation(*, creator_id: str, files: list[GeneratedFile], validation: ValidationResult, plan: CoursePlan) -> TemporaryGeneration:
    now = datetime.now(UTC)
    item = TemporaryGeneration(f"tmp_{secrets.token_urlsafe(24)}", creator_id, copy.deepcopy(files), copy.deepcopy(validation), plan.model_copy(deep=True), now, now + _TTL)
    with _lock:
        _purge_expired_locked(now)
        _evict_for_capacity_locked(item)
        _items[item.id] = item
    _log("created", item)
    return copy.deepcopy(item)


def get_temporary_generation(temporary_id: str, creator_id: str) -> TemporaryGeneration:
    with _lock:
        item = _items.get(temporary_id)
        if item is None or item.creator_id != creator_id:
            raise TemporaryGenerationError("temporary generation was not found; it may have expired or the server restarted")
        if item.expires_at <= datetime.now(UTC):
            del _items[temporary_id]
            raise TemporaryGenerationExpired("temporary generation has expired")
        if item.promoted:
            raise TemporaryGenerationPromoted("temporary generation was already promoted")
        return copy.deepcopy(item)


def update_temporary_generation(temporary_id: str, creator_id: str, *, files: list[GeneratedFile], validation: ValidationResult, expected_generation: int | None = None) -> TemporaryGeneration:
    with _lock:
        get_temporary_generation(temporary_id, creator_id)
        item = _items[temporary_id]
        if expected_generation is not None and item.generation != expected_generation:
            raise TemporaryGenerationError("temporary generation has changed; refresh before applying another fix")
        item.files, item.validation, item.generation = copy.deepcopy(files), copy.deepcopy(validation), item.generation + 1
        _log("updated", item)
        return copy.deepcopy(item)


def mark_promoted(temporary_id: str, creator_id: str) -> None:
    with _lock:
        get_temporary_generation(temporary_id, creator_id)
        _items[temporary_id].promoted = True
        _log("promoted", _items[temporary_id])


def _log(state: str, item: TemporaryGeneration) -> None:
    logger.info("temporary_generation.%s id=%s creator_id=%s state=%s expires_at=%s file_count=%s blocking_errors=%s", state, item.id, item.creator_id, state, item.expires_at.isoformat(), len(item.files), sum(issue.severity == "error" for issue in item.validation.errors))


def _purge_expired_locked(now: datetime) -> None:
    for key, value in list(_items.items()):
        if value.expires_at <= now:
            del _items[key]


def _serialized_size(item: TemporaryGeneration) -> int:
    return len(json.dumps([file.model_dump(mode="json") for file in item.files], ensure_ascii=False))


def _evict_for_capacity_locked(incoming: TemporaryGeneration) -> None:
    while _items and (len(_items) >= _MAX_ENTRIES or sum(_serialized_size(item) for item in _items.values()) + _serialized_size(incoming) > _MAX_BYTES):
        oldest = min(_items.values(), key=lambda item: item.created_at)
        del _items[oldest.id]
