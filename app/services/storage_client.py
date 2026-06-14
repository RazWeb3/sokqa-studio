import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from google.cloud import storage

from app.config import get_settings
from app.schemas.sokqa import GeneratedFile
from app.services.pack_paths import assert_resolved_under_root, manifest_relative_path, validate_relative_path
from app.services.storage_status import record_storage_event


class StorageClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def save_files(self, pack_id: str, files: list[GeneratedFile], storage_prefix: str | None = None) -> list[GeneratedFile]:
        if self.settings.storage_backend == "gcs":
            return self._save_gcs(pack_id, files, storage_prefix)
        return self._save_local(pack_id, files, storage_prefix)

    def save_bytes(
        self,
        pack_id: str,
        object_name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        storage_prefix: str | None = None,
    ) -> str:
        if self.settings.storage_backend == "gcs":
            return self._save_gcs_bytes(pack_id, object_name, data, content_type, storage_prefix)
        return self._save_local_bytes(pack_id, object_name, data, content_type, storage_prefix)

    def load_json_file(self, pack_id: str, file_name: str, storage_prefix: str | None = None) -> dict:
        if self.settings.storage_backend == "gcs":
            return self._load_gcs_json(pack_id, file_name, storage_prefix)
        return self._load_local_json(pack_id, file_name, storage_prefix)

    def public_url_for_prefix(self, storage_prefix: str) -> str:
        return f"{self.settings.public_base_url.rstrip('/')}/{storage_prefix.strip('/')}"

    def copy_prefix(self, source_prefix: str, target_prefix: str) -> list[str]:
        if self.settings.storage_backend == "gcs":
            return self._copy_gcs_prefix(source_prefix, target_prefix)
        return self._copy_local_prefix(source_prefix, target_prefix)

    def save_object(self, prefix: str, relative_path: str, data: bytes | str, content_type: str) -> str:
        safe_prefix = validate_relative_path(prefix)
        safe_relative_path = validate_relative_path(relative_path)
        payload = data.encode("utf-8") if isinstance(data, str) else data
        if self.settings.storage_backend == "gcs":
            return self._save_gcs_object(safe_prefix, safe_relative_path, payload, content_type)
        return self._save_local_object(safe_prefix, safe_relative_path, payload, content_type)

    def save_manifest(self, prefix: str, version_id: str, manifest_json: dict[str, Any] | str) -> str:
        relative_path = manifest_relative_path(version_id)
        payload = (
            manifest_json
            if isinstance(manifest_json, str)
            else json.dumps(manifest_json, ensure_ascii=False, indent=2)
        )
        return self.save_object(prefix, relative_path, payload, "application/json; charset=utf-8")

    def read_manifest(self, prefix: str, version_id: str) -> dict:
        payload = self.read_object(prefix, manifest_relative_path(version_id))
        return json.loads(payload.decode("utf-8"))

    def read_object(self, prefix: str, relative_path: str) -> bytes:
        safe_prefix = validate_relative_path(prefix)
        safe_relative_path = validate_relative_path(relative_path)
        if self.settings.storage_backend == "gcs":
            return self._read_gcs_object(safe_prefix, safe_relative_path)
        return self._read_local_object(safe_prefix, safe_relative_path)

    def list_manifests(self, prefix: str) -> list[str]:
        safe_prefix = validate_relative_path(prefix)
        if self.settings.storage_backend == "gcs":
            return self._list_gcs_manifests(safe_prefix)
        return self._list_local_manifests(safe_prefix)

    def _save_local(self, pack_id: str, files: list[GeneratedFile], storage_prefix: str | None = None) -> list[GeneratedFile]:
        relative_prefix = (storage_prefix or pack_id).strip("/")
        base_dir = Path.cwd() / self.settings.local_storage_dir / relative_prefix
        storage_available = ensure_dir(base_dir)
        if not storage_available and base_dir.exists() and base_dir.is_dir():
            storage_available = True
            record_storage_event(f"local save using existing directory: {base_dir}")
        elif not storage_available:
            record_storage_event(f"local save skipped: could not create directory {base_dir}")
        base_url = self.settings.public_base_url.rstrip("/")
        for file in files:
            path = base_dir / file.name
            if storage_available:
                try:
                    write_json_file(path, file.content)
                    record_storage_event(f"local saved: {file.name}")
                except OSError as exc:
                    # Some local runtimes can restrict Python file IO. Keep generation usable;
                    # Cloud Run/GCS remains the intended persistence path for shared manifests.
                    record_storage_event(f"local save skipped: {file.name}: {exc}")
                    storage_available = False
            else:
                record_storage_event(f"local save skipped: {file.name}: storage unavailable")
            file.url = f"{base_url}/{relative_prefix}/{file.name}"
        return files

    def _save_local_bytes(
        self,
        pack_id: str,
        object_name: str,
        data: bytes,
        content_type: str,
        storage_prefix: str | None = None,
    ) -> str:
        relative_prefix = (storage_prefix or pack_id).strip("/")
        relative_name = object_name.strip("/")
        base_dir = Path.cwd() / self.settings.local_storage_dir / relative_prefix
        target = base_dir / relative_name
        storage_available = ensure_dir(target.parent)
        if storage_available:
            try:
                target.write_bytes(data)
                record_storage_event(f"local saved: {relative_name} ({content_type})")
            except OSError as exc:
                record_storage_event(f"local binary save skipped: {relative_name}: {exc}")
        else:
            record_storage_event(f"local binary save skipped: {relative_name}: storage unavailable")
        return f"{self.settings.public_base_url.rstrip('/')}/{relative_prefix}/{relative_name}"

    def _load_local_json(self, pack_id: str, file_name: str, storage_prefix: str | None = None) -> dict:
        relative_prefix = (storage_prefix or pack_id).strip("/")
        path = Path.cwd() / self.settings.local_storage_dir / relative_prefix / file_name.strip("/")
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _copy_local_prefix(self, source_prefix: str, target_prefix: str) -> list[str]:
        source = Path.cwd() / self.settings.local_storage_dir / source_prefix.strip("/")
        target = Path.cwd() / self.settings.local_storage_dir / target_prefix.strip("/")
        if not source.exists():
            return []
        copied: list[str] = []
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            destination = target / relative
            ensure_dir(destination.parent)
            shutil.copy2(path, destination)
            copied.append(f"{target_prefix.strip('/')}/{relative.as_posix()}")
        record_storage_event(f"local copied prefix: {source_prefix} -> {target_prefix} ({len(copied)} objects)")
        return copied

    def _save_local_object(self, prefix: str, relative_path: str, data: bytes, content_type: str) -> str:
        root = Path.cwd() / self.settings.local_storage_dir / prefix
        target = assert_resolved_under_root(root, relative_path)
        storage_available = ensure_dir(target.parent)
        if storage_available:
            try:
                target.write_bytes(data)
                record_storage_event(f"local saved v2 object: {prefix}/{relative_path} ({content_type})")
            except OSError as exc:
                record_storage_event(f"local v2 object save failed: {prefix}/{relative_path}: {exc}")
                raise
        else:
            raise FileNotFoundError(target)
        return f"{self.settings.public_base_url.rstrip('/')}/{prefix}/{relative_path}"

    def _read_local_object(self, prefix: str, relative_path: str) -> bytes:
        root = Path.cwd() / self.settings.local_storage_dir / prefix
        target = assert_resolved_under_root(root, relative_path)
        return target.read_bytes()

    def _list_local_manifests(self, prefix: str) -> list[str]:
        root = Path.cwd() / self.settings.local_storage_dir / prefix
        versions = root / "versions"
        if not versions.exists():
            return []
        manifests: list[str] = []
        for path in versions.glob("*/manifest.json"):
            if path.is_file():
                manifests.append(path.relative_to(root).as_posix())
        return sorted(manifests)

    def _save_gcs(self, pack_id: str, files: list[GeneratedFile], storage_prefix: str | None = None) -> list[GeneratedFile]:
        if not self.settings.gcs_bucket:
            raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        client = storage.Client()
        bucket = client.bucket(self.settings.gcs_bucket)
        prefix = (storage_prefix or f"{self.settings.gcs_prefix.strip('/')}/{pack_id}").strip("/")
        public_base = self.settings.public_base_url.rstrip("/")
        for file in files:
            blob_name = f"{prefix}/{file.name}"
            blob = bucket.blob(blob_name)
            blob.upload_from_string(
                json.dumps(file.content, ensure_ascii=False, indent=2),
                content_type="application/json; charset=utf-8",
            )
            record_storage_event(f"gcs saved: {blob_name}")
            file.url = f"{public_base}/{prefix}/{file.name}"
        return files

    def _save_gcs_bytes(
        self,
        pack_id: str,
        object_name: str,
        data: bytes,
        content_type: str,
        storage_prefix: str | None = None,
    ) -> str:
        if not self.settings.gcs_bucket:
            raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        client = storage.Client()
        bucket = client.bucket(self.settings.gcs_bucket)
        prefix = (storage_prefix or f"{self.settings.gcs_prefix.strip('/')}/{pack_id}").strip("/")
        relative_name = object_name.strip("/")
        blob_name = f"{prefix}/{relative_name}"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type=content_type)
        record_storage_event(f"gcs saved: {blob_name} ({content_type})")
        return f"{self.settings.public_base_url.rstrip('/')}/{prefix}/{relative_name}"

    def _load_gcs_json(self, pack_id: str, file_name: str, storage_prefix: str | None = None) -> dict:
        if not self.settings.gcs_bucket:
            raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        client = storage.Client()
        bucket = client.bucket(self.settings.gcs_bucket)
        prefix = (storage_prefix or f"{self.settings.gcs_prefix.strip('/')}/{pack_id}").strip("/")
        blob_name = f"{prefix}/{file_name.strip('/')}"
        payload = bucket.blob(blob_name).download_as_text(encoding="utf-8")
        return json.loads(payload)

    def _copy_gcs_prefix(self, source_prefix: str, target_prefix: str) -> list[str]:
        if not self.settings.gcs_bucket:
            raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        client = storage.Client()
        bucket = client.bucket(self.settings.gcs_bucket)
        source = source_prefix.strip("/")
        target = target_prefix.strip("/")
        copied: list[str] = []
        for blob in bucket.list_blobs(prefix=f"{source}/"):
            relative = blob.name.removeprefix(f"{source}/")
            if not relative:
                continue
            destination_name = f"{target}/{relative}"
            bucket.copy_blob(blob, bucket, destination_name)
            copied.append(destination_name)
        record_storage_event(f"gcs copied prefix: {source_prefix} -> {target_prefix} ({len(copied)} objects)")
        return copied

    def _save_gcs_object(self, prefix: str, relative_path: str, data: bytes, content_type: str) -> str:
        if not self.settings.gcs_bucket:
            raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        client = storage.Client()
        bucket = client.bucket(self.settings.gcs_bucket)
        blob_name = f"{prefix}/{relative_path}"
        bucket.blob(blob_name).upload_from_string(data, content_type=content_type)
        record_storage_event(f"gcs saved v2 object: {blob_name} ({content_type})")
        return f"{self.settings.public_base_url.rstrip('/')}/{prefix}/{relative_path}"

    def _read_gcs_object(self, prefix: str, relative_path: str) -> bytes:
        if not self.settings.gcs_bucket:
            raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        client = storage.Client()
        bucket = client.bucket(self.settings.gcs_bucket)
        return bucket.blob(f"{prefix}/{relative_path}").download_as_bytes()

    def _list_gcs_manifests(self, prefix: str) -> list[str]:
        if not self.settings.gcs_bucket:
            raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        client = storage.Client()
        bucket = client.bucket(self.settings.gcs_bucket)
        manifests: list[str] = []
        for blob in bucket.list_blobs(prefix=f"{prefix}/versions/"):
            relative = blob.name.removeprefix(f"{prefix}/")
            if relative.endswith("/manifest.json"):
                manifests.append(relative)
        return sorted(manifests)


