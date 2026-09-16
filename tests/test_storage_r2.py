import json
from io import BytesIO
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.routes import packs
from app.schemas.sokqa import GeneratedFile
from app.services import storage_client as module
from app.services.pack_listing import _list_latest_items_for_content, _list_v2_items_for_content
from app.services.storage_client import StorageClient


BUCKET = "test-bucket"
BASE = "private/packs"
ROOT = f"{BASE}/creators/creator_a/packs/content_a"


@pytest.fixture
def r2(monkeypatch):
    settings = get_settings()
    for name, value in {
        "storage_backend": "r2",
        "r2_endpoint": "https://r2.invalid",
        "r2_access_key_id": "test-key",
        "r2_secret_access_key": "test-secret",
        "r2_bucket_name": BUCKET,
        "r2_prefix": BASE,
        "gcs_prefix": "old-gcs-root",
        "public_base_url": "http://localhost:8000/generated",
    }.items():
        monkeypatch.setattr(settings, name, value)
    sdk = boto3.client(
        "s3", endpoint_url="https://r2.invalid", region_name="auto",
        aws_access_key_id="test-key", aws_secret_access_key="test-secret",
    )
    monkeypatch.setattr(module, "_cached_r2_client", lambda *args: sdk)
    with Stubber(sdk) as stub:
        yield StorageClient(), stub, sdk
        stub.assert_no_pending_responses()
    sdk.close()


def queue_read(stub, key, payload):
    stream = BytesIO(payload)
    stub.add_response(
        "get_object", {"Body": StreamingBody(stream, len(payload))},
        {"Bucket": BUCKET, "Key": key},
    )
    return stream


def queue_list(stub, prefix, response, *, delimiter=None, token=None):
    params = {"Bucket": BUCKET, "Prefix": prefix}
    if delimiter is not None:
        params["Delimiter"] = delimiter
    if token is not None:
        params["ContinuationToken"] = token
    stub.add_response("list_objects_v2", response, params)


@pytest.mark.parametrize("explicit_prefix", [None, ROOT])
def test_save_files_and_load_json_use_matching_r2_paths(r2, explicit_prefix):
    storage, stub, _ = r2
    prefix = explicit_prefix or f"{BASE}/demo"
    data = {"title": "日本語の教材"}
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    stub.add_response("put_object", {}, {
        "Bucket": BUCKET, "Key": f"{prefix}/doc.json", "Body": payload,
        "ContentType": "application/json; charset=utf-8",
    })
    files = storage.save_files("demo", [GeneratedFile(name="doc.json", kind="document", content=data)], explicit_prefix)
    assert files[0].url == f"http://localhost:8000/generated/{prefix}/doc.json"
    stream = queue_read(stub, f"{prefix}/doc.json", payload)
    assert storage.load_json_file("demo", "doc.json", explicit_prefix) == data
    assert stream.closed


def test_save_bytes_preserves_audio_and_content_type(r2):
    storage, stub, _ = r2
    stub.add_response("put_object", {}, {
        "Bucket": BUCKET, "Key": f"{BASE}/demo/audio/test.mp3",
        "Body": b"mp3", "ContentType": "audio/mpeg",
    })
    assert storage.save_bytes("demo", "/audio/test.mp3/", b"mp3", "audio/mpeg") == (
        f"http://localhost:8000/generated/{BASE}/demo/audio/test.mp3"
    )


def test_save_manifest_and_latest_roundtrip(r2):
    storage, stub, _ = r2
    manifest = {"versionId": "v1", "title": "教材", "items": [{"name": "doc.json"}]}
    payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    for relative, save, read in [
        ("versions/v1/manifest.json", lambda: storage.save_manifest(ROOT, "v1", manifest), lambda: storage.read_manifest(ROOT, "v1")),
        ("latest.json", lambda: storage.save_latest(ROOT, manifest), lambda: storage.read_latest(ROOT)),
    ]:
        stub.add_response("put_object", {}, {
            "Bucket": BUCKET, "Key": f"{ROOT}/{relative}", "Body": payload,
            "ContentType": "application/json; charset=utf-8",
        })
        assert save() == f"http://localhost:8000/generated/{ROOT}/{relative}"
        stream = queue_read(stub, f"{ROOT}/{relative}", payload)
        assert read() == manifest
        assert stream.closed
    assert storage.last_latest_measurement[ROOT]["items"] == 1
    assert storage.last_latest_measurement[ROOT]["bytes"] == len(payload)


