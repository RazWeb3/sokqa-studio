from fastapi.testclient import TestClient
import io
import zipfile

from main import app
from app.schemas.common import TtsRule
from app.schemas.sokqa import GeneratedFile
from app.services.pack_revision_tools import apply_tts_replacement_rules


client = TestClient(app)


def test_tts_replacement_patch_preserves_unrelated_existing_quiz_tts() -> None:
    file = GeneratedFile(
        name="quiz.json",
        kind="quiz",
        content={
            "id": "quiz",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "Quiz",
            "questions": [
                {
                    "id": "q-1",
                    "question": "CSRの説明はどれですか?",
                    "choices": ["説明A", "説明B", "説明C", "説明D"],
                    "answerIndex": 0,
                    "explanation": "CSRは企業の社会的責任です。",
                    "tts": {
                        "questionText": "シーエスアールの説明はどれですか?",
                        "choiceTexts": ["", "ビー", "", ""],
                        "explanationText": "シーエスアールは企業の社会的責任です。",
                    },
                },
                {
                    "id": "q-2",
                    "question": "ガバナンスの説明はどれですか?",
                    "choices": ["ガバナンス", "統制", "監査", "会計"],
                    "answerIndex": 0,
                    "explanation": "ガバナンスは統治の仕組みです。",
                },
            ],
        },
    )

    revised = apply_tts_replacement_rules([file], [TtsRule(source="ガバナンス", reading="ガバガバ")])

    assert len(revised) == 1
    questions = revised[0].content["questions"]
    assert questions[0]["tts"] == file.content["questions"][0]["tts"]
    assert questions[1]["tts"] == {
        "questionText": "ガバガバの説明はどれですか?",
        "choiceTexts": ["ガバガバ", "", "", ""],
        "explanationText": "ガバガバは統治の仕組みです。",
    }


def test_tts_replacement_patch_updates_existing_tts_and_clears_only_changed_audio() -> None:
    file = GeneratedFile(
        name="quiz.json",
        kind="quiz",
        content={
            "id": "quiz",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "Quiz",
            "questions": [
                {
                    "id": "q-1",
                    "question": "ガバナンスの説明はどれですか?",
                    "choices": ["ガバナンス", "統制", "監査", "会計"],
                    "answerIndex": 0,
                    "explanation": "ガバナンスは統治の仕組みです。",
                    "tts": {
                        "questionText": "ガバナンスの説明はどれですか?",
                        "questionAudioPath": "audio/question.mp3",
                        "choiceTexts": ["ガバナンス", "とうせい", "", ""],
                        "choiceAudioPaths": ["audio/choice0.mp3", "audio/choice1.mp3", None, None],
                        "explanationText": "ガバナンスは統治の仕組みです。",
                        "explanationAudioPath": "audio/explanation.mp3",
                    },
                }
            ],
        },
    )

    revised = apply_tts_replacement_rules([file], [TtsRule(source="ガバナンス", reading="ガバガバ")])

    tts = revised[0].content["questions"][0]["tts"]
    assert tts["questionText"] == "ガバガバの説明はどれですか?"
    assert "questionAudioPath" not in tts
    assert tts["choiceTexts"] == ["ガバガバ", "とうせい", "", ""]
    assert tts["choiceAudioPaths"] == [None, "audio/choice1.mp3", None, None]
    assert tts["explanationText"] == "ガバガバは統治の仕組みです。"
    assert "explanationAudioPath" not in tts


def test_tts_replacement_patch_leaves_nonmatching_file_unchanged() -> None:
    file = GeneratedFile(
        name="doc.json",
        kind="document",
        content={
            "id": "doc",
            "type": "document",
            "schemaVersion": 1,
            "title": "Document",
            "documents": [
                {
                    "id": "doc-1",
                    "text": "CSRの説明です。",
                    "tts": {"text": "シーエスアールの説明です。", "audioPath": "audio/doc.mp3"},
                }
            ],
        },
    )

    revised = apply_tts_replacement_rules([file], [TtsRule(source="ガバナンス", reading="ガバガバ")])

    assert revised == []


