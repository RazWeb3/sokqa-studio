import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.pack_paths import pack_root_prefix
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

    prefix = pack_root_prefix("creator_test", "content_test")
    target_dir = tmp_path / "generated" / prefix
    target_dir.mkdir(parents=True)
    file_version_id = "fv_20260607_120000_quiz_quiz_01"
    content = _quiz_content()
    content["assetBaseUrl"] = f"http://localhost:8000/generated/{prefix}"
    object_path = target_dir / "objects" / "quiz" / f"{file_version_id}.json"
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "id": "content_test_manifest_r1",
        "type": "pack_manifest",
        "schemaVersion": 1,
        "contentId": "content_test",
        "slug": "content-test",
        "revision": 1,
        "versionId": "v20260607_120000",
        "buildId": "build_20260607_120000",
        "generatedAt": "2026-06-07T12:00:00+09:00",
        "creator": {"id": "creator_test", "displayName": None},
        "title": "録音APIクイズ",
        "description": "録音APIクイズの説明",
        "language": "ja",
        "change": {"operation": "initial_generate"},
        "items": [
            {
                "kind": "quiz",
                "name": "quiz.json",
                "title": "録音APIクイズ",
                "logicalId": "quiz",
                "fileVersionId": file_version_id,
                "url": f"http://localhost:8000/generated/{prefix}/objects/quiz/{file_version_id}.json",
            }
        ],
    }
    manifest_path = target_dir / "versions" / "v20260607_120000" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
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
    assert data["textSource"] == "raw"
    assert data["unitCount"] == 6
    assert data["billableUnitCount"] == 5
    assert "q_q-1_question" in unit_ids
    assert "q_q-1_choice_0" in unit_ids
    question = next(unit for unit in data["units"] if unit["itemId"] == "q_q-1_question")
    assert question["isRecorded"] is True
    assert question["hasCorrected"] is True
    assert question["usedTextSource"] == "raw"
    choice_0 = next(unit for unit in data["units"] if unit["itemId"] == "q_q-1_choice_0")
    assert choice_0["text"] == "人工知能"
    assert choice_0["hasCorrected"] is True
    assert choice_0["usedTextSource"] == "raw"
    assert data["totalChars"] == sum(unit["charCount"] for unit in data["units"] if not unit["isRecorded"])
    assert data["estimatedCredits"] == data["totalChars"] * 0.0001


