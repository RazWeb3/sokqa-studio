from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = "local"
    sokqa_author: str = "Sokqa Team"
    public_base_url: str = "http://localhost:8000/generated"
    storage_backend: Literal["local", "gcs"] = "local"
    local_storage_dir: str = "generated"
    gcs_bucket: str = ""
    gcs_prefix: str = "sokqa/packs"
    allowed_manifest_domains: str = Field(
        "convly.jp,studio.convly.jp,cdn.convly.jp,localhost,127.0.0.1"
    )
    gemini_provider: Literal["mock", "gemini"] = "mock"
    gemini_model: str = "gemini-2.5-flash"

    @property
    def allowed_domains(self) -> set[str]:
        return {domain.strip().lower() for domain in self.allowed_manifest_domains.split(",") if domain.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
