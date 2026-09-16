import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def default_services_to_local_mocks(monkeypatch, tmp_path):
    # A developer's .env may point at a real R2/GCS bucket. Tests must opt into
    # another backend explicitly and must never write into the user's packs.
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")
