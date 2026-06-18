import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.gemini_client import GeminiClient
from app.services.pack_paths import pack_root_prefix
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
    prefix = pack_root_prefix(creator_id, content_id)
    target_dir = tmp_path / "generated" / prefix
    (target_dir / "objects" / "audio").mkdir(parents=True, exist_ok=True)
    asset_base = f"http://localhost:8000/generated/{prefix}"
    doc_fv = "fv_20260612_130000_document_doc_01"
    quiz_fv = "fv_20260612_130000_quiz_quiz_01"
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
                "tts": {"audioPath": "objects/audio/av_doc_01__doc-1.mp3"},
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
        "id": "content_fix_manifest_r1",
        "type": "pack_manifest",
        "schemaVersion": 1,
        "contentId": content_id,
        "slug": "content-fix",
        "revision": 1,
        "versionId": version_id,
        "buildId": "build_20260612_130000",
        "generatedAt": "2026-06-12T13:00:00+09:00",
        "creator": {"id": creator_id, "displayName": None},
        "title": "修正テスト",
        "change": {"operation": "initial_generate"},
        "items": [
            {
                "kind": "document",
                "name": "doc_01.json",
                "title": "修正テスト",
                "logicalId": "doc_01",
                "fileVersionId": doc_fv,
                "url": f"{asset_base}/objects/doc/{doc_fv}.json",
            },
            {
                "kind": "quiz",
                "name": "quiz_01.json",
                "title": "修正テストクイズ",
                "logicalId": "quiz_01",
                "fileVersionId": quiz_fv,
                "url": f"{asset_base}/objects/quiz/{quiz_fv}.json",
            },
        ],
    }
    (target_dir / "objects" / "doc").mkdir(parents=True, exist_ok=True)
    (target_dir / "objects" / "quiz").mkdir(parents=True, exist_ok=True)
    (target_dir / "versions" / version_id).mkdir(parents=True, exist_ok=True)
    (target_dir / "objects" / "doc" / f"{doc_fv}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (target_dir / "objects" / "quiz" / f"{quiz_fv}.json").write_text(json.dumps(quiz, ensure_ascii=False), encoding="utf-8")
    (target_dir / "versions" / version_id / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (target_dir / "objects" / "audio" / "av_doc_01__doc-1.mp3").write_bytes(b"mp3")
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
            "suggestion": "エスキューエルとジェイソン",
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
    assert item["tts"]["text"] == "エスキューエルとジェイソンを説明します。ドキュメントによると重要です。"
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
    assert "tts" not in data["finalJson"]["documents"][0]
    assert data["appliedApprovedIds"] == [first["pendingFixes"][0]["id"]]


def test_quality_fix_save_text_fix_commits_changed_file_only(tmp_path, monkeypatch) -> None:
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
    monkeypatch.setattr(
        "app.services.storage_client.StorageClient.copy_prefix",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("copy_prefix must not be called")),
    )

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
    manifest_path = new_dir / "versions" / data["newVersionId"] / "manifest.json"
    assert manifest_path.exists()
    saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved_manifest["schemaVersion"] == 1
    assert saved_manifest["versionId"] == data["newVersionId"]
    assert saved_manifest["revision"] == 2
    assert saved_manifest["sourceVersionId"] == target["versionId"]
    assert saved_manifest["change"]["operation"] == "text_fix"
    assert saved_manifest["change"]["reRecordNeededUnits"] == [
        {"fileName": "doc_01.json", "unitId": "doc-1", "reason": "text_changed"}
    ]
    doc_item = next(item for item in saved_manifest["items"] if item["name"] == "doc_01.json")
    quiz_item = next(item for item in saved_manifest["items"] if item["name"] == "quiz_01.json")
    assert "/objects/doc/" in doc_item["url"]
    assert quiz_item["fileVersionId"] == "fv_20260612_130000_quiz_quiz_01"
    assert quiz_item["url"].endswith("/objects/quiz/fv_20260612_130000_quiz_quiz_01.json")
    saved_doc_path = new_dir / doc_item["url"].split(f"{new_prefix}/", 1)[1]
    saved_doc = json.loads(saved_doc_path.read_text(encoding="utf-8"))
    assert saved_doc["assetBaseUrl"].endswith(f"/{new_prefix}")
    assert saved_doc["documents"][0]["text"] == "SQLとJSONを説明します。重要です。"
    assert "tts" not in saved_doc["documents"][0]
    old_dir = tmp_path / "generated" / pack_root_prefix(target["creatorId"], target["contentId"])
    assert (old_dir / "versions" / target["versionId"] / "manifest.json").exists()
    assert (old_dir / "objects" / "audio" / "av_doc_01__doc-1.mp3").exists()

    packs = client.get("/packs?creatorId=creator_fix").json()["items"]
    assert {item["versionId"] for item in packs if item["contentId"] == "content_fix"} == {data["newVersionId"]}
    assert {item["revision"] for item in packs if item["contentId"] == "content_fix"} == {2}


def test_quality_fix_save_tts_fix_clears_audio_and_preserves_display_text(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    tts_first = client.post("/quality/tts-fix", json={"target": target, "issues": [_issues()[0]]}).json()

    response = client.post(
        "/quality/save-version",
        json={
            "target": target,
            "files": [{"name": "doc_01.json", "kind": "document", "content": tts_first["updatedJson"]}],
            "appliedFixes": tts_first["appliedFixes"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    manifest_path = tmp_path / "generated" / data["storagePrefix"] / "versions" / data["newVersionId"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["change"]["operation"] == "tts_fix"
    assert manifest["change"]["reRecordNeededUnits"] == [
        {"fileName": "doc_01.json", "unitId": "doc-1", "reason": "tts_changed"}
    ]
    doc_item = next(item for item in manifest["items"] if item["name"] == "doc_01.json")
    saved_doc = json.loads(
        (tmp_path / "generated" / data["storagePrefix"] / doc_item["url"].split(f"{data['storagePrefix']}/", 1)[1]).read_text(
            encoding="utf-8"
        )
    )
    assert saved_doc["documents"][0]["text"] == "SQLとJSONを説明します。ドキュメントによると重要です。"
    assert saved_doc["documents"][0]["tts"] == {"text": "エスキューエルとジェイソンを説明します。ドキュメントによると重要です。"}
    old_dir = tmp_path / "generated" / pack_root_prefix(target["creatorId"], target["contentId"])
    assert (old_dir / "objects" / "audio" / "av_doc_01__doc-1.mp3").exists()


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
        "excerpt": "A",
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
        "excerpt": "A",
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


def test_tts_fix_no_llm_replaces_excerpt_without_collapsing_document_text(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    prefix = pack_root_prefix(target["creatorId"], target["contentId"])
    manifest_path = tmp_path / "generated" / prefix / "versions" / target["versionId"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc_item = next(item for item in manifest["items"] if item["name"] == "doc_01.json")
    doc_path = tmp_path / "generated" / prefix / doc_item["url"].split(f"{prefix}/", 1)[1]
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    doc["documents"][0]["text"] = "VRIOフレームワークは、経営資源の強みを評価します。"
    doc["documents"][0]["tts"] = {}
    doc_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    issue = {
        "category": "reading",
        "severity": "medium",
        "confidence": 0.8,
        "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
        "excerpt": "VRIO",
        "issue": "VRIO が誤読される可能性があります。",
        "suggestion": "ブイリオ",
    }

    response = client.post("/quality/tts-fix", json={"target": target, "issues": [issue]})

    assert response.status_code == 200
    data = response.json()
    assert data["unappliedFixes"] == []
    assert data["updatedJson"]["documents"][0]["tts"]["text"] == "ブイリオフレームワークは、経営資源の強みを評価します。"
    assert data["appliedFixes"][0]["before"] == "VRIOフレームワークは、経営資源の強みを評価します。"
    assert data["appliedFixes"][0]["after"] == "ブイリオフレームワークは、経営資源の強みを評価します。"


def test_tts_fix_no_llm_accumulates_multiple_replacements_on_same_document_field(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    prefix = pack_root_prefix(target["creatorId"], target["contentId"])
    manifest_path = tmp_path / "generated" / prefix / "versions" / target["versionId"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc_item = next(item for item in manifest["items"] if item["name"] == "doc_01.json")
    doc_path = tmp_path / "generated" / prefix / doc_item["url"].split(f"{prefix}/", 1)[1]
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    doc["documents"][0]["id"] = "doc-20"
    doc["documents"][0]["text"] = "アイティーILは監査で使われ、最後にアイティーIL 4を確認します。"
    doc["documents"][0]["tts"] = {}
    doc_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    issues = [
        {
            "category": "reading",
            "severity": "medium",
            "confidence": 0.8,
            "location": {"fileName": "doc_01.json", "unitId": "doc-20", "field": "text"},
            "excerpt": "アイティーIL",
            "issue": "冒頭の ITIL 読み補正",
            "suggestion": "アイティル",
        },
        {
            "category": "reading",
            "severity": "medium",
            "confidence": 0.8,
            "location": {"fileName": "doc_01.json", "unitId": "doc-20", "field": "text"},
            "excerpt": "アイティーIL 4",
            "issue": "末尾の ITIL 4 読み補正",
            "suggestion": "アイティル・フォー",
        },
    ]

    response = client.post("/quality/tts-fix", json={"target": target, "issues": issues})

    assert response.status_code == 200
    data = response.json()
    assert data["unappliedFixes"] == []
    assert data["updatedJson"]["documents"][0]["tts"]["text"] == "アイティルは監査で使われ、最後にアイティル・フォーを確認します。"
    assert data["appliedFixes"][0]["after"] == "アイティルは監査で使われ、最後にアイティーIL 4を確認します。"
    assert data["appliedFixes"][1]["before"] == "アイティルは監査で使われ、最後にアイティーIL 4を確認します。"
    assert data["appliedFixes"][1]["after"] == "アイティルは監査で使われ、最後にアイティル・フォーを確認します。"


def test_tts_fix_no_llm_unapplied_when_excerpt_not_found(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    issue = _issues()[0] | {"excerpt": "VRIO", "suggestion": "ブイリオ"}

    response = client.post("/quality/tts-fix", json={"target": target, "issues": [issue]})

    assert response.status_code == 200
    data = response.json()
    assert data["appliedFixes"] == []
    assert len(data["unappliedFixes"]) == 1
    assert "見つからない" in data["unappliedFixes"][0]["reason"]
    assert "text" not in data["updatedJson"]["documents"][0]["tts"]


def test_tts_fix_no_llm_conflicting_later_excerpt_is_unapplied_without_undoing_previous_fix(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    issues = [
        _issues()[0],
        _issues()[0] | {"excerpt": "SQLとJSON", "suggestion": "エスキューエルとジェイソン再修正"},
    ]

    response = client.post("/quality/tts-fix", json={"target": target, "issues": issues})

    assert response.status_code == 200
    data = response.json()
    assert len(data["appliedFixes"]) == 1
    assert len(data["unappliedFixes"]) == 1
    assert "見つからない" in data["unappliedFixes"][0]["reason"]
    assert data["unappliedFixes"][0]["before"] == "エスキューエルとジェイソンを説明します。ドキュメントによると重要です。"
    assert data["updatedJson"]["documents"][0]["tts"]["text"] == "エスキューエルとジェイソンを説明します。ドキュメントによると重要です。"


def test_tts_fix_no_llm_replaces_excerpt_inside_long_choice_text(tmp_path, monkeypatch) -> None:
    target = _write_quiz_version(tmp_path, monkeypatch)
    prefix = pack_root_prefix(target["creatorId"], target["contentId"])
    manifest_path = tmp_path / "generated" / prefix / "versions" / target["versionId"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    quiz_item = next(item for item in manifest["items"] if item["name"] == "quiz_01.json")
    quiz_path = tmp_path / "generated" / prefix / quiz_item["url"].split(f"{prefix}/", 1)[1]
    quiz = json.loads(quiz_path.read_text(encoding="utf-8"))
    quiz["questions"][0]["choices"] = ["SQLを使ってデータを検索する選択肢です。", "B", "C", "D"]
    quiz_path.write_text(json.dumps(quiz, ensure_ascii=False), encoding="utf-8")
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
    assert question["choices"] == ["SQLを使ってデータを検索する選択肢です。", "B", "C", "D"]
    assert question["answerIndex"] == 0
    assert question["tts"]["choiceTexts"] == ["エスキューエルを使ってデータを検索する選択肢です。", "B", "C", "D"]
    assert data["appliedFixes"][0]["after"] == "エスキューエルを使ってデータを検索する選択肢です。"


def test_tts_fix_no_llm_accumulates_multiple_replacements_on_same_choice_index(tmp_path, monkeypatch) -> None:
    target = _write_quiz_version(tmp_path, monkeypatch)
    prefix = pack_root_prefix(target["creatorId"], target["contentId"])
    manifest_path = tmp_path / "generated" / prefix / "versions" / target["versionId"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    quiz_item = next(item for item in manifest["items"] if item["name"] == "quiz_01.json")
    quiz_path = tmp_path / "generated" / prefix / quiz_item["url"].split(f"{prefix}/", 1)[1]
    quiz = json.loads(quiz_path.read_text(encoding="utf-8"))
    quiz["questions"][0]["choices"] = ["SQLとJSONを使う選択肢です。", "B", "C", "D"]
    quiz_path.write_text(json.dumps(quiz, ensure_ascii=False), encoding="utf-8")
    issues = [
        {
            "category": "reading",
            "severity": "medium",
            "confidence": 0.8,
            "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "tts.choiceTexts[0]"},
            "excerpt": "SQL",
            "issue": "SQL reading",
            "suggestion": "エスキューエル",
        },
        {
            "category": "reading",
            "severity": "medium",
            "confidence": 0.8,
            "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "tts.choiceTexts[0]"},
            "excerpt": "JSON",
            "issue": "JSON reading",
            "suggestion": "ジェイソン",
        },
    ]

    response = client.post("/quality/tts-fix", json={"target": target, "issues": issues})

    assert response.status_code == 200
    data = response.json()
    question = data["updatedJson"]["questions"][0]
    assert question["choices"] == ["SQLとJSONを使う選択肢です。", "B", "C", "D"]
    assert question["answerIndex"] == 0
    assert question["tts"]["choiceTexts"] == ["エスキューエルとジェイソンを使う選択肢です。", "B", "C", "D"]
    assert len(data["appliedFixes"]) == 2
    assert data["unappliedFixes"] == []


def test_tts_fix_no_llm_replaces_excerpt_in_quiz_question_and_explanation(tmp_path, monkeypatch) -> None:
    target = _write_quiz_version(tmp_path, monkeypatch)
    issues = [
        {
            "category": "reading",
            "severity": "medium",
            "confidence": 0.8,
            "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "question"},
            "excerpt": "SQL",
            "issue": "question reading",
            "suggestion": "エスキューエル",
        },
        {
            "category": "reading",
            "severity": "medium",
            "confidence": 0.8,
            "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "explanation"},
            "excerpt": "SQL",
            "issue": "explanation reading",
            "suggestion": "エスキューエル",
        },
    ]

    response = client.post("/quality/tts-fix", json={"target": target, "issues": issues})

    assert response.status_code == 200
    data = response.json()
    tts = data["updatedJson"]["questions"][0]["tts"]
    assert tts["questionText"] == "エスキューエルとは何ですか？"
    assert tts["explanationText"] == "エスキューエルの説明です。"
    assert len(data["appliedFixes"]) == 2


def test_tts_fix_no_llm_matches_normalized_excerpt_without_normalizing_output(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    prefix = pack_root_prefix(target["creatorId"], target["contentId"])
    manifest_path = tmp_path / "generated" / prefix / "versions" / target["versionId"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc_item = next(item for item in manifest["items"] if item["name"] == "doc_01.json")
    doc_path = tmp_path / "generated" / prefix / doc_item["url"].split(f"{prefix}/", 1)[1]
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    doc["documents"][0]["text"] = "[en-US]VRIO　フレームワークは、４Ｐを確認します。"
    doc["documents"][0]["tts"] = {}
    doc_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    issue = {
        "category": "reading",
        "severity": "medium",
        "confidence": 0.8,
        "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
        "excerpt": "VRIO フレームワークは 4P",
        "issue": "VRIO と 4P の読み補正",
        "suggestion": "ブイリオフレームワークはフォーピー",
    }

    response = client.post("/quality/tts-fix", json={"target": target, "issues": [issue]})

    assert response.status_code == 200
    data = response.json()
    assert data["unappliedFixes"] == []
    assert data["updatedJson"]["documents"][0]["tts"]["text"] == "[en-US]ブイリオフレームワークはフォーピーを確認します。"


def test_tts_fix_no_llm_rejects_clear_vocabulary_rewrite(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    prefix = pack_root_prefix(target["creatorId"], target["contentId"])
    manifest_path = tmp_path / "generated" / prefix / "versions" / target["versionId"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc_item = next(item for item in manifest["items"] if item["name"] == "doc_01.json")
    doc_path = tmp_path / "generated" / prefix / doc_item["url"].split(f"{prefix}/", 1)[1]
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    doc["documents"][0]["text"] = "有線LANはケーブルを使って通信します。"
    doc["documents"][0].pop("tts", None)
    doc_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    issue = {
        "category": "reading",
        "severity": "medium",
        "confidence": 0.8,
        "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
        "excerpt": "有線LAN",
        "issue": "有線LAN の読み補正",
        "suggestion": "LANケーブル",
    }

    response = client.post("/quality/tts-fix", json={"target": target, "issues": [issue]})

    assert response.status_code == 200
    data = response.json()
    assert data["appliedFixes"] == []
    assert len(data["unappliedFixes"]) == 1
    assert "語彙変更" in data["unappliedFixes"][0]["reason"]
    assert "text" not in (data["updatedJson"]["documents"][0].get("tts") or {})


def test_tts_fix_no_llm_allows_pronunciation_for_mixed_japanese_term(tmp_path, monkeypatch) -> None:
    target = _write_version(tmp_path, monkeypatch)
    prefix = pack_root_prefix(target["creatorId"], target["contentId"])
    manifest_path = tmp_path / "generated" / prefix / "versions" / target["versionId"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc_item = next(item for item in manifest["items"] if item["name"] == "doc_01.json")
    doc_path = tmp_path / "generated" / prefix / doc_item["url"].split(f"{prefix}/", 1)[1]
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    doc["documents"][0]["text"] = "有線LANはケーブルを使って通信します。"
    doc["documents"][0].pop("tts", None)
    doc_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    issue = {
        "category": "reading",
        "severity": "medium",
        "confidence": 0.8,
        "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
        "excerpt": "有線LAN",
        "issue": "有線LAN の読み補正",
        "suggestion": "ゆうせんラン",
    }

    response = client.post("/quality/tts-fix", json={"target": target, "issues": [issue]})

    assert response.status_code == 200
    data = response.json()
    assert data["unappliedFixes"] == []
    assert data["updatedJson"]["documents"][0]["tts"]["text"] == "ゆうせんランはケーブルを使って通信します。"


def test_tts_fix_no_llm_resolves_generic_choices_field_by_excerpt(tmp_path, monkeypatch) -> None:
    target = _write_quiz_version(tmp_path, monkeypatch)
    prefix = pack_root_prefix(target["creatorId"], target["contentId"])
    manifest_path = tmp_path / "generated" / prefix / "versions" / target["versionId"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    quiz_item = next(item for item in manifest["items"] if item["name"] == "quiz_01.json")
    quiz_path = tmp_path / "generated" / prefix / quiz_item["url"].split(f"{prefix}/", 1)[1]
    quiz = json.loads(quiz_path.read_text(encoding="utf-8"))
    quiz["questions"][0]["choices"] = ["A", "SQL を使う選択肢", "JSON", "D"]
    quiz_path.write_text(json.dumps(quiz, ensure_ascii=False), encoding="utf-8")
    issue = {
        "category": "reading",
        "severity": "medium",
        "confidence": 0.8,
        "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "choices"},
        "excerpt": "sqlを使う",
        "issue": "choice reading without explicit index",
        "suggestion": "エスキューエルを使う",
    }

    response = client.post("/quality/tts-fix", json={"target": target, "issues": [issue]})

    assert response.status_code == 200
    data = response.json()
    question = data["updatedJson"]["questions"][0]
    assert question["choices"] == ["A", "SQL を使う選択肢", "JSON", "D"]
    assert question["answerIndex"] == 0
    assert question["tts"]["choiceTexts"] == ["A", "エスキューエルを使う選択肢", "JSON", "D"]
    assert data["appliedFixes"][0]["location"]["field"] == "tts.choiceTexts[1]"
    assert data["unappliedFixes"] == []


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
    prefix = pack_root_prefix(target["creatorId"], target["contentId"])
    manifest_path = tmp_path / "generated" / prefix / "versions" / target["versionId"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    quiz_item = next(item for item in manifest["items"] if item["name"] == "quiz_01.json")
    quiz_path = tmp_path / "generated" / prefix / quiz_item["url"].split(f"{prefix}/", 1)[1]
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
        "excerpt": "A",
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
