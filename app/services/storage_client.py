import json
import os
import subprocess
import tempfile
from pathlib import Path

from google.cloud import storage

from app.config import get_settings
from app.schemas.sokqa import GeneratedFile
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