def test_missing_latest_returns_none(r2):
    storage, stub, _ = r2
    stub.add_client_error("get_object", "NoSuchKey", http_status_code=404,
                          expected_params={"Bucket": BUCKET, "Key": f"{ROOT}/latest.json"})
    assert storage.read_latest(ROOT) is None


@pytest.mark.parametrize("code,status", [("AccessDenied", 403), ("InternalError", 500), ("NoSuchBucket", 404), ("404", 404)])
def test_latest_does_not_hide_storage_failures(r2, code, status):
    storage, stub, _ = r2
    stub.add_client_error("get_object", code, http_status_code=status,
                          expected_params={"Bucket": BUCKET, "Key": f"{ROOT}/latest.json"})
    with pytest.raises(ClientError) as error:
        storage.read_latest(ROOT)
    assert error.value.response["Error"]["Code"] == code


@pytest.mark.parametrize("operation", ["manifest", "object", "json"])
def test_missing_r2_reads_raise_file_not_found(r2, operation):
    storage, stub, _ = r2
    relative = "versions/v1/manifest.json" if operation == "manifest" else "missing.json"
    stub.add_client_error("get_object", "NoSuchKey", http_status_code=404,
                          expected_params={"Bucket": BUCKET, "Key": f"{ROOT}/{relative}"})
    with pytest.raises(FileNotFoundError):
        if operation == "manifest":
            storage.read_manifest(ROOT, "v1")
        elif operation == "object":
            storage.read_object(ROOT, relative)
        else:
            storage.load_json_file("ignored", relative, ROOT)


def test_missing_pack_file_api_returns_404(r2):
    _, stub, _ = r2
    stub.add_client_error("get_object", "NoSuchKey", http_status_code=404,
                          expected_params={"Bucket": BUCKET, "Key": f"{ROOT}/versions/v1/manifest.json"})
    app = FastAPI()
    app.include_router(packs.router)
    with TestClient(app) as client:
        response = client.get("/packs/file", params={
            "creatorId": "creator_a", "contentId": "content_a", "versionId": "v1", "logicalId": "doc_01",
        })
    assert response.status_code == 404


@pytest.mark.parametrize("source", ["latest", "manifest"])
def test_pack_listing_does_not_hide_r2_authorization_errors(r2, source):
    storage, stub, _ = r2
    if source == "manifest":
        queue_list(stub, f"{ROOT}/versions/", {"IsTruncated": False, "Contents": [
            {"Key": f"{ROOT}/versions/v1/manifest.json"},
        ]})
    relative = "latest.json" if source == "latest" else "versions/v1/manifest.json"
    stub.add_client_error("get_object", "AccessDenied", http_status_code=403,
                          expected_params={"Bucket": BUCKET, "Key": f"{ROOT}/{relative}"})
    with pytest.raises(ClientError):
        if source == "latest":
            _list_latest_items_for_content(storage, "creator_a", "content_a")
        else:
            _list_v2_items_for_content(storage, "creator_a", "content_a", backfill_latest=True)


def test_r2_response_stream_is_closed_on_read_error(r2, monkeypatch):
    storage, _, sdk = r2
    body = Mock()
    body.read.side_effect = OSError("interrupted")
    monkeypatch.setattr(sdk, "get_object", lambda **kwargs: {"Body": body})
    with pytest.raises(OSError):
        storage.read_object(ROOT, "doc.json")
    body.close.assert_called_once()


def test_creator_and_pack_listing_sorts_strings_and_reads_every_page(r2):
    storage, stub, _ = r2
    creators = f"{BASE}/creators/"
    queue_list(stub, creators, {"IsTruncated": True, "NextContinuationToken": "c2", "CommonPrefixes": [
        {"Prefix": f"{creators}creator_c/"}, {"Prefix": f"{creators}creator_a/"},
    ]}, delimiter="/")
    queue_list(stub, creators, {"IsTruncated": False, "CommonPrefixes": [
        {"Prefix": f"{creators}creator_b/"},
    ]}, delimiter="/", token="c2")
    a = f"{creators}creator_a/packs/"
    queue_list(stub, a, {"IsTruncated": True, "NextContinuationToken": "p2", "CommonPrefixes": [
        {"Prefix": f"{a}z/"},
    ]}, delimiter="/")
    queue_list(stub, a, {"IsTruncated": False, "CommonPrefixes": [{"Prefix": f"{a}a/"}]}, delimiter="/", token="p2")
    queue_list(stub, f"{creators}creator_b/packs/", {"IsTruncated": False}, delimiter="/")
    queue_list(stub, f"{creators}creator_c/packs/", {"IsTruncated": False, "CommonPrefixes": [
        {"Prefix": f"{creators}creator_c/packs/p/"},
    ]}, delimiter="/")
    assert storage.list_pack_prefixes_for_creator() == [f"{a}a", f"{a}z", f"{creators}creator_c/packs/p"]


