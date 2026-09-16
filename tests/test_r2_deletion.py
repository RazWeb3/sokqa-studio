from unittest.mock import Mock, call

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from app.config import get_settings
from app.schemas.request import DeletePackRequest
from app.services import pack_deletion
from app.services.storage_client import StorageClient


BASE = "tenant/media"
PREFIX = f"{BASE}/creators/creator_demo/packs/cnt_demo"
BUCKET = "test-r2-bucket"


@pytest.fixture
def r2_storage(tmp_path, monkeypatch):
    settings = get_settings()
    for name, value in {
        "storage_backend": "r2",
        "r2_prefix": BASE,
        "gcs_prefix": "unused/gcs",
        "r2_endpoint": "https://r2.example.invalid",
        "r2_bucket_name": BUCKET,
        "r2_access_key_id": "test-access-key",
        "r2_secret_access_key": "test-secret-key",
        "local_storage_dir": str(tmp_path / "generated"),
    }.items():
        monkeypatch.setattr(settings, name, value)
    for name in ("_list_local_objects", "_delete_local_objects", "_list_gcs_objects", "_delete_gcs_objects"):
        monkeypatch.setattr(pack_deletion, name, Mock(side_effect=AssertionError("R2 must not fall back")))
    # Exercise the real cached client factory using dummy credentials only.
    with Stubber(StorageClient()._r2_client()) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


def test_r2_deletes_all_pages_and_preserves_same_named_local_files(r2_storage, tmp_path, monkeypatch) -> None:
    keys = [
        f"{PREFIX}/latest.json",
        f"{PREFIX}/objects/audio/av_1.mp3",
        f"{PREFIX}/objects/doc/",
        f"{PREFIX}/objects/doc/fv_1.json",
        f"{PREFIX}/versions/v1/manifest.json",
    ]
    local_file = tmp_path / "generated" / keys[0]
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"local file must survive")
    local_only_file = local_file.parent / "local-only.json"
    local_only_file.write_bytes(b"not an R2 object")
    events = Mock()
    monkeypatch.setattr(pack_deletion, "record_storage_event", events)
    r2_storage.add_response(
        "list_objects_v2",
        {"IsTruncated": True, "NextContinuationToken": "page-2", "Contents": [{"Key": key} for key in keys[2:]]},
        {"Bucket": BUCKET, "Prefix": f"{PREFIX}/"},
    )
    r2_storage.add_response(
        "list_objects_v2",
        {"IsTruncated": False, "Contents": [{"Key": key} for key in reversed(keys[:2])]},
        {"Bucket": BUCKET, "Prefix": f"{PREFIX}/", "ContinuationToken": "page-2"},
    )
    for key in keys:
        r2_storage.add_response("delete_object", {}, {"Bucket": BUCKET, "Key": key})

    response = pack_deletion.delete_pack_version(DeletePackRequest(creatorId="creator_demo", contentId="cnt_demo"))

    assert response.status == "deleted"
    assert response.storagePrefix == PREFIX
    assert response.objectNames == keys
    assert response.objectCount == response.deletedCount == len(keys)
    assert events.call_args_list == [call(f"r2 deleted: {key}") for key in keys]
    assert local_file.read_bytes() == b"local file must survive"
    assert local_only_file.read_bytes() == b"not an R2 object"


def test_r2_empty_listing_does_not_use_local_files(r2_storage, tmp_path) -> None:
    local_file = tmp_path / "generated" / PREFIX / "latest.json"
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"local only")
    r2_storage.add_response(
        "list_objects_v2", {"IsTruncated": False}, {"Bucket": BUCKET, "Prefix": f"{PREFIX}/"}
    )

    with pytest.raises(FileNotFoundError, match="no objects found"):
        pack_deletion.delete_pack_version(DeletePackRequest(storagePrefix=PREFIX))

    assert local_file.read_bytes() == b"local only"
    assert pack_deletion.delete_storage_objects([]) == 0


