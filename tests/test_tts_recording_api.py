from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.pack_metadata import pack_storage_prefix
from app.services.tts_recorder import RecordingResult, RecordingSummary
from main import app


client = TestClient(app)


def _quiz_content() -> dict:
    return {
        "id": "quiz-pack",
        "type": "quiz",
        "schemaVersion": 1,
        "title": "録音APIクイズ",
        "questions": [
            {
                "id": "q-1",
                "question": "AI の説明はどれですか?",
                "choices": ["人工知能", "会計", "在庫", "販売"],
                "answerIndex": 0,
                "explanation": "AI は人工知能です。",
                "tts": {
                    "questionText": "エーアイ の説明はどれですか?",
                    "choiceTexts": ["人工知能", "会計", "在庫", "販売"],
                    "explanationText": "エーアイ は人工知能です。",
                    "questionAudioUrl": "https://cdn.example.test/already.mp3",
                },
            }
        ],
    }


def _write_pack(tmp_path: Path, monkeypatch) -> tuple[str, str]:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")
    monkeypatch.setattr(settings, "tts_credit_per_char", 0.0001)
    monkeypatch.setattr(settings, "cloud_tts_recording_request_max_units", 2)

    prefix = pack_storage_prefix("creator_test", "content_test", "v20260607_120000")
    target_dir = tmp_path / "generated" / prefix
    target_dir.mkdir(parents=True)
    pack_path = target_dir / "quiz.json"
    pack_path.write_text(__import__("json").dumps(_quiz_content(), ensure_ascii=False), encoding="utf-8")
    return prefix, "quiz.json"


def _target(pack_name: str) -> dict:
    return {
        "creatorId": "creator_test",
        "contentId": "content_test",
        "versionId": "v20260607_120000",
        "packName": pack_name,
    }


def test_recording_estimate_returns_unrecorded_units_and_credits(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)

    response = client.post(
        "/tts/recording-estimate",
        json={"target": _target(pack_name)},
    )

    assert response.status_code == 200
    data = response.json()
    unit_ids = [unit["itemId"] for unit in data["units"]]

    assert data["packId"] == "quiz-pack"
    assert data["unitCount"] == 5
    assert "q_q-1_question" not in unit_ids
    assert "q_q-1_choice_0" in unit_ids
    assert data["totalChars"] == sum(unit["charCount"] for unit in data["units"])
    assert data["estimatedCredits"] == data["totalChars"] * 0.0001


def test_recording_endpoint_records_only_requested_units_and_skips_recorded(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)
    captured = {}

    def fake_record_generated_file_audio(file, units, storage_prefix, **kwargs):
        captured["unitIds"] = [unit.item_id for unit in units]
        return RecordingSummary(
            total_units=len(units),
            skipped_units=1,
            success_count=1,
            failure_count=0,
            failed_unit_ids=[],
            results=[
                RecordingResult(unit_id="q_q-1_question", success=False, error=None),
                RecordingResult(
                    unit_id="q_q-1_choice_0",
                    success=True,
                    audio_url=f"https://cdn.example.test/{storage_prefix}/audio/q_q-1_choice_0.mp3",
                ),
            ],
        )

    monkeypatch.setattr("app.services.tts_recording_api.record_generated_file_audio", fake_record_generated_file_audio)

    response = client.post(
        "/tts/record",
        json={
            "target": _target(pack_name),
            "unitIds": ["q_q-1_question", "q_q-1_choice_0"],
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert captured["unitIds"] == ["q_q-1_question", "q_q-1_choice_0"]
    assert data["summary"]["skippedUnits"] == 1
    assert data["summary"]["successCount"] == 1
    assert data["audioUrls"] == [
        {
            "unitId": "q_q-1_choice_0",
            "audioUrl": f"https://cdn.example.test/{data['storagePrefix']}/audio/q_q-1_choice_0.mp3",
        }
    ]


def test_recording_endpoint_rejects_too_many_units(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)
    monkeypatch.setattr(get_settings(), "cloud_tts_recording_request_max_units", 1)

    response = client.post(
        "/tts/record",
        json={
            "target": _target(pack_name),
            "unitIds": ["q_q-1_choice_0", "q_q-1_choice_1"],
        },
    )

    assert response.status_code == 400
    assert "per-request limit" in response.json()["detail"]


def test_recording_endpoint_returns_partial_failure_summary(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)

    def fake_record_generated_file_audio(file, units, storage_prefix, **kwargs):
        return RecordingSummary(
            total_units=len(units),
            skipped_units=0,
            success_count=1,
            failure_count=1,
            failed_unit_ids=["q_q-1_choice_1"],
            results=[
                RecordingResult(
                    unit_id="q_q-1_choice_0",
                    success=True,
                    audio_url=f"https://cdn.example.test/{storage_prefix}/audio/q_q-1_choice_0.mp3",
                ),
                RecordingResult(unit_id="q_q-1_choice_1", success=False, error="synthetic failure"),
            ],
        )

    monkeypatch.setattr("app.services.tts_recording_api.record_generated_file_audio", fake_record_generated_file_audio)

    response = client.post(
        "/tts/record",
        json={
            "target": _target(pack_name),
            "unitIds": ["q_q-1_choice_0", "q_q-1_choice_1"],
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["summary"]["successCount"] == 1
    assert data["summary"]["failureCount"] == 1
    assert data["summary"]["failedUnitIds"] == ["q_q-1_choice_1"]
    assert data["summary"]["results"][1]["error"] == "synthetic failure"