def test_filtered_creator_listing_reads_every_page(r2):
    storage, stub, _ = r2
    prefix = f"{BASE}/creators/creator_a/packs/"
    queue_list(stub, prefix, {"IsTruncated": True, "NextContinuationToken": "next", "CommonPrefixes": [
        {"Prefix": f"{prefix}a/"},
    ]}, delimiter="/")
    queue_list(stub, prefix, {"IsTruncated": False, "CommonPrefixes": [{"Prefix": f"{prefix}b/"}]}, delimiter="/", token="next")
    assert storage.list_pack_prefixes_for_creator("creator_a") == [f"{prefix}a", f"{prefix}b"]


def test_empty_creator_listing(r2):
    storage, stub, _ = r2
    queue_list(stub, f"{BASE}/creators/", {"IsTruncated": False}, delimiter="/")
    assert storage.list_pack_prefixes_for_creator() == []


def test_manifest_listing_is_paginated_and_filtered(r2):
    storage, stub, _ = r2
    prefix = f"{ROOT}/versions/"
    queue_list(stub, prefix, {"IsTruncated": True, "NextContinuationToken": "next", "Contents": [
        {"Key": f"{prefix}v2/manifest.json"}, {"Key": f"{prefix}v2/notes.json"},
    ]})
    queue_list(stub, prefix, {"IsTruncated": False, "Contents": [{"Key": f"{prefix}v1/manifest.json"}]}, token="next")
    assert storage.list_manifests(ROOT) == ["versions/v1/manifest.json", "versions/v2/manifest.json"]


def test_copy_prefix_reads_every_page(r2):
    storage, stub, _ = r2
    for name, token, truncated in [("doc.json", None, True), ("audio.mp3", "next", False)]:
        response = {"IsTruncated": truncated, "Contents": [{"Key": f"source/{name}"}]}
        if truncated:
            response["NextContinuationToken"] = "next"
        queue_list(stub, "source/", response, token=token)
        stub.add_response("copy_object", {}, {
            "CopySource": {"Bucket": BUCKET, "Key": f"source/{name}"},
            "Bucket": BUCKET, "Key": f"destination/{name}",
        })
    assert storage.copy_prefix("source", "destination") == ["destination/doc.json", "destination/audio.mp3"]


@pytest.mark.parametrize("target", ["source", "source/nested"])
def test_copy_rejects_recursive_destination(r2, target):
    storage, _, _ = r2
    with pytest.raises(ValueError):
        storage.copy_prefix("source", target)


@pytest.mark.parametrize("field", ["r2_endpoint", "r2_bucket_name", "r2_access_key_id", "r2_secret_access_key"])
def test_missing_r2_configuration_fails_before_network(r2, monkeypatch, field):
    storage, _, _ = r2
    monkeypatch.setattr(storage.settings, field, "")
    with pytest.raises(ValueError, match=field.upper()):
        storage._r2_client()


def test_r2_client_cache_and_timeout_configuration(monkeypatch):
    module._cached_r2_client_inner.cache_clear()
    factory = Mock()
    monkeypatch.setattr(module.boto3, "client", factory)
    first = module._cached_r2_client("https://r2.invalid", "test-key", "test-secret")
    assert module._cached_r2_client("https://r2.invalid", "test-key", "test-secret") is first
    factory.assert_called_once()
    kwargs = factory.call_args.kwargs
    assert kwargs["region_name"] == "auto"
    assert kwargs["config"].connect_timeout == 8
    assert kwargs["config"].read_timeout == 8
    assert kwargs["config"].retries == {"mode": "standard", "max_attempts": 2}
    assert kwargs["config"].s3 == {"addressing_style": "path"}
    module._cached_r2_client_inner.cache_clear()