def test_recording_estimate_can_use_corrected_text_source(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)

    response = client.post(
        "/tts/recording-estimate",
        json={"target": _target(pack_name), "unitIds": ["q_q-1_explanation"], "textSource": "corrected"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["textSource"] == "corrected"
    assert data["units"] == [
        {
            "itemId": "q_q-1_explanation",
            "text": "エーアイ は人工知能です。",
            "charCount": len("エーアイ は人工知能です。"),
            "packId": "quiz-pack",
            "packType": "quiz",
            "kind": "explanation",
            "isRecorded": False,
            "audioPath": None,
            "audioUrl": None,
            "hasCorrected": True,
            "usedTextSource": "corrected",
        }
    ]


def test_recording_estimate_does_not_charge_recorded_units_when_ids_are_specified(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)

    response = client.post(
        "/tts/recording-estimate",
        json={"target": _target(pack_name), "unitIds": ["q_q-1_question", "q_q-1_choice_0"]},
    )

    assert response.status_code == 200
    data = response.json()

    assert [unit["itemId"] for unit in data["units"]] == ["q_q-1_question", "q_q-1_choice_0"]
    assert data["unitCount"] == 2
    assert data["billableUnitCount"] == 1
    assert data["totalChars"] == len("人工知能")


def test_recording_endpoint_records_only_requested_units_and_skips_recorded(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)
    captured = {}

    def fake_record_generated_file_audio(file, units, storage_prefix, **kwargs):
        captured["unitIds"] = [unit.item_id for unit in units]
        captured["texts"] = [unit.text for unit in units]
        captured["forceRerecord"] = kwargs.get("force_rerecord")
        captured["storage_prefix"] = storage_prefix
        audio_path = kwargs["audio_path_factory"](units[1])
        file.content["assetBaseUrl"] = f"http://localhost:8000/generated/{storage_prefix}"
        file.content["questions"][0]["tts"]["choiceAudioPaths"] = [audio_path, None, None, None]
        file.content["questions"][0]["tts"]["choiceAudioUrls"] = None
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
                    audio_url=f"http://localhost:8000/generated/{storage_prefix}/{audio_path}",
                    audio_path=audio_path,
                    audio_data=b"mp3",
                    used_text_source=units[1].used_text_source,
                ),
            ],
        )

    monkeypatch.setattr("app.services.tts_recording_api.record_generated_file_audio", fake_record_generated_file_audio)
    monkeypatch.setattr(
        "app.services.storage_client.StorageClient.copy_prefix",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("copy_prefix must not be called")),
    )

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
    assert captured["texts"] == ["AI の説明はどれですか?", "人工知能"]
    assert captured["forceRerecord"] is False
    assert captured["storage_prefix"] == "sokqa/creators/creator_test/packs/content_test"
    assert data["textSource"] == "raw"
    assert data["versionId"].startswith("v")
    assert data["versionId"] != "v20260607_120000"
    assert data["assetBaseUrl"].endswith("/sokqa/creators/creator_test/packs/content_test")
    assert data["target"]["versionId"] == data["versionId"]
    assert data["summary"]["skippedUnits"] == 1
    assert data["summary"]["successCount"] == 1
    assert data["audioUrls"][0]["unitId"] == "q_q-1_choice_0"
    assert data["audioUrls"][0]["audioPath"].startswith("objects/audio/")
    assert data["audioUrls"][0]["audioUrl"].endswith(data["audioUrls"][0]["audioPath"])
    assert data["audioUrls"][0]["usedTextSource"] == "raw"
    new_manifest = tmp_path / "generated" / data["storagePrefix"] / "versions" / data["versionId"] / "manifest.json"
    assert new_manifest.exists()
    manifest = __import__("json").loads(new_manifest.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 1
    assert manifest["versionId"] == data["versionId"]
    assert manifest["buildId"] == data["buildId"]
    assert manifest["generatedAt"] == data["generatedAt"]
    assert manifest["revision"] == 2
    assert manifest["sourceVersionId"] == "v20260607_120000"
    assert manifest["title"] == "録音APIクイズ"
    assert manifest["description"] == "録音APIクイズの説明"
    assert manifest["language"] == "ja"
    assert manifest["change"]["operation"] == "recording"
    assert manifest["items"][0]["title"] == "録音APIクイズ"
    assert manifest["items"][0]["url"].startswith(f"http://localhost:8000/generated/{data['storagePrefix']}/objects/quiz/")
    assert (tmp_path / "generated" / data["storagePrefix"] / data["audioUrls"][0]["audioPath"]).exists()
    assert (
        tmp_path
        / "generated"
        / "sokqa/creators/creator_test/packs/content_test/versions/v20260607_120000/manifest.json"
    ).exists()


def test_recording_endpoint_can_force_rerecord_recorded_units(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)
    captured = {}

    def fake_record_generated_file_audio(file, units, storage_prefix, **kwargs):
        captured["unitIds"] = [unit.item_id for unit in units]
        captured["forceRerecord"] = kwargs.get("force_rerecord")
        return RecordingSummary(
            total_units=len(units),
            skipped_units=0,
            success_count=1,
            failure_count=0,
            failed_unit_ids=[],
            results=[
                RecordingResult(
                    unit_id="q_q-1_question",
                    success=True,
                    audio_url=f"https://cdn.example.test/{storage_prefix}/audio/quiz__q_q-1_question.mp3",
                    audio_path="audio/quiz__q_q-1_question.mp3",
                ),
            ],
        )

    monkeypatch.setattr("app.services.tts_recording_api.record_generated_file_audio", fake_record_generated_file_audio)

    response = client.post(
        "/tts/record",
        json={
            "target": _target(pack_name),
            "unitIds": ["q_q-1_question"],
            "forceRerecord": True,
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert captured["unitIds"] == ["q_q-1_question"]
    assert captured["forceRerecord"] is True
    assert data["forceRerecord"] is True
    assert data["summary"]["skippedUnits"] == 0


def test_recording_reset_clears_audio_urls_and_persists_pack(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)
    old_prefix = "sokqa/creators/creator_test/packs/content_test"
    monkeypatch.setattr(
        "app.services.storage_client.StorageClient.copy_prefix",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("copy_prefix must not be called")),
    )

    response = client.post(
        "/tts/recording-reset",
        json={"target": _target(pack_name), "unitIds": ["q_q-1_question"]},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["clearedCount"] == 1
    assert data["requestedUnitCount"] == 1
    assert data["versionId"] != "v20260607_120000"
    assert data["storagePrefix"] == "sokqa/creators/creator_test/packs/content_test"
    assert data["target"]["versionId"] == data["versionId"]
    assert data["units"][0]["itemId"] == "q_q-1_question"
    assert data["units"][0]["isRecorded"] is False

    manifest_path = tmp_path / "generated" / data["storagePrefix"] / "versions" / data["versionId"] / "manifest.json"
    assert manifest_path.exists()
    manifest = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 1
    assert manifest["revision"] == 2
    assert manifest["sourceVersionId"] == "v20260607_120000"
    assert manifest["change"]["operation"] == "recording_reset"
    object_path = manifest["items"][0]["url"].split(f"{data['storagePrefix']}/", 1)[1]
    updated_pack = __import__("json").loads((tmp_path / "generated" / data["storagePrefix"] / object_path).read_text(encoding="utf-8"))
    assert "questionAudioUrl" not in updated_pack["questions"][0]["tts"]
    assert updated_pack["questions"][0]["tts"]["questionText"] == "エーアイ の説明はどれですか?"
    assert (tmp_path / "generated" / old_prefix / "versions" / "v20260607_120000" / "manifest.json").exists()

    estimate = client.post("/tts/recording-estimate", json={"target": data["target"]}).json()
    question = next(unit for unit in estimate["units"] if unit["itemId"] == "q_q-1_question")
    assert question["isRecorded"] is False


def test_recording_reset_alias_paths_are_available(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)

    response = client.post(
        "/tts/recording_reset",
        json={"target": _target(pack_name), "unitIds": ["q_q-1_question"]},
    )

    assert response.status_code == 200
    assert response.json()["clearedCount"] == 1

    response = client.post(
        "/tts/reset-recording",
        json={"target": _target(pack_name), "unitIds": ["q_q-1_question"]},
    )

    assert response.status_code == 200


def test_recording_then_reset_creates_consecutive_v2_revisions_and_keeps_old_audio_object(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)

    def fake_record_generated_file_audio(file, units, storage_prefix, **kwargs):
        audio_path = kwargs["audio_path_factory"](units[0])
        file.content["assetBaseUrl"] = f"http://localhost:8000/generated/{storage_prefix}"
        file.content["questions"][0]["tts"]["choiceAudioPaths"] = [audio_path, None, None, None]
        return RecordingSummary(
            total_units=len(units),
            skipped_units=0,
            success_count=1,
            failure_count=0,
            failed_unit_ids=[],
            results=[
                RecordingResult(
                    unit_id="q_q-1_choice_0",
                    success=True,
                    audio_url=f"http://localhost:8000/generated/{storage_prefix}/{audio_path}",
                    audio_path=audio_path,
                    audio_data=b"mp3",
                    used_text_source=units[0].used_text_source,
                )
            ],
        )

    monkeypatch.setattr("app.services.tts_recording_api.record_generated_file_audio", fake_record_generated_file_audio)

    recording = client.post(
        "/tts/record",
        json={"target": _target(pack_name), "unitIds": ["q_q-1_choice_0"]},
    )
    assert recording.status_code == 200
    recording_data = recording.json()
    audio_path = recording_data["audioUrls"][0]["audioPath"]
    audio_file = tmp_path / "generated" / recording_data["storagePrefix"] / audio_path
    assert audio_file.exists()

    reset = client.post(
        "/tts/recording-reset",
        json={"target": recording_data["target"], "unitIds": ["q_q-1_choice_0"]},
    )
    assert reset.status_code == 200
    reset_data = reset.json()

    assert reset_data["versionId"] != recording_data["versionId"]
    recording_manifest_path = (
        tmp_path / "generated" / recording_data["storagePrefix"] / "versions" / recording_data["versionId"] / "manifest.json"
    )
    reset_manifest_path = (
        tmp_path / "generated" / reset_data["storagePrefix"] / "versions" / reset_data["versionId"] / "manifest.json"
    )
    recording_manifest = __import__("json").loads(recording_manifest_path.read_text(encoding="utf-8"))
    reset_manifest = __import__("json").loads(reset_manifest_path.read_text(encoding="utf-8"))

    assert recording_manifest["revision"] == 2
    assert recording_manifest["change"]["operation"] == "recording"
    assert reset_manifest["revision"] == 3
    assert reset_manifest["sourceVersionId"] == recording_data["versionId"]
    assert reset_manifest["change"]["operation"] == "recording_reset"
    assert audio_file.exists()

    listing = client.get("/packs?creatorId=creator_test").json()
    assert listing["items"][0]["versionId"] == reset_data["versionId"]
    assert listing["items"][0]["revision"] == 3


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
                    audio_path="audio/q_q-1_choice_0.mp3",
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


def test_recording_endpoint_can_use_corrected_text_source(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)
    captured = {}

    def fake_record_generated_file_audio(file, units, storage_prefix, **kwargs):
        captured["texts"] = [unit.text for unit in units]
        return RecordingSummary(
            total_units=len(units),
            skipped_units=0,
            success_count=1,
            failure_count=0,
            failed_unit_ids=[],
            results=[
                RecordingResult(
                    unit_id="q_q-1_explanation",
                    success=True,
                    audio_url=f"https://cdn.example.test/{storage_prefix}/audio/q_q-1_explanation.mp3",
                    audio_path="audio/q_q-1_explanation.mp3",
                    used_text_source=units[0].used_text_source,
                )
            ],
        )

    monkeypatch.setattr("app.services.tts_recording_api.record_generated_file_audio", fake_record_generated_file_audio)

    response = client.post(
        "/tts/record",
        json={
            "target": _target(pack_name),
            "unitIds": ["q_q-1_explanation"],
            "textSource": "corrected",
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["textSource"] == "corrected"
    assert captured["texts"] == ["エーアイ は人工知能です。"]
    assert data["summary"]["results"][0]["usedTextSource"] == "corrected"
    assert data["audioUrls"][0]["usedTextSource"] == "corrected"


def test_tts_voices_endpoint_returns_listed_voices(monkeypatch) -> None:
    def fake_list_cloud_tts_voices(language_code=None):
        assert language_code == "ja-JP"
        return [
            {
                "name": "ja-JP-Neural2-B",
                "label": "標準（開発用）Neural2",
                "languageCodes": ["ja-JP"],
                "ssmlGender": "MALE",
                "gender": "female",
                "tier": "standard",
                "order": 50,
                "naturalSampleRateHertz": 24000,
            }
        ]

    monkeypatch.setattr("app.routes.tts_recording.list_cloud_tts_voices", fake_list_cloud_tts_voices)

    response = client.get("/tts/voices?languageCode=ja-JP")

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "languageCode": "ja-JP",
        "voices": [
            {
                "name": "ja-JP-Neural2-B",
                "label": "標準（開発用）Neural2",
                "languageCodes": ["ja-JP"],
                "ssmlGender": "MALE",
                "gender": "female",
                "tier": "standard",
                "order": 50,
                "naturalSampleRateHertz": 24000,
            }
        ],
    }


def test_recording_endpoint_passes_voice_options(tmp_path, monkeypatch) -> None:
    _, pack_name = _write_pack(tmp_path, monkeypatch)
    captured = {}

    def fake_record_generated_file_audio(file, units, storage_prefix, **kwargs):
        captured["language_code"] = kwargs.get("language_code")
        captured["voice_name"] = kwargs.get("voice_name")
        captured["speaking_rate"] = kwargs.get("speaking_rate")
        captured["pitch"] = kwargs.get("pitch")
        return RecordingSummary(
            total_units=len(units),
            skipped_units=0,
            success_count=1,
            failure_count=0,
            failed_unit_ids=[],
            results=[
                RecordingResult(
                    unit_id="q_q-1_choice_0",
                    success=True,
                    audio_url=f"https://cdn.example.test/{storage_prefix}/audio/q_q-1_choice_0.mp3",
                    audio_path="audio/q_q-1_choice_0.mp3",
                    used_text_source=units[0].used_text_source,
                )
            ],
        )

    monkeypatch.setattr("app.services.tts_recording_api.record_generated_file_audio", fake_record_generated_file_audio)

    response = client.post(
        "/tts/record",
        json={
            "target": _target(pack_name),
            "unitIds": ["q_q-1_choice_0"],
            "voiceName": "ja-JP-Chirp3-HD-Achernar",
            "languageCode": "ja-JP",
            "speakingRate": 1.15,
            "pitch": -1.5,
        },
    )

    assert response.status_code == 200
    assert captured == {
        "language_code": "ja-JP",
        "voice_name": "ja-JP-Chirp3-HD-Achernar",
        "speaking_rate": 1.15,
        "pitch": -1.5,
    }
