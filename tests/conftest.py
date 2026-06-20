import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def default_gemini_provider_to_mock(monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_provider", "mock")
