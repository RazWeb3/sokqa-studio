import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.gemini_client import GeminiClient
from app.services.pack_paths import pack_root_prefix
from app.schemas.sokqa import GeneratedFile
from app.services import quality_checker
from app.services.quality_checker import TTS_QUALITY_CATEGORIES, _generate_json_with_retry, _quality_prompt, _quality_response_from_data
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
    prefix = pack_root_prefix(creator_id, content_id)
    target_dir = tmp_path / "generated" / prefix
    target_dir.mkdir(parents=True, exist_ok=True)
    file_version_id = "fv_20260612_120000_document_doc_01"
    content = {
        "id": "content_quality_doc_01",
        "type": "document",
        "schemaVersion": 1,
        "title": "品質チェック用ドキュメント",
        "language": "ja",
        "assetBaseUrl": f"http://localhost:8000/generated/{prefix}",
        "documents": [
            {"id": "doc-1", "text": "SQLとJSONを説明します。ドキュメントによると重要です。"}
        ],
    }
    (target_dir / "objects" / "doc").mkdir(parents=True, exist_ok=True)
    (target_dir / "versions" / version_id).mkdir(parents=True, exist_ok=True)
    (target_dir / "objects" / "doc" / f"{file_version_id}.json").write_text(
        json.dumps(content, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest = {
        "id": "content_quality_manifest_r1",
        "type": "pack_manifest",
        "schemaVersion": 1,
        "contentId": content_id,
        "title": "品質チェック用ドキュメント",
        "creator": {"id": creator_id, "displayName": None},
        "revision": 1,
        "versionId": version_id,
        "buildId": "build_20260612_120000",
        "generatedAt": "2026-06-12T12:00:00+09:00",
        "change": {"operation": "initial_generate"},
        "items": [
            {
                "kind": "document",
                "name": pack_name,
                "title": "品質チェック用ドキュメント",
                "logicalId": "doc_01",
                "fileVersionId": file_version_id,
                "url": f"http://localhost:8000/generated/{prefix}/objects/doc/{file_version_id}.json",
            }
        ],
    }
    (target_dir / "versions" / version_id / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "creatorId": creator_id,
        "contentId": content_id,
        "versionId": version_id,
        "packName": pack_name,
        "kind": "document",
    }


def test_text_quality_check_mock_provider_returns_text_issues(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    response = client.post("/quality/text-check", json={"target": target})

    assert response.status_code == 200
    data = response.json()
    assert data["fileName"] == "doc_01.json"
    assert {issue["category"] for issue in data["issues"]} == {"factual", "style", "leak"}
    assert all(issue["location"]["fileName"] == "doc_01.json" for issue in data["issues"])


def test_text_quality_prompt_limits_targets_and_excludes_tts_fields() -> None:
    prompt, truncated = _quality_prompt(
        "sample_quiz.json",
        {
            "type": "quiz",
            "questions": [
                {
                    "id": "q-1",
                    "question": "問題文",
                    "choices": ["A", "B", "C", "D"],
                    "explanation": "解説",
                    "tts": {
                        "questionText": "TTS専用",
                        "choiceTexts": ["TTS A", "TTS B", "TTS C", "TTS D"],
                        "explanationText": "TTS解説",
                    },
                }
            ],
        },
        50,
        mode="text",
    )

    assert truncated is False
    assert "document.documents[].text" in prompt
    assert "quiz.questions[].question" in prompt
    assert "quiz.questions[].choices[]" in prompt
    assert "quiz.questions[].explanation" in prompt
    assert "Ignore all tts fields" in prompt
    assert "tts.questionText" in prompt


def test_tts_quality_check_mock_provider_returns_tts_issues(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    response = client.post("/quality/tts-check", json={"target": target})

    assert response.status_code == 200
    data = response.json()
    assert {issue["category"] for issue in data["issues"]} == {
        "reading",
        "double_utterance",
        "notation",
        "tts_text_mismatch",
    }
    assert all(issue["severity"] != "high" for issue in data["issues"] if issue["category"] == "tts_text_mismatch")


def test_tts_quality_check_detects_missing_learning_language_choice_texts(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="quiz_english.json",
        kind="quiz",
        content={
            "id": "quiz_english",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "英会話",
            "language": "ja",
            "learningLanguage": "en",
            "choiceLanguageMode": "learning",
            "questions": [
                {
                    "id": "q-1",
                    "question": "朝の挨拶はどれですか。",
                    "choices": ["Good morning", "Hello", "Good evening", "Goodbye"],
                    "answerIndex": 0,
                    "explanation": "朝は Good morning を使います。",
                }
            ],
        },
    )
    monkeypatch.setattr(
        quality_checker,
        "load_target_pack",
        lambda _target: SimpleNamespace(file=file),
    )

    response = client.post(
        "/quality/tts-check",
        json={
            "target": {
                "creatorId": "creator",
                "contentId": "content",
                "versionId": "version",
                "packName": "quiz_english.json",
                "kind": "quiz",
            }
        },
    )

    assert response.status_code == 200
    issues = response.json()["issues"]
    missing = [issue for issue in issues if "choiceTexts" in issue["issue"]]
    assert len(missing) == 4
    assert [issue["location"]["field"] for issue in missing] == [
        "choices[0]",
        "choices[1]",
        "choices[2]",
        "choices[3]",
    ]


def test_tts_quality_location_is_normalized() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.9,
                    "location": {
                        "fileName": "quiz.json",
                        "unitId": "q-1",
                        "field": "tts.explanationText",
                    },
                    "excerpt": "SQL",
                    "issue": "読みを確認します。",
                    "suggestion": "エスキューエル",
                }
            ]
        },
        file_name="quiz.json",
        model="test",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
    )

    assert response.issues[0].location.field == "explanation"