def ensure_dir(path: Path) -> bool:
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return True
    except OSError as exc:
        record_storage_event(f"local save skipped: mkdir failed for {path}: {exc}")
        if os.name == "nt":
            try:
                ensure_dir_windows_fallback(path)
                return True
            except (OSError, subprocess.CalledProcessError) as fallback_exc:
                record_storage_event(f"local save skipped: mkdir fallback failed for {path}: {fallback_exc}")
        return False


def write_json_file(path: Path, content: dict) -> None:
    payload = json.dumps(content, ensure_ascii=False, indent=2)
    try:
        with path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
        return
    except OSError:
        pass

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(path.parent),
            suffix=".tmp",
        ) as handle:
            handle.write(payload)
            temp_name = handle.name
        Path(temp_name).replace(path)
        return
    except OSError:
        pass

    if os.name == "nt":
        write_json_file_windows_fallback(path, content)
        return

    raise FileNotFoundError(path)


def write_json_file_windows_fallback(path: Path, content: dict) -> None:
    payload = json.dumps(content, ensure_ascii=False, indent=2)
    executable = "pwsh.exe"
    command = [
        executable,
        "-NoProfile",
        "-Command",
        "$input | Set-Content -LiteralPath $env:SOKQA_LOCAL_SAVE_PATH -Encoding UTF8",
    ]
    env = os.environ.copy()
    env["SOKQA_LOCAL_SAVE_PATH"] = str(path)
    try:
        subprocess.run(command, input=payload, text=True, check=True, env=env)
    except (OSError, subprocess.CalledProcessError):
        command[0] = "powershell.exe"
        subprocess.run(command, input=payload, text=True, check=True, env=env)


def ensure_dir_windows_fallback(path: Path) -> None:
    command = [
        "pwsh.exe",
        "-NoProfile",
        "-Command",
        "New-Item -ItemType Directory -Force -Path $env:SOKQA_LOCAL_SAVE_DIR | Out-Null",
    ]
    env = os.environ.copy()
    env["SOKQA_LOCAL_SAVE_DIR"] = str(path)
    try:
        subprocess.run(command, check=True, env=env)
    except (OSError, subprocess.CalledProcessError):
        command[0] = "powershell.exe"
        subprocess.run(command, check=True, env=env)