@pytest.mark.parametrize("failure_stage", ["list_first", "list_later", "delete_first", "delete_later"])
def test_r2_api_errors_propagate_without_reporting_success(r2_storage, monkeypatch, failure_stage) -> None:
    keys = [f"{PREFIX}/latest.json", f"{PREFIX}/objects/doc/fv_1.json"]
    list_params = {"Bucket": BUCKET, "Prefix": f"{PREFIX}/"}
    events = Mock()
    monkeypatch.setattr(pack_deletion, "record_storage_event", events)
    if failure_stage == "list_later":
        r2_storage.add_response(
            "list_objects_v2",
            {"IsTruncated": True, "NextContinuationToken": "page-2", "Contents": [{"Key": keys[0]}]},
            list_params,
        )
        list_params = {**list_params, "ContinuationToken": "page-2"}
    if failure_stage.startswith("list"):
        r2_storage.add_client_error(
            "list_objects_v2", service_error_code="AccessDenied", http_status_code=403, expected_params=list_params
        )
    else:
        r2_storage.add_response(
            "list_objects_v2",
            {"IsTruncated": False, "Contents": [{"Key": key} for key in keys]},
            list_params,
        )
        failure_key = keys[0]
        if failure_stage == "delete_later":
            r2_storage.add_response("delete_object", {}, {"Bucket": BUCKET, "Key": keys[0]})
            failure_key = keys[1]
        r2_storage.add_client_error(
            "delete_object",
            service_error_code="AccessDenied",
            http_status_code=403,
            expected_params={"Bucket": BUCKET, "Key": failure_key},
        )

    with pytest.raises(ClientError) as error:
        pack_deletion.delete_pack_version(DeletePackRequest(storagePrefix=PREFIX))

    assert error.value.response["Error"]["Code"] == "AccessDenied"
    expected_events = [call(f"r2 deleted: {keys[0]}")] if failure_stage == "delete_later" else []
    assert events.call_args_list == expected_events


@pytest.mark.parametrize(
    "prefix",
    [
        BASE,
        f"{BASE}/creators/creator_demo",
        f"{BASE}/creators/creator_demo/packs",
        f"{PREFIX}/versions/v1",
        "unused/gcs/creators/creator_demo/packs/cnt_demo",
        "tenant/media-other/creators/creator_demo/packs/cnt_demo",
        f"{BASE}/creators/../packs/cnt_demo",
        f"{BASE}/creators/%2e%2e/packs/cnt_demo",
    ],
)
def test_r2_listing_rejects_targets_other_than_a_single_pack(r2_storage, prefix) -> None:
    with pytest.raises(ValueError):
        pack_deletion.list_storage_objects(prefix)
    with pytest.raises(ValueError):
        pack_deletion.delete_pack_version(DeletePackRequest(storagePrefix=prefix))


@pytest.mark.parametrize(
    "key",
    [
        "",
        BASE,
        "outside/latest.json",
        "unused/gcs/creators/creator_demo/packs/cnt_demo/latest.json",
        "tenant/media-other/creators/creator_demo/packs/cnt_demo/latest.json",
        f"/{PREFIX}/latest.json",
        f"{BASE}/../outside/latest.json",
        f"{BASE}/%2e%2e/outside/latest.json",
        f"{BASE}\\outside\\latest.json",
        f"{BASE}//latest.json",
    ],
)
def test_r2_validates_all_object_keys_before_any_deletion(r2_storage, key) -> None:
    # No SDK calls are stubbed: even the first valid key must not be deleted.
    with pytest.raises(ValueError):
        pack_deletion.delete_storage_objects([f"{PREFIX}/latest.json", key])


@pytest.mark.parametrize("missing_setting", ["r2_endpoint", "r2_bucket_name"])
@pytest.mark.parametrize("operation", ["list", "delete"])
def test_r2_reuses_client_configuration_validation(r2_storage, monkeypatch, missing_setting, operation) -> None:
    monkeypatch.setattr(get_settings(), missing_setting, "")

    with pytest.raises(ValueError, match=missing_setting.upper()):
        if operation == "list":
            pack_deletion.list_storage_objects(PREFIX)
        else:
            pack_deletion.delete_storage_objects([f"{PREFIX}/latest.json"])