def test_tts_quality_check_filters_null_audio_issues(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
        return {
            "issues": [
                {
                    "category": "reading",
                    "severity": "low",
                    "confidence": 0.6,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "audioPath"},
                    "excerpt": "audioPath is null",
                    "issue": "audioPath が null です。",
                    "suggestion": "録音してください。",
                },
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "SQL",
                    "issue": "SQL が誤読される可能性があります。",
                    "suggestion": "エスキューエルにします。",
                },
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality/tts-check", json={"target": target})

    assert response.status_code == 200
    data = response.json()
    assert [issue["excerpt"] for issue in data["issues"]] == ["SQL"]


def test_tts_quality_response_filters_unspoken_symbol_only_reading_issues() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "low",
                    "confidence": 0.7,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "question"},
                    "excerpt": "「」",
                    "issue": "かぎ括弧の読み方に関する指摘です。",
                    "suggestion": "読まない",
                },
                {
                    "category": "reading",
                    "severity": "low",
                    "confidence": 0.7,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "choices"},
                    "excerpt": "・",
                    "issue": "中黒の読み方に関する指摘です。",
                    "suggestion": "読まない",
                },
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "question"},
                    "excerpt": "SQL",
                    "issue": "括弧内の専門語が誤読される可能性があります。",
                    "suggestion": "エスキューエル",
                },
            ]
        },
        file_name="quiz_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
    )

    assert [issue.excerpt for issue in response.issues] == ["SQL"]


def test_tts_quality_response_keeps_words_inside_brackets() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "『API』",
                    "issue": "括弧内のAPIが誤読される可能性があります。",
                    "suggestion": "『エーピーアイ』",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
    )

    assert [issue.excerpt for issue in response.issues] == ["『API』"]


def test_tts_quality_response_does_not_filter_non_reading_categories() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "tts_text_mismatch",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "tts.text"},
                    "excerpt": "・",
                    "issue": "tts.text が元テキストと意味的にずれている可能性があります。",
                    "suggestion": "意味を一致させる",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
    )

    assert [issue.category for issue in response.issues] == ["tts_text_mismatch"]


def test_tts_quality_response_filters_already_corrected_choice_reading_issue() -> None:
    content = {
        "type": "quiz",
        "questions": [
            {
                "id": "q-14",
                "question": "IT部門の役割はどれですか?",
                "choices": ["IT部門", "営業部門", "経理部門", "総務部門"],
                "answerIndex": 0,
                "explanation": "IT部門は情報システムを支えます。",
                "tts": {"choiceTexts": ["アイティー部門", "営業部門", "経理部門", "総務部門"]},
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-14", "field": "choices[0]"},
                    "excerpt": "IT",
                    "issue": "IT はアイティーと読む必要があります。",
                    "suggestion": "アイティー",
                },
            ]
        },
        file_name="quiz_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert response.issues == []


def test_tts_quality_response_keeps_uncorrected_choice_reading_issue() -> None:
    content = {
        "type": "quiz",
        "questions": [
            {
                "id": "q-14",
                "question": "IT部門の役割はどれですか?",
                "choices": ["IT部門", "OS管理", "営業部門", "総務部門"],
                "answerIndex": 0,
                "explanation": "IT部門は情報システムを支えます。",
                "tts": {"choiceTexts": ["アイティー部門", "OS管理", "営業部門", "総務部門"]},
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-14", "field": "choices[0]"},
                    "excerpt": "IT",
                    "issue": "IT はアイティーと読む必要があります。",
                    "suggestion": "アイティー",
                },
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-14", "field": "choices[1]"},
                    "excerpt": "OS",
                    "issue": "OS はオーエスと読む必要があります。",
                    "suggestion": "オーエス",
                },
            ]
        },
        file_name="quiz_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert [issue.excerpt for issue in response.issues] == ["OS"]


def test_tts_quality_response_does_not_filter_reading_by_other_choice_index() -> None:
    content = {
        "type": "quiz",
        "questions": [
            {
                "id": "q-14",
                "question": "IT部門の役割はどれですか?",
                "choices": ["IT部門", "OS管理", "営業部門", "総務部門"],
                "answerIndex": 0,
                "explanation": "IT部門は情報システムを支えます。",
                "tts": {"choiceTexts": ["アイティー部門", "オーエス管理", "営業部門", "総務部門"]},
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-14", "field": "choices[0]"},
                    "excerpt": "IT",
                    "issue": "IT はオーエスと読む必要があります。",
                    "suggestion": "オーエス",
                },
            ]
        },
        file_name="quiz_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert [issue.excerpt for issue in response.issues] == ["IT"]


def test_tts_quality_response_does_not_filter_non_reading_already_corrected_issue() -> None:
    content = {
        "type": "document",
        "documents": [
            {"id": "doc-1", "text": "ITを説明します。", "tts": {"text": "アイティーを説明します。"}}
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "tts_text_mismatch",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "tts.text"},
                    "excerpt": "IT",
                    "issue": "意味のずれを確認してください。",
                    "suggestion": "アイティー",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert [issue.category for issue in response.issues] == ["tts_text_mismatch"]


def test_quality_check_invalid_llm_response_is_error(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
        return {"issues": [{"category": "unknown"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality/text-check", json={"target": target})

    assert response.status_code == 502
    assert "response validation failed" in response.json()["detail"]


def test_quality_check_llm_failure_does_not_fallback_to_mock(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
        raise RuntimeError("temporary failure")

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality/text-check", json={"target": target})

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
        "/quality/text-check",
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
