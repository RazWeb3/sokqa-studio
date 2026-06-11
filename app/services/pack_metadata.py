from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.schemas.sokqa import CoursePlan
from app.utils.ids import new_opaque_id, path_token, slugify

_LAST_TIMESTAMP: datetime | None = None


@dataclass(frozen=True)
class PackBuildMetadata:
    creator_id: str
    creator_display_name: str | None
    content_id: str
    slug: str
    version_id: str
    build_id: str
    generated_at: str
    storage_prefix: str


@dataclass(frozen=True)
class PackVersionMetadata:
    version_id: str
    build_id: str
    generated_at: str
    storage_prefix: str


def _timestamp_now() -> datetime:
    global _LAST_TIMESTAMP
    current = datetime.now(timezone(timedelta(hours=9))).replace(microsecond=0)
    if _LAST_TIMESTAMP is not None and current <= _LAST_TIMESTAMP:
        current = _LAST_TIMESTAMP + timedelta(seconds=1)
    _LAST_TIMESTAMP = current
    return current


def _timestamp_ids(now: datetime) -> tuple[str, str]:
    stamp = now.strftime("%Y%m%d_%H%M%S")
    return f"v{stamp}", f"build_{stamp}"


def resolve_creator_id(request_value: str | None = None, plan_value: str | None = None) -> str:
    value = request_value or plan_value or get_settings().default_creator_id or "creator_default"
    return path_token(value, "creator_default")


def resolve_plan_identity(plan: CoursePlan) -> CoursePlan:
    creator_id = resolve_creator_id(plan_value=plan.creatorId)
    slug = path_token(plan.slug or slugify(plan.title or plan.id, "sokqa-pack").replace("_", "-"), plan.id)
    content_id = path_token(plan.contentId or new_opaque_id("cnt"), "cnt_default")
    return plan.model_copy(
        update={
            "creatorId": creator_id,
            "contentId": content_id,
            "slug": slug,
        }
    )


def build_pack_metadata(
    plan: CoursePlan,
    *,
    creator_id: str | None = None,
    creator_display_name: str | None = None,
    content_id: str | None = None,
    slug: str | None = None,
    now: datetime | None = None,
) -> PackBuildMetadata:
    timestamp = now or _timestamp_now()
    version_id, build_id = _timestamp_ids(timestamp)
    resolved_creator_id = resolve_creator_id(creator_id, plan.creatorId)
    resolved_content_id = path_token(content_id or plan.contentId or new_opaque_id("cnt"), "cnt_default")
    resolved_slug = path_token(slug or plan.slug or slugify(plan.title or plan.id, "sokqa-pack").replace("_", "-"), plan.id)
    display_name = creator_display_name if creator_display_name is not None else plan.creatorDisplayName
    storage_prefix = pack_storage_prefix(resolved_creator_id, resolved_content_id, version_id)
    return PackBuildMetadata(
        creator_id=resolved_creator_id,
        creator_display_name=display_name,
        content_id=resolved_content_id,
        slug=resolved_slug,
        version_id=version_id,
        build_id=build_id,
        generated_at=timestamp.isoformat(timespec="seconds"),
        storage_prefix=storage_prefix,
    )


def build_pack_version_metadata(creator_id: str, content_id: str, *, now: datetime | None = None) -> PackVersionMetadata:
    timestamp = now or _timestamp_now()
    version_id, build_id = _timestamp_ids(timestamp)
    return PackVersionMetadata(
        version_id=version_id,
        build_id=build_id,
        generated_at=timestamp.isoformat(timespec="seconds"),
        storage_prefix=pack_storage_prefix(creator_id, content_id, version_id),
    )


def pack_storage_prefix(creator_id: str, content_id: str, version_id: str) -> str:
    base = get_settings().gcs_prefix.strip("/") or "sokqa"
    if base == "sokqa/packs":
        base = "sokqa"
    return f"{base}/creators/{creator_id}/packs/{content_id}/versions/{version_id}"
