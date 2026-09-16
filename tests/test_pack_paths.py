from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.schemas.request import DeletePackRequest
from app.services import pack_deletion, pack_listing, pack_metadata
from app.services.pack_paths import (
    UnsafePathError,
    allocate_logical_id,
    assert_resolved_under_root,
    audio_object_relative_path,
    asset_base_url,
    doc_object_relative_path,
    generate_audio_version_id,
    generate_file_version_id,
    generate_version_id,
    pack_root_prefix,
    resolve_asset_url,
    storage_base_prefix,
    validate_relative_path,
    validate_safe_token,
)


JST = timezone(timedelta(hours=9))
FIXED_NOW = datetime(2026, 6, 13, 12, 34, 56, tzinfo=JST)
SAFE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def test_generated_ids_use_safe_characters() -> None:
    version_id = generate_version_id(FIXED_NOW)
    file_version_id = generate_file_version_id("document", "doc_01", FIXED_NOW)
    audio_version_id = generate_audio_version_id("doc_01__doc-1", FIXED_NOW)

    assert version_id == "v20260613_123456"
    assert SAFE_RE.fullmatch(version_id)
    assert SAFE_RE.fullmatch(file_version_id)
    assert SAFE_RE.fullmatch(audio_version_id)
    assert file_version_id.startswith("fv_20260613_123456_document_doc_01_")
    assert audio_version_id.startswith("av_20260613_123456_doc_01__doc-1_")


@pytest.mark.parametrize(
    "value",
    [
        "../secret",
        "/absolute",
        "http://example.com/x",
        "https://example.com/x",
        "%2e%2e/secret",
        "bad\\slash",
        "",
        "bad\ncontrol",
        "日本語",
    ],
)
def test_unsafe_tokens_are_rejected(value: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_safe_token(value)


@pytest.mark.parametrize(
    "value",
    [
        "../secret.json",
        "/objects/doc/file.json",
        "http://example.com/objects/doc/file.json",
        "https://example.com/objects/doc/file.json",
        "objects/%2e%2e/file.json",
        "objects\\doc\\file.json",
        "",
        "objects/doc/bad\nfile.json",
    ],
)
def test_unsafe_relative_paths_are_rejected(value: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_relative_path(value)


def test_paths_and_urls_are_composed_from_safe_parts() -> None:
    root_prefix = pack_root_prefix("creator_default", "cnt_abc123")
    asset_base = asset_base_url("https://cdn.example.com", "creator_default", "cnt_abc123")

    assert root_prefix == "sokqa/creators/creator_default/packs/cnt_abc123"
    assert asset_base == "https://cdn.example.com/sokqa/creators/creator_default/packs/cnt_abc123"
    assert doc_object_relative_path("fv_1") == "objects/doc/fv_1.json"
    assert audio_object_relative_path("av_1") == "objects/audio/av_1.mp3"
    assert resolve_asset_url(asset_base, "objects/audio/av_1.mp3") == (
        "https://cdn.example.com/sokqa/creators/creator_default/packs/cnt_abc123/objects/audio/av_1.mp3"
    )


def test_assert_resolved_under_root_rejects_escape(tmp_path) -> None:
    target = assert_resolved_under_root(tmp_path, "objects/doc/file.json")
    assert target == (tmp_path / "objects" / "doc" / "file.json").resolve()

    with pytest.raises(UnsafePathError):
        assert_resolved_under_root(tmp_path, "../escape.json")


def test_allocate_logical_id_uses_suffixes_and_rejects_unsafe_values() -> None:
    existing = {"doc_01", "doc_01_2"}

    assert allocate_logical_id("doc_01", existing) == "doc_01_3"
    assert allocate_logical_id("doc_02", existing) == "doc_02"

    with pytest.raises(UnsafePathError):
        allocate_logical_id("../doc_03", existing)


@pytest.mark.parametrize("backend", ["local", "gcs", "r2"])
@pytest.mark.parametrize(
    ("configured_prefix", "expected_base"),
    [
        ("tenant/media/published", "tenant/media/published"),
        ("/tenant/media/published/", "tenant/media/published"),
        ("", "sokqa"),
        ("/", "sokqa"),
        ("sokqa/packs", "sokqa"),
        ("/sokqa/packs/", "sokqa"),
    ],
)
def test_backend_prefix_is_shared_by_paths_metadata_listing_deletion_and_urls(
    monkeypatch, backend, configured_prefix, expected_base
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", backend)
    # The unused backend's prefix must not affect any consumer.
    monkeypatch.setattr(settings, "gcs_prefix", "unused/gcs" if backend == "r2" else configured_prefix)
    monkeypatch.setattr(settings, "r2_prefix", configured_prefix if backend == "r2" else "unused/r2")
    monkeypatch.setattr(settings, "public_base_url", "https://cdn.example.com/assets")
    expected_root = f"{expected_base}/creators/creator_demo/packs/cnt_demo"
    version_id = generate_version_id(FIXED_NOW)
    version_prefix = f"{expected_root}/versions/{version_id}"
    manifest_url = f"{settings.public_base_url}/{version_prefix}/manifest.json"

    assert storage_base_prefix() == expected_base
    assert pack_deletion._storage_base_prefix() == expected_base
    assert pack_listing._storage_base_prefix() == expected_base
    assert pack_root_prefix("creator_demo", "cnt_demo") == expected_root
    assert pack_metadata.pack_storage_prefix("creator_demo", "cnt_demo", version_id) == version_prefix
    assert pack_metadata.build_pack_version_metadata(
        "creator_demo", "cnt_demo", now=FIXED_NOW
    ).storage_prefix == version_prefix
    assert pack_listing._identity_from_pack_prefix(expected_root) == ("creator_demo", "cnt_demo")
    assert pack_listing._identity_from_pack_prefix("unused/creators/creator_demo/packs/cnt_demo") is None
    assert asset_base_url(settings.public_base_url, "creator_demo", "cnt_demo") == (
        f"{settings.public_base_url}/{expected_root}"
    )
    for request in (
        DeletePackRequest(creatorId="creator_demo", contentId="cnt_demo"),
        DeletePackRequest(storagePrefix=expected_root),
        DeletePackRequest(manifestUrl=manifest_url),
    ):
        assert pack_deletion.resolve_delete_storage_prefix(request) == expected_root


@pytest.mark.parametrize("backend", ["local", "gcs", "r2"])
@pytest.mark.parametrize("prefix", ["../outside", "tenant/%2e%2e/outside", "tenant//media", "bad\\prefix"])
def test_selected_storage_prefix_is_validated(monkeypatch, backend, prefix) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", backend)
    monkeypatch.setattr(settings, "r2_prefix" if backend == "r2" else "gcs_prefix", prefix)

    for helper in (storage_base_prefix, pack_deletion._storage_base_prefix, pack_listing._storage_base_prefix):
        with pytest.raises(UnsafePathError):
            helper()
    with pytest.raises(UnsafePathError):
        pack_metadata.pack_storage_prefix("creator_demo", "cnt_demo", "v1")
