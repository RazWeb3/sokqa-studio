from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    sokqa_author: str = "Sokqa Team"
    public_base_url: str = "http://localhost:8000/generated"
    storage_backend: Literal["local", "gcs"] = "local"
    local_storage_dir: str = "generated"
    tts_rules_path: str = "tts_rules.json"
    tts_user_rules_path: str = ""
    tts_reading_mode: Literal["rule", "llm", "auto"] = "rule"
    gcs_bucket: str = ""
    gcs_prefix: str = "sokqa"
    # Hackathon deployments may use readable values such as creator_demo.
    # Production creator IDs should be opaque random IDs, not emails or sequential values.
    default_creator_id: str = "creator_default"
    allowed_manifest_domains: str = Field(
        "convly.jp,studio.convly.jp,cdn.convly.jp,localhost,127.0.0.1"
    )
    gemini_provider: Literal["mock", "gemini"] = "mock"
    gemini_model: str = "gemini-2.5-flash"
    gemini_model_doc: str | None = None
    gemini_model_quiz: str | None = None
    gemini_model_planner: str | None = None
    gemini_model_quality: str | None = None
    google_cloud_project: str = ""
    google_cloud_location: str = "global"
    google_genai_use_vertexai: bool = False

    # TTS estimation: credit cost per character (仮の値。Google Cloud TTS の料金体系に合わせて後で調整)
    # 現状は 1 文字 = 0.0001 クレジット（= 10,000 文字で 1 クレジット）程度を想定
    tts_credit_per_char: float = 0.0001
    cloud_tts_language_code: str = "ja-JP"
    cloud_tts_voice: str = "ja-JP-Neural2-B"
    cloud_tts_speaking_rate: float = 1.0
    cloud_tts_pitch: float = 0.0
    cloud_tts_max_concurrency: int = Field(default=5, ge=1, le=10)
    cloud_tts_recording_request_max_units: int = Field(default=20, ge=1, le=100)

    @property
    def allowed_domains(self) -> set[str]:
        return {domain.strip().lower() for domain in self.allowed_manifest_domains.split(",") if domain.strip()}

    @property
    def document_model(self) -> str:
        return self.gemini_model_doc or self.gemini_model

    @property
    def quiz_model(self) -> str:
        return self.gemini_model_quiz or self.gemini_model

    @property
    def planner_model(self) -> str:
        return self.gemini_model_planner or self.gemini_model

    @property
    def quality_model(self) -> str:
        return self.gemini_model_quality or self.gemini_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
