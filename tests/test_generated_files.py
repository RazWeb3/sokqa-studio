import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import generated_files, pack_paths, storage_client


BASE = "private/studio"
AUDIO_PATH = "creators/demo/packs/course/objects/audio/av_1.mp3"
JSON_PATH = "creators/demo/packs/course/versions/v1/manifest.json"
AUDIO_URL = f"/generated/{BASE}/{AUDIO_PATH}"
JSON_URL = f"/generated/{BASE}/{JSON_PATH}"
AUDIO = b"0123456789"
SECRET = "https://secret-account.r2.cloudflarestorage.com/private?credential=SECRET"


@pytest.fixture(autouse=True)
def no_cloud_connections(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Tests must not connect to cloud storage"))
    monkeypatch.setattr(storage_client.boto3, "client", forbidden)
    monkeypatch.setattr(storage_client.storage, "Client", forbidden)


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    settings = SimpleNamespace(storage_backend="r2", r2_prefix=BASE, gcs_prefix="different/gcs")
    monkeypatch.setattr(generated_files, "get_settings", lambda: settings)
    monkeypatch.setattr(pack_paths, "get_settings", lambda: settings)
    storage = Mock()
    storage.read_object.return_value = AUDIO
    monkeypatch.setattr(generated_files, "StorageClient", Mock(return_value=storage))
    app = FastAPI()
    app.mount("/generated", generated_files.GeneratedFiles(directory=tmp_path), name="generated")
    return SimpleNamespace(app=app, storage=storage, settings=settings)


@pytest.fixture
def client(proxy):
    with TestClient(proxy.app, client=("127.0.0.1", 50000), base_url="http://localhost") as test_client:
        yield test_client


def assert_private(response):
    assert response.headers["cache-control"] == "private,no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "location" not in response.headers
    assert "access-control-allow-origin" not in response.headers
    assert SECRET not in str(response.headers)
    assert SECRET.encode() not in response.content


def test_json_uses_shared_r2_prefix_and_returns_bytes(client, proxy):
    payload = '{"title":"日本語"}'.encode("utf-8")
    proxy.storage.read_object.return_value = payload
    response = client.get(JSON_URL + "?download=1")
    assert response.status_code == 200
    assert response.content == payload
    assert response.json() == {"title": "日本語"}
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-length"] == str(len(payload))
    proxy.storage.read_object.assert_called_once_with(BASE, JSON_PATH)
    assert_private(response)


def test_mp3_is_proxied_not_redirected(client, proxy):
    response = client.get(AUDIO_URL)
    assert response.status_code == 200
    assert response.content == AUDIO
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["content-length"] == "10"
    assert response.headers["accept-ranges"] == "bytes"
    proxy.storage.read_object.assert_called_once_with(BASE, AUDIO_PATH)
    assert_private(response)


def test_storage_read_runs_outside_event_loop(client, proxy):
    def read_object(base, relative_path):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return AUDIO

    proxy.storage.read_object.side_effect = read_object
    assert client.get(AUDIO_URL).content == AUDIO
    proxy.storage.read_object.assert_called_once_with(BASE, AUDIO_PATH)


@pytest.mark.parametrize(
    "range_header, expected, content_range",
    [
        ("bytes=2-5", b"2345", "bytes 2-5/10"),
        ("bytes=7-", b"789", "bytes 7-9/10"),
        ("bytes=-3", b"789", "bytes 7-9/10"),
        ("bytes=0-0", b"0", "bytes 0-0/10"),
        ("bytes=9-", b"9", "bytes 9-9/10"),
        ("bytes=8-99", b"89", "bytes 8-9/10"),
        ("bytes=-99", AUDIO, "bytes 0-9/10"),
        ("bytes=0-9", AUDIO, "bytes 0-9/10"),
    ],
)
def test_single_byte_ranges(client, range_header, expected, content_range):
    response = client.get(AUDIO_URL, headers={"Range": range_header})
    assert response.status_code == 206
    assert response.content == expected
    assert response.headers["content-range"] == content_range
    assert response.headers["content-length"] == str(len(expected))
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "audio/mpeg"
    assert_private(response)


@pytest.mark.parametrize(
    "range_header",
    ["bytes=10-", "bytes=10-12", "bytes=5-2", "bytes=-0", "bytes=-", "bytes=", "", "items=0-1",
     "bytes=a-b", "bytes=1-2-3", "bytes=+1-2", "bytes=1.5-2", "bytes=0-" + "9" * 5000],
)
def test_invalid_or_unsatisfiable_range_is_416(client, range_header):
    response = client.get(AUDIO_URL, headers={"Range": range_header})
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */10"
    assert response.headers["content-length"] == "0"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.content == b""
    assert_private(response)


@pytest.mark.parametrize("range_header", ["bytes=0-0", "bytes=0-", "bytes=-1"])
def test_empty_object_cannot_satisfy_range(client, proxy, range_header):
    proxy.storage.read_object.return_value = b""
    response = client.get(AUDIO_URL, headers={"Range": range_header})
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */0"


def test_empty_object_without_range(client, proxy):
    proxy.storage.read_object.return_value = b""
    response = client.get(AUDIO_URL)
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "0"


@pytest.mark.parametrize(
    "headers",
    [
        {"Range": "bytes=0-1,4-5"},
        {"Range": "bytes=0-1", "If-Range": '"old-etag"'},
        {"Range": "bytes=0-1", "If-Range": "Wed, 21 Oct 2015 07:28:00 GMT"},
        {"Range": "invalid", "If-Range": ""},
        [("Range", "bytes=0-1"), ("Range", "bytes=4-5")],
    ],
)
def test_multiple_ranges_and_if_range_return_full_200(client, headers):
    response = client.get(AUDIO_URL, headers=headers)
    assert response.status_code == 200
    assert response.content == AUDIO
    assert response.headers["content-length"] == "10"
    assert "content-range" not in response.headers
    assert_private(response)


@pytest.mark.parametrize("url, media_type", [(AUDIO_URL, "audio/mpeg"), (JSON_URL, "application/json")])
@pytest.mark.parametrize("headers", [{}, {"Range": "bytes=2-5"}, {"Range": "invalid"}])
def test_head_has_full_headers_and_ignores_range(client, url, media_type, headers):
    response = client.head(url, headers=headers)
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "10"
    assert response.headers["content-type"] == media_type
    assert response.headers["accept-ranges"] == "bytes"
    assert "content-range" not in response.headers
    assert_private(response)


def test_head_response_body_is_empty_before_httpx_discards_it(proxy):
    scope = {
        "type": "http", "method": "HEAD", "scheme": "http", "path": AUDIO_URL,
        "root_path": "/generated", "headers": [(b"host", b"localhost")], "query_string": b"",
        "client": ("127.0.0.1", 50000), "server": ("localhost", 80),
    }
    response = asyncio.run(proxy.app.routes[-1].app.get_response("ignored", scope))
    assert response.status_code == 200
    assert response.body == b""
    assert response.headers["content-length"] == "10"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"])
def test_other_methods_are_405(client, proxy, method):
    response = client.request(method, AUDIO_URL)
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"
    proxy.storage.read_object.assert_not_called()
    assert_private(response)


@pytest.mark.parametrize(
    "path",
    [
        "other/manifest.json", "different/gcs/manifest.json", "private/studio-other/file.json",
        BASE, BASE + "/", BASE + ".json", BASE + "/file.html", BASE + "/file.js",
        BASE + "/file.wav", BASE + "/file.json.exe", BASE + "/file", BASE + "/.env",
        BASE + "/file.json/", BASE + "//file.json", "/" + BASE + "/file.json",
        BASE + "/%2e%2e/file.json", BASE + "/sub/%2e%2e/file.json",
        BASE + "/%252e%252e/file.json", "%2e%2e/" + BASE + "/file.json",
        "outside/%2e%2e/" + BASE + "/file.json", BASE + "/%2e/file.json",
        BASE + "/sub%5cfile.json", BASE + "/sub%255cfile.json", BASE + "/%00file.json",
        BASE + "/%25250afile.json", BASE + "/https://example.com/file.json",
    ],
)
def test_unsafe_paths_prefixes_and_extensions_are_rejected(client, proxy, path):
    response = client.get("/generated/" + path, follow_redirects=False)
    assert response.status_code == 404
    proxy.storage.read_object.assert_not_called()
    assert_private(response)


def test_newline_in_path_is_rejected_before_mount_by_router(client, proxy):
    response = client.get(f"/generated/{BASE}/%0afile.json")
    assert response.status_code == 404
    proxy.storage.read_object.assert_not_called()


@pytest.mark.parametrize("configured, expected", [("/custom/base/", "custom/base"), ("sokqa/packs", "sokqa")])
def test_shared_base_normalization_is_used(client, proxy, configured, expected):
    proxy.settings.r2_prefix = configured
    response = client.get(f"/generated/{expected}/{AUDIO_PATH}")
    assert response.status_code == 200
    proxy.storage.read_object.assert_called_once_with(expected, AUDIO_PATH)


@pytest.mark.parametrize("address", ["127.0.0.1", "127.2.3.4", "::1"])
@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
def test_loopback_peers_and_allowed_hosts(proxy, address, host):
    with TestClient(proxy.app, client=(address, 50000), base_url="http://localhost") as client:
        response = client.get(AUDIO_URL, headers={"Host": host})
    assert response.status_code == 200


@pytest.mark.parametrize("address", ["192.168.1.2", "10.0.0.1", "203.0.113.10", "2001:db8::1", "localhost", "testclient"])
def test_remote_or_non_ip_peer_is_rejected_even_with_localhost_host(proxy, address):
    with TestClient(proxy.app, client=(address, 50000), base_url="http://localhost") as client:
        response = client.get(AUDIO_URL, headers={"X-Forwarded-For": "127.0.0.1"})
    assert response.status_code == 403
    proxy.storage.read_object.assert_not_called()
    assert_private(response)


@pytest.mark.parametrize(
    "host", ["evil.example", "localhost.evil.example", "127.0.0.1.evil.example", "localhost.",
             "0.0.0.0", "127.0.0.2", "2130706433", "[::]", "[invalid", "", "localhost:65536",
             "localhost:invalid", "evil.example@localhost", "localhost/evil", "localhost#evil"],
)
def test_bad_host_is_rejected_for_loopback_peer(client, proxy, host):
    response = client.get(AUDIO_URL, headers={"Host": host, "X-Forwarded-Host": "localhost"})
    assert response.status_code == 403
    proxy.storage.read_object.assert_not_called()
    assert_private(response)


def test_missing_peer_is_rejected(proxy):
    with TestClient(proxy.app, client=None, base_url="http://localhost") as client:
        response = client.get(AUDIO_URL)
    assert response.status_code == 403
    proxy.storage.read_object.assert_not_called()


def test_missing_host_is_rejected(proxy):
    async def without_host(scope, receive, send):
        # TestClient inserts Host automatically, so remove it at the ASGI boundary.
        if scope["type"] == "http":
            scope = {**scope, "headers": [(key, value) for key, value in scope["headers"] if key != b"host"]}
        await proxy.app(scope, receive, send)

    with TestClient(without_host, client=("127.0.0.1", 50000), base_url="http://localhost") as client:
        response = client.get(AUDIO_URL)
    assert response.status_code == 403
    proxy.storage.read_object.assert_not_called()


def test_duplicate_host_is_rejected(client, proxy):
    response = client.get(AUDIO_URL, headers=[("Host", "localhost"), ("Host", "evil.example")])
    assert response.status_code == 403
    proxy.storage.read_object.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_missing_object_is_404_without_exception_details(client, proxy, method):
    proxy.storage.read_object.side_effect = FileNotFoundError(SECRET)
    response = client.request(method, AUDIO_URL)
    assert response.status_code == 404
    assert response.content == b""
    assert_private(response)


@pytest.mark.parametrize(
    "error",
    [
        ClientError({"Error": {"Code": "AccessDenied", "Message": SECRET}}, "GetObject"),
        ClientError({"Error": {"Code": "NoSuchBucket", "Message": SECRET}}, "GetObject"),
        ClientError({"Error": {"Code": "InternalError", "Message": SECRET}}, "GetObject"),
        BotoCoreError(),
        EndpointConnectionError(endpoint_url=SECRET),
    ],
)
def test_upstream_errors_are_sanitized_502(client, proxy, error):
    proxy.storage.read_object.side_effect = error
    response = client.get(AUDIO_URL)
    assert response.status_code == 502
    assert response.content == b""
    assert_private(response)


@pytest.mark.parametrize("source", ["settings", "base", "constructor", "read"])
def test_configuration_errors_are_sanitized_500(client, proxy, monkeypatch, source):
    failure = Mock(side_effect=ValueError(SECRET))
    if source == "settings":
        monkeypatch.setattr(generated_files, "get_settings", failure)
    elif source == "base":
        proxy.settings.r2_prefix = SECRET
    elif source == "constructor":
        monkeypatch.setattr(generated_files, "StorageClient", failure)
    else:
        proxy.storage.read_object.side_effect = ValueError(SECRET)
    response = client.get(AUDIO_URL)
    assert response.status_code == 500
    assert response.content == b""
    assert_private(response)


@pytest.mark.parametrize("backend", ["local", "gcs"])
def test_non_r2_preserves_static_files_including_other_hosts_and_extensions(proxy, tmp_path, backend):
    proxy.settings.storage_backend = backend
    (tmp_path / "file.txt").write_bytes(b"local static file")
    with TestClient(proxy.app, client=("192.168.1.2", 50000), base_url="http://example.com") as client:
        response = client.get("/generated/file.txt")
        assert response.status_code == 200
        assert response.content == b"local static file"
        assert "etag" in response.headers
        assert client.get("/generated/file.txt", headers={"If-None-Match": response.headers["etag"]}).status_code == 304
        head = client.head("/generated/file.txt")
        assert head.content == b""
        assert head.headers["content-length"] == str(len(b"local static file"))
        assert client.get("/generated/missing.json").status_code == 404
        assert client.post("/generated/file.txt").status_code == 405
    proxy.storage.read_object.assert_not_called()


def test_main_mount_uses_generated_files(proxy):
    from main import app

    mount = next(route for route in app.routes if getattr(route, "path", None) == "/generated")
    assert isinstance(mount.app, generated_files.GeneratedFiles)
    with TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost") as client:
        response = client.get(AUDIO_URL)
    assert response.status_code == 200
    assert response.content == AUDIO
    proxy.storage.read_object.assert_called_once_with(BASE, AUDIO_PATH)