def test_tts_revision_creates_new_manifest_revision() -> None:
    plan_response = client.post(
        "/plan-pack",
        json={
            "theme": "日本橋の読み",
            "targetUser": "学習者",
            "scale": "quick",
            "docCount": 1,
            "quizCount": 1,
            "sourceText": "日本橋の読みを確認します。",
            "ttsReadingMode": "none",
        },
    )
    generated_response = client.post(
        "/generate-pack",
        json={
            "plan": plan_response.json(),
            "persist": False,
        },
    )
    generated = generated_response.json()

    revised_response = client.post(
        "/debug/revise-tts",
        json={
            "jobId": generated["jobId"],
            "persist": False,
            "ttsRules": [
                {
                    "source": "日本橋",
                    "reading": "にほんばし",
                    "note": "地名として読む場合",
                }
            ],
        },
    )

    assert revised_response.status_code == 200
    revised = revised_response.json()
    assert revised["manifest"]["schemaVersion"] == 1
    assert revised["manifest"]["revision"] == generated["manifest"]["revision"] + 1
    assert revised["manifest"]["versionId"] != generated["manifest"]["versionId"]
    assert revised["manifest"]["sourceVersionId"] == generated["manifest"]["versionId"]
    assert revised["manifest"]["change"]["operation"] == "tts_fix"
    assert revised["validation"]["valid"] is True


def test_pack_tts_revision_applies_to_manifest_and_increments_once(tmp_path, monkeypatch) -> None:
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://127.0.0.1:8000/generated")
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan_response = client.post(
        "/plan-pack",
        json={
            "theme": "日本橋の読み",
            "targetUser": "学習者",
            "scale": "quick",
            "documentCount": 1,
            "quizCount": 1,
            "sourceText": "日本橋の読みを確認します。",
            "includeTts": False,
        },
    )
    generated_response = client.post("/generate-pack", json={"plan": plan_response.json(), "persist": True})
    generated = generated_response.json()
    manifest = generated["manifest"]

    revised_response = client.post(
        "/packs/revise-tts",
        json={
            "target": {
                "creatorId": manifest["creator"]["id"],
                "contentId": manifest["contentId"],
                "versionId": manifest["versionId"],
            },
            "ttsRules": [{"source": "日本橋", "reading": "にほんばし"}],
            "persist": True,
        },
    )

    assert revised_response.status_code == 200
    revised = revised_response.json()
    assert revised["manifest"]["revision"] == manifest["revision"] + 1
    assert revised["manifest"]["sourceVersionId"] == manifest["versionId"]
    assert revised["manifest"]["change"]["operation"] == "tts_fix"
    assert len(revised["manifest"]["change"]["changedFiles"]) == len(manifest["items"])
    document_file = next(file for file in revised["files"] if file["kind"] == "document")
    assert any("にほんばし" in item.get("tts", {}).get("text", "") for item in document_file["content"]["documents"])


def test_pack_json_zip_exports_latest_manifest_json_only(tmp_path, monkeypatch) -> None:
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://127.0.0.1:8000/generated")
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan_response = client.post(
        "/plan-pack",
        json={
            "theme": "Zip Export",
            "targetUser": "Learners",
            "generationUnit": "pack",
            "docCount": 1,
            "quizCount": 1,
            "includeTts": False,
        },
    )
    generated_response = client.post("/generate-pack", json={"plan": plan_response.json(), "persist": True})
    manifest = generated_response.json()["manifest"]

    export_response = client.post(
        "/packs/export-json-zip",
        json={
            "creatorId": manifest["creator"]["id"],
            "contentId": manifest["contentId"],
            "versionId": manifest["versionId"],
        },
    )

    assert export_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(export_response.content)) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert {item["name"] for item in manifest["items"]}.issubset(names)
        assert not any(name.endswith(".mp3") for name in names)
        exported_manifest = archive.read("manifest.json").decode("utf-8")
        assert manifest["versionId"] in exported_manifest
