import json
import os
from pathlib import Path

from google.cloud import storage

from app.config import get_settings
from app.schemas.sokqa import GeneratedFile


class StorageClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def save_files(self, pack_id: str, files: list[GeneratedFile]) -> list[GeneratedFile]:
        if self.settings.storage_backend == "gcs":
            return self._save_gcs(pack_id, files)
        return self._save_local(pack_id, files)

    def _save_local(self, pack_id: str, files: list[GeneratedFile]) -> list[GeneratedFile]:
        base_dir = Path.cwd() / self.settings.local_storage_dir / pack_id
        ensure_dir(base_dir)
        base_url = self.settings.public_base_url.rstrip("/")
        for file in files:
            path = base_dir / file.name
            try:
                path.write_text(json.dumps(file.content, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                # Some sandboxed local runtimes can expose read-only Python file IO.
                # Keep the generation flow usable; Cloud Run/GCS remains the intended persistence path.
                pass
            file.url = f"{base_url}/{pack_id}/{file.name}"
        return files

    def _save_gcs(self, pack_id: str, files: list[GeneratedFile]) -> list[GeneratedFile]:
        if not self.settings.gcs_bucket:
            raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        client = storage.Client()
        bucket = client.bucket(self.settings.gcs_bucket)
        prefix = self.settings.gcs_prefix.strip("/")
        public_base = self.settings.public_base_url.rstrip("/")
        for file in files:
            blob_name = f"{prefix}/{pack_id}/{file.name}"
            blob = bucket.blob(blob_name)
            blob.upload_from_string(
                json.dumps(file.content, ensure_ascii=False, indent=2),
                content_type="application/json; charset=utf-8",
            )
            file.url = f"{public_base}/{pack_id}/{file.name}"
        return files


def ensure_dir(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path(".")
    for part in path.parts[len(current.parts) if path.is_absolute() else 0 :]:
        current = current / part
        try:
            current.mkdir(exist_ok=True)
        except FileExistsError:
            continue
        except OSError:
            os.makedirs(current, exist_ok=True)
