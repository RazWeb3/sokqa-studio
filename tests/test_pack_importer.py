import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from main import app


client = TestClient(app)


def _configure_local_storage(tmp_path: Path, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")


def test_import_clears_audio_urls_but_keeps_tts_text(tmp_path, monkeypatch) -> None:
    _configure_local_storage(tmp_path, monkeypatch)
    document = {
        "id": "doc-pack",
        "type": "document",
        "schemaVersion": 1,
        "title": "Document",
        "assetBaseUrl": "https://old.example/pack",
        "documents": [
            {
                "id": "doc-1",
                "text": "AIの説明です。",
                "tts": {
                    "text": "エーアイの説明です。",
                    "audioPath": "audio/doc.mp3",
                    "audioUrl": "https://old.example/audio/doc.mp3",
                },
            }
        ],
    }
    quiz = {
        "id": "quiz-pack",
        "type": "quiz",
        "schemaVersion": 1,
        "title": "Quiz",
        "assetBaseUrl": "https://old.example/pack",
        "questions": [
            {
                "id": "q-1",
                "question": "AIとは何ですか?",
                "choices": ["人工知能", "通信規約", "記憶装置", "画面設計"],
                "answerIndex": 0,
                "explanation": "AIは人工知能です。",
                "tts": {
                    "questionText": "エーアイとは何ですか?",
                    "choiceTexts": ["人工知能", "通信規約", "記憶装置", "画面設計"],
                    "explanationText": "エーアイは人工知能です。",
                    "questionAudioPath": "audio/q.mp3",
                    "questionAudioUrl": "https://old.example/audio/q.mp3",
                    "choiceAudioPaths": [
                        "audio/c0.mp3",
                        "audio/c1.mp3",
                        None,
                        "audio/c3.mp3",
                    ],
                    "choiceAudioUrls": [
                        "https://old.example/audio/c0.mp3",
                        "https://old.example/audio/c1.mp3",
                        None,
                        "https://old.example/audio/c3.mp3",
                    ],
                    "explanationAudioPath": "audio/e.mp3",
                    "explanationAudioUrl": "https://old.example/audio/e.mp3",
                },
            }
        ],
    }

    response = client.post(
        "/packs/import",
        json={
            "creatorId": "creator_import",
            "contentId": "cnt_import_audio",
            "files": [
                {"name": "doc_01.json", "content": document},
                {"name": "quiz_01.json", "content": quiz},
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    doc_file = next(file for file in data["files"] if file["kind"] == "document")
    quiz_file = next(file for file in data["files"] if file["kind"] == "quiz")
    doc_path = doc_file["url"].split("/generated/", 1)[1]
    quiz_path = quiz_file["url"].split("/generated/", 1)[1]
    doc_content = json.loads((tmp_path / "generated" / doc_path).read_text(encoding="utf-8"))
    quiz_content = json.loads((tmp_path / "generated" / quiz_path).read_text(encoding="utf-8"))

    assert doc_content["assetBaseUrl"].endswith("/sokqa/creators/creator_import/packs/cnt_import_audio")
    assert quiz_content["assetBaseUrl"] == doc_content["assetBaseUrl"]
    assert doc_content["documents"][0]["tts"] == {"text": "エーアイの説明です。"}
    quiz_tts = quiz_content["questions"][0]["tts"]
    assert quiz_tts["questionText"] == "エーアイとは何ですか?"
    assert quiz_tts["choiceTexts"] == ["人工知能", "通信規約", "記憶装置", "画面設計"]
    assert quiz_tts["explanationText"] == "エーアイは人工知能です。"
    assert "questionAudioPath" not in quiz_tts
    assert "questionAudioUrl" not in quiz_tts
    assert "choiceAudioPaths" not in quiz_tts
    assert "choiceAudioUrls" not in quiz_tts
    assert "explanationAudioPath" not in quiz_tts
    assert "explanationAudioUrl" not in quiz_tts


def test_import_without_audio_urls_still_succeeds(tmp_path, monkeypatch) -> None:
    _configure_local_storage(tmp_path, monkeypatch)
    document = {
        "id": "doc-pack",
        "type": "document",
        "schemaVersion": 1,
        "title": "Document",
        "documents": [{"id": "doc-1", "text": "本文です。"}],
    }

    response = client.post(
        "/packs/import",
        json={
            "creatorId": "creator_import",
            "contentId": "cnt_import_plain",
            "files": [{"name": "doc_01.json", "content": document}],
        },
    )

    assert response.status_code == 200
    assert response.json()["validation"]["valid"] is True
    assert response.json()["manifest"]["schemaVersion"] == 2
    assert response.json()["manifest"]["change"]["operation"] == "import"
