from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

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
