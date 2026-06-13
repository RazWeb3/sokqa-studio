import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.gemini_client import GeminiClient
from app.services.pack_metadata import pack_storage_prefix
from main import app


client = TestClient(app)


def _write_version(tmp_path: Path, monkeypatch) -> dict:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    creator_id = "creator_fix"
    content_id = "content_fix"
    version_id = "v20260612_130000"
    prefix = pack_storage_prefix(creator_id, content_id, version_id)
    target_dir = tmp_path / "generated" / prefix
    (target_dir / "audio").mkdir(parents=True, exist_ok=True)
    asset_base = f"http://localhost:8000/generated/{prefix}"
    doc = {
        "id": "content_fix_doc_01",
        "type": "document",
        "schemaVersion": 1,
        "title": "修正テスト",
        "language": "ja",
        "assetBaseUrl": asset_base,
        "documents": [
            {
                "id": "doc-1",
                "text": "SQLとJSONを説明します。ドキュメントによると重要です。",
                "tts": {"audioPath": "audio/doc_01__doc-1.mp3"},
            }
        ],
    }
    quiz = {
        "id": "content_fix_quiz_01",
        "type": "quiz",
        "schemaVersion": 1,
        "title": "修正テストクイズ",
        "language": "ja",
        "assetBaseUrl": asset_base,
        "questions": [
            {
                "id": "q-1",
                "question": "SQLとは何ですか？",
                "choices": ["A", "B", "C", "D"],
                "answerIndex": 0,
                "explanation": "SQLの説明です。",
            }
        ],
    }
    manifest = {
        "id": "content_fix_manifest",
        "type": "pack_manifest",
        "schemaVersion": 1,
        "contentId": content_id,
        "slug": "content-fix",
        "versionId": version_id,
        "buildId": "build_20260612_130000",
        "generatedAt": "2026-06-12T13:00:00+09:00",
        "creator": {"id": creator_id, "displayName": None},
        "title": "修正テスト",
        "items": [
            {"kind": "document", "url": f"{asset_base}/doc_01.json"},
            {"kind": "quiz", "url": f"{asset_base}/quiz_01.json"},
        ],
    }
    (target_dir / "doc_01.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (target_dir / "quiz_01.json").write_text(json.dumps(quiz, ensure_ascii=False), encoding="utf-8")
    (target_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (target_dir / "audio" / "doc_01__doc-1.mp3").write_bytes(b"mp3")
    return {
        "creatorId": creator_id,
        "contentId": content_id,
        "versionId": version_id,
        "packName": "doc_01.json",
        "kind": "document",
    }


def _write_quiz_version(tmp_path: Path, monkeypatch) -> dict:
    target = _write_version(tmp_path, monkeypatch)
    target["packName"] = "quiz_01.json"
    target["kind"] = "quiz"
    return target


def _issues() -> list[dict]:
    return [
        {
            "category": "reading",
            "severity": "medium",
            "confidence": 0.8,
            "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
            "excerpt": "SQLとJSON",
            "issue": "略語が誤読される可能性があります。",
            "suggestion": "エスキューエルとジェイソンを説明します。",
        },
        {
            "category": "style",
            "severity": "medium",
            "confidence": 0.9,
            "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
            "excerpt": "ドキュメントによると",
            "issue": "伝聞調です。",
            "suggestion": "SQLとJSONを説明します。重要です。",
        },
    ]


def test_tts_fix_mock_applies_only_tts_and_keeps_text_unchanged(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)

    def fail_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        raise AssertionError("LLM must not be called by no-LLM tts-fix")

    monkeypatch.setattr(GeminiClient, "generate_json", fail_generate_json)

    response = client.post("/quality/tts-fix", json={"target": target, "issues": _issues()})

    assert response.status_code == 200
    data = response.json()
    item = data["updatedJson"]["documents"][0]
    assert item["text"] == "SQLとJSONを説明します。ドキュメントによると重要です。"
    assert item["tts"]["text"] == "エスキューエルとジェイソンを説明します。"
    assert len(data["appliedFixes"]) == 1
    assert data["appliedFixes"][0]["field"] == "tts.text"
    assert data["pendingFixes"] == []
    assert data["unappliedFixes"] == []
    assert data["reRecordNeededUnits"][0]["unitId"] == "doc-1"


def test_text_fix_mock_returns_pending_without_auto_apply(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)

    response = client.post("/quality/text-fix", json={"target": target, "issues": _issues()})

    assert response.status_code == 200
    data = response.json()
    item = data["updatedJson"]["documents"][0]
    assert item["text"] == "SQLとJSONを説明します。ドキュメントによると重要です。"
    assert "text" not in item["tts"]
    assert data["appliedFixes"] == []
    assert len(data["pendingFixes"]) == 1
    assert data["pendingFixes"][0]["category"] == "style"


def test_quality_fix_apply_uses_only_approved_pending_fixes_without_llm(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    first = client.post("/quality/text-fix", json={"target": target, "issues": _issues()}).json()

    def fail_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        raise AssertionError("LLM must not be called while applying approved fixes")

    monkeypatch.setattr(GeminiClient, "generate_json", fail_generate_json)

    response = client.post(
        "/quality/text-fix/apply",
        json={
            "updatedJson": first["updatedJson"],
            "pendingFixes": first["pendingFixes"],
            "approvedIds": [first["pendingFixes"][0]["id"]],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["finalJson"]["documents"][0]["text"] == "SQLとJSONを説明します。重要です。"
    assert data["finalJson"]["documents"][0]["tts"] == {"ttsNeedsRefresh": True}
    assert data["appliedApprovedIds"] == [first["pendingFixes"][0]["id"]]


def test_quality_fix_save_creates_complete_new_version_snapshot(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    tts_first = client.post("/quality/tts-fix", json={"target": target, "issues": _issues()}).json()
    first = client.post("/quality/text-fix", json={"target": target, "issues": _issues()}).json()
    applied = client.post(
        "/quality/text-fix/apply",
        json={
            "updatedJson": first["updatedJson"],
            "pendingFixes": first["pendingFixes"],
            "approvedIds": [first["pendingFixes"][0]["id"]],
        },
    ).json()

    response = client.post(
        "/quality/save-version",
        json={
            "target": target,
            "files": [{"name": "doc_01.json", "kind": "document", "content": applied["finalJson"]}],
            "appliedFixes": tts_first["appliedFixes"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["newVersionId"] != target["versionId"]
    assert data["reRecordNeededUnits"][0]["unitId"] == "doc-1"
    new_prefix = data["storagePrefix"]
    new_dir = tmp_path / "generated" / new_prefix
    assert (new_dir / "manifest.json").exists()
    assert (new_dir / "doc_01.json").exists()
    assert (new_dir / "quiz_01.json").exists()
    assert (new_dir / "audio" / "doc_01__doc-1.mp3").exists()
    saved_doc = json.loads((new_dir / "doc_01.json").read_text(encoding="utf-8"))
    assert saved_doc["assetBaseUrl"].endswith(f"/{new_prefix}")
    saved_manifest = json.loads((new_dir / "manifest.json").read_text(encoding="utf-8"))
    assert saved_manifest["versionId"] == data["newVersionId"]
    assert all(data["newVersionId"] in item["url"] for item in saved_manifest["items"])
    old_dir = tmp_path / "generated" / pack_storage_prefix(target["creatorId"], target["contentId"], target["versionId"])
    assert (old_dir / "manifest.json").exists()

    packs = client.get("/packs?creatorId=creator_fix").json()["items"]
    assert {item["versionId"] for item in packs if item["contentId"] == "content_fix"} == {data["newVersionId"]}


def test_quality_fix_invalid_llm_response_is_error(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {"appliedFixes": []}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality/tts-fix/llm", json={"target": target, "issues": _issues()})

    assert response.status_code == 502
    assert "response validation failed" in response.json()["detail"]


def test_quality_fix_fills_before_and_skips_incomplete_llm_fixes(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "updatedJson": {"type": "document", "documents": []},
            "appliedFixes": [
                {
                    "id": "auto-valid",
                    "category": "reading",
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "field": "tts.text",
                    "after": "エスキューエルとジェイソンを説明します。",
                    "sourceIssue": "略語が誤読される可能性があります。",
                },
                {
                    "id": "auto-missing-after",
                    "category": "reading",
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "field": "tts.text",
                    "sourceIssue": "after がありません。",
                },
                {
                    "id": "auto-bad-location",
                    "category": "reading",
                    "location": {"fileName": "doc_01.json", "unitId": "doc-404", "field": "text"},
                    "field": "tts.text",
                    "after": "これは使われません。",
                    "sourceIssue": "location が不正です。",
                },
            ],
            "pendingFixes": [
                {
                    "id": "pending-valid",
                    "category": "style",
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "field": "text",
                    "suggestedAfter": "SQLとJSONを説明します。重要です。",
                    "reason": "伝聞調です。",
                    "sourceIssue": "伝聞調です。",
                },
                {
                    "id": "pending-missing-after",
                    "category": "style",
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "field": "text",
                    "reason": "suggestedAfter がありません。",
                    "sourceIssue": "伝聞調です。",
                },
                {
                    "id": "pending-bad-location",
                    "category": "style",
                    "location": {"fileName": "doc_01.json", "unitId": "doc-404", "field": "text"},
                    "field": "text",
                    "suggestedAfter": "これは使われません。",
                    "reason": "location が不正です。",
                    "sourceIssue": "伝聞調です。",
                },
            ],
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality/tts-fix/llm", json={"target": target, "issues": _issues()})

    assert response.status_code == 200
    data = response.json()
    assert [fix["id"] for fix in data["appliedFixes"]] == ["auto-valid"]
    assert [fix["id"] for fix in data["pendingFixes"]] == ["pending-valid"]
    assert data["appliedFixes"][0]["before"] == "SQLとJSONを説明します。ドキュメントによると重要です。"
    assert data["pendingFixes"][0]["before"] == "SQLとJSONを説明します。ドキュメントによると重要です。"
    item = data["updatedJson"]["documents"][0]
    assert item["text"] == "SQLとJSONを説明します。ドキュメントによると重要です。"
    assert item["tts"]["text"] == "エスキューエルとジェイソンを説明します。"


def test_tts_fix_choice_texts_array_after_preserves_index_mapping(tmp_path, monkeypatch) -> None:
    target = _write_quiz_version(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "updatedJson": {"ignored": True},
            "appliedFixes": [
                {
                    "id": "auto-choice-0",
                    "category": "reading",
                    "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "tts.choiceTexts[0]"},
                    "field": "tts.choiceTexts[0]",
                    "after": ["エスキューエル", "ビー", "シー", "ディー"],
                    "sourceIssue": "choice 0 reading",
                }
            ],
            "pendingFixes": [],
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    issue = {
        "category": "reading",
        "severity": "medium",
        "confidence": 0.8,
        "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "tts.choiceTexts[0]"},
        "excerpt": "SQL",
        "issue": "choice 0 reading",
        "suggestion": "エスキューエル",
    }

    response = client.post("/quality/tts-fix/llm", json={"target": target, "issues": [issue]})

    assert response.status_code == 200
    data = response.json()
    question = data["updatedJson"]["questions"][0]
    assert question["choices"] == ["A", "B", "C", "D"]
    assert question["answerIndex"] == 0
    assert question["tts"]["choiceTexts"] == ["エスキューエル", "B", "C", "D"]
    assert all(isinstance(item, str) for item in question["tts"]["choiceTexts"])
    assert not any(isinstance(item, list) for item in question["tts"]["choiceTexts"])
    assert data["appliedFixes"][0]["after"] == "エスキューエル"


def test_tts_fix_no_llm_choice_texts_suggestion_preserves_index_mapping(tmp_path, monkeypatch) -> None:
    target = _write_quiz_version(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fail_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        raise AssertionError("LLM must not be called by no-LLM tts-fix")

    monkeypatch.setattr(GeminiClient, "generate_json", fail_generate_json)
    issue = {
        "category": "reading",
        "severity": "medium",
        "confidence": 0.8,
        "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "tts.choiceTexts[0]"},
        "excerpt": "SQL",
        "issue": "choice 0 reading",
        "suggestion": "エスキューエル",
    }

    response = client.post("/quality/tts-fix", json={"target": target, "issues": [issue]})

    assert response.status_code == 200
    data = response.json()
    question = data["updatedJson"]["questions"][0]
    assert question["choices"] == ["A", "B", "C", "D"]
    assert question["answerIndex"] == 0
    assert question["tts"]["choiceTexts"] == ["エスキューエル", "B", "C", "D"]
    assert all(isinstance(item, str) for item in question["tts"]["choiceTexts"])
    assert not any(isinstance(item, list) for item in question["tts"]["choiceTexts"])
    assert data["appliedFixes"][0]["after"] == "エスキューエル"
    assert data["reRecordNeededUnits"] == [
        {"fileName": "quiz_01.json", "unitId": "q-1", "field": "tts.choiceTexts[0]", "category": "reading"}
    ]


def test_tts_fix_no_llm_unapplied_for_non_applicable_suggestion(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    issue = _issues()[0] | {"suggestion": "必要ならTTS補正で読みを指定します。"}

    response = client.post("/quality/tts-fix", json={"target": target, "issues": [issue]})

    assert response.status_code == 200
    data = response.json()
    assert data["appliedFixes"] == []
    assert len(data["unappliedFixes"]) == 1
    assert "適用可能" in data["unappliedFixes"][0]["reason"]
    assert "text" not in data["updatedJson"]["documents"][0]["tts"]


def test_tts_fix_empty_llm_response_returns_friendly_error_after_retries(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")
    calls = {"count": 0}

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        calls["count"] += 1
        raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality/tts-fix/llm", json={"target": target, "issues": [_issues()[0]]})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "修正案を生成できませんでした" in detail
    assert "Expecting value" not in detail
    assert calls["count"] == 3


def test_tts_fix_rejects_nested_choice_texts_before_llm_call(tmp_path, monkeypatch) -> None:
    target = _write_quiz_version(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")
    prefix = pack_storage_prefix(target["creatorId"], target["contentId"], target["versionId"])
    quiz_path = tmp_path / "generated" / prefix / "quiz_01.json"
    quiz = json.loads(quiz_path.read_text(encoding="utf-8"))
    quiz["questions"][0]["tts"] = {"choiceTexts": [["nested"], "B", "C", "D"]}
    quiz_path.write_text(json.dumps(quiz, ensure_ascii=False), encoding="utf-8")

    def fail_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        raise AssertionError("LLM must not be called for invalid choiceTexts")

    monkeypatch.setattr(GeminiClient, "generate_json", fail_generate_json)
    issue = {
        "category": "reading",
        "severity": "medium",
        "confidence": 0.8,
        "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "tts.choiceTexts[0]"},
        "excerpt": "SQL",
        "issue": "choice 0 reading",
        "suggestion": "エスキューエル",
    }

    response = client.post("/quality/tts-fix", json={"target": target, "issues": [issue]})

    assert response.status_code == 400
    assert "choiceTexts" in response.json()["detail"]


def test_quality_fix_missing_pack_returns_404(tmp_path, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    response = client.post(
        "/quality/tts-fix",
        json={
            "target": {
                "creatorId": "creator_missing",
                "contentId": "content_missing",
                "versionId": "v20260612_130000",
                "packName": "doc_01.json",
                "kind": "document",
            },
            "issues": _issues(),
        },
    )

    assert response.status_code == 404
