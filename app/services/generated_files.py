from __future__ import annotations

import re
from ipaddress import ip_address
from pathlib import PurePosixPath

from botocore.exceptions import BotoCoreError, ClientError
from starlette._utils import get_route_path
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.config import get_settings
from app.services.pack_paths import storage_base_prefix, validate_relative_path
from app.services.storage_client import StorageClient


_PRIVATE_HEADERS = {"Cache-Control": "private,no-store", "X-Content-Type-Options": "nosniff"}
_MEDIA_TYPES = {".json": "application/json", ".mp3": "audio/mpeg"}
_LOCAL_HOST_RE = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]+)?", re.IGNORECASE)


def _is_loopback_request(request: Request) -> bool:
    hosts = request.headers.getlist("host")
    if request.client is None or len(hosts) != 1 or not _LOCAL_HOST_RE.fullmatch(hosts[0]):
        return False
    try:
        # Some Starlette versions ignore malformed Host headers and use the
        # server address instead, so validate the raw header as well as the URL.
        url = request.url
        valid_port = url.port is None or 0 <= url.port <= 65535
        return valid_port and ip_address(request.client.host).is_loopback and url.hostname in {
            "localhost", "127.0.0.1", "::1"
        }
    except ValueError:
        return False


def _byte_range(value: str, size: int) -> tuple[int, int]:
    match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", value.strip())
    if match is None or size == 0:
        raise ValueError("Invalid byte range")
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix == 0:
            raise ValueError("Invalid suffix range")
        return max(0, size - suffix), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError("Unsatisfiable byte range")
    return start, min(end, size - 1)


class GeneratedFiles(StaticFiles):
    """Keep static serving for local/GCS; proxy private R2 objects on loopback only."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            backend = get_settings().storage_backend
        except ValueError:
            return Response(status_code=500, headers=_PRIVATE_HEADERS)
        if backend != "r2":
            return await super().get_response(path, scope)

        request = Request(scope)
        if request.method not in {"GET", "HEAD"}:
            return Response(status_code=405, headers={**_PRIVATE_HEADERS, "Allow": "GET, HEAD"})
        if not _is_loopback_request(request):
            return Response(status_code=403, headers=_PRIVATE_HEADERS)

        # StaticFiles.get_path() normalizes away traversal and uses OS separators.
        # Validate the original mount-relative URL path instead, including its base.
        try:
            safe_path = validate_relative_path(get_route_path(scope).removeprefix("/"))
        except ValueError:
            return Response(status_code=404, headers=_PRIVATE_HEADERS)
        try:
            base = validate_relative_path(storage_base_prefix())
        except ValueError:
            return Response(status_code=500, headers=_PRIVATE_HEADERS)
        media_type = _MEDIA_TYPES.get(PurePosixPath(safe_path).suffix.lower())
        if not safe_path.startswith(f"{base}/") or media_type is None or "." in safe_path.split("/"):
            return Response(status_code=404, headers=_PRIVATE_HEADERS)
        relative_path = safe_path[len(base) + 1:]

        try:
            data = await run_in_threadpool(StorageClient().read_object, base, relative_path)
        except FileNotFoundError:
            return Response(status_code=404, headers=_PRIVATE_HEADERS)
        except (ClientError, BotoCoreError):
            return Response(status_code=502, headers=_PRIVATE_HEADERS)
        except ValueError:
            return Response(status_code=500, headers=_PRIVATE_HEADERS)

        size = len(data)
        headers = {**_PRIVATE_HEADERS, "Accept-Ranges": "bytes", "Content-Length": str(size)}
        status_code = 200
        ranges = request.headers.getlist("range")
        # HEAD ignores Range. Multiple ranges and If-Range deliberately fall back
        # to the complete object; no multipart responses or validators are needed.
        if request.method == "GET" and len(ranges) == 1 and "," not in ranges[0] and "if-range" not in request.headers:
            try:
                start, end = _byte_range(ranges[0], size)
            except ValueError:
                return Response(
                    status_code=416,
                    headers={**headers, "Content-Range": f"bytes */{size}", "Content-Length": "0"},
                )
            data = data[start:end + 1]
            status_code = 206
            headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(len(data))})
        return Response(
            content=b"" if request.method == "HEAD" else data,
            status_code=status_code,
            media_type=media_type,
            headers=headers,
        )
