import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.gemini_client import GeminiClient
from app.services.pack_metadata import pack_storage_prefix
from app.services.quality_checker import _generate_json_with_retry
from main import app


client = TestClient(app)


def _write_document_pack(tmp_path: Path, monkeypatch) -> dict:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")

    creator_id = "creator_quality"
    content_id = "content_quality"
    version_id = "v20260612_120000"
    pack_name = "doc_01.json"
    prefix = pack_storage_prefix(creator_id, content_id, version_id)
    target_dir = tmp_path / "generated" / prefix
    target_dir.mkdir(parents=True, exist_ok=True)
    content = {
        "id": "content_quality_doc_01",
        "type": "document",
        "schemaVersion": 1,
        "title": "品質チェック用ドキュメント",
        "language": "ja",
        "documents": [
            {"id": "doc-1", "text": "SQLとJSONを説明します。ドキュメントによると重要です。"}
        ],
    }
    (target_dir / pack_name).write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return {
        "creatorId": creator_id,
        "contentId": content_id,
        "versionId": version_id,
        "packName": pack_name,
        "kind": "document",
    }


def test_quality_check_mock_provider_returns_sample_issues(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    response = client.post("/quality-check", json={"target": target})

    assert response.status_code == 200
    data = response.json()
    assert data["fileName"] == "doc_01.json"
    assert {issue["category"] for issue in data["issues"]} == {
        "factual",
        "reading",
        "double_utterance",
        "notation",
        "style",
        "leak",
    }
    assert all(issue["location"]["fileName"] == "doc_01.json" for issue in data["issues"])


def test_quality_check_invalid_llm_response_is_error(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
        return {"issues": [{"category": "unknown"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality-check", json={"target": target})

    assert response.status_code == 502
    assert "response validation failed" in response.json()["detail"]


def test_quality_check_llm_failure_does_not_fallback_to_mock(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
        raise RuntimeError("temporary failure")

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality-check", json={"target": target})

    assert response.status_code == 502
    assert "LLM call failed" in response.json()["detail"]


def test_quality_check_retries_temporary_failures() -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def generate() -> dict:
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("retry me")
        return {"issues": []}

    result = _generate_json_with_retry(generate, attempts=3, initial_delay=0.1, sleep=sleeps.append)

    assert result == {"issues": []}
    assert calls["count"] == 3
    assert sleeps == [0.1, 0.2]


def test_quality_check_missing_pack_returns_404(tmp_path, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    response = client.post(
        "/quality-check",
        json={
            "target": {
                "creatorId": "creator_missing",
                "contentId": "content_missing",
                "versionId": "v20260612_120000",
                "packName": "doc_01.json",
                "kind": "document",
            }
        },
    )

    assert response.status_code == 404
