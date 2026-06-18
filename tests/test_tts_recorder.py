import pytest

from app.schemas.sokqa import DocumentTts, GeneratedFile, QuizTts, SokqaDocumentItem, SokqaDocumentPack, SokqaQuestion, SokqaQuizPack
from app.services import tts_recorder
from app.services.tts_estimation import extract_recording_units_from_document_pack, extract_recording_units_from_quiz_pack
from app.services.tts_recorder import _max_concurrency, clear_pack_audio_urls, record_generated_file_audio, record_pack_audio


class FakeStorageClient:
    def __init__(self) -> None:
        self.saved_bytes: list[dict] = []
        self.saved_files: list[tuple[str, list[GeneratedFile], str | None]] = []

    def save_bytes(
        self,
        pack_id: str,
        object_name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        storage_prefix: str | None = None,
    ) -> str:
        self.saved_bytes.append(
            {
                "pack_id": pack_id,
                "object_name": object_name,
                "data": data,
                "content_type": content_type,
                "storage_prefix": storage_prefix,
            }
        )
        return f"https://cdn.example.test/{storage_prefix}/{object_name}"

    def save_files(self, pack_id: str, files: list[GeneratedFile], storage_prefix: str | None = None) -> list[GeneratedFile]:
        self.saved_files.append((pack_id, files, storage_prefix))
        return files


def _quiz_pack() -> SokqaQuizPack:
    return SokqaQuizPack(
        id="quiz-pack",
        title="録音クイズ",
        questions=[
            SokqaQuestion(
                id="q-1",
                question="AI の説明はどれですか?",
                choices=["人工知能", "会計", "在庫", "販売"],
                answerIndex=0,
                explanation="AI は人工知能です。",
                tts=QuizTts(
                    questionText="エーアイ の説明はどれですか?",
                    choiceTexts=["人工知能", "会計", "在庫", "販売"],
                    explanationText="エーアイ は人工知能です。",
                ),
            )
        ],
    )


def _document_pack() -> SokqaDocumentPack:
    return SokqaDocumentPack(
        id="doc-pack",
        title="録音ドキュメント",
        documents=[
            SokqaDocumentItem(
                id="doc-1",
                text="AI の説明です。",
                tts=DocumentTts(text="エーアイ の説明です。"),
            )
        ],
    )


def test_record_quiz_audio_urls_are_attached_and_mp3_is_saved() -> None:
    pack = _quiz_pack()
    units = extract_recording_units_from_quiz_pack(pack)
    storage = FakeStorageClient()

    summary = record_pack_audio(
        pack,
        units,
        "sokqa/creators/creator/packs/content/versions/v1",
        storage_client=storage,
        synthesize_fn=lambda text: f"mp3:{text}".encode(),
    )

    tts = pack.questions[0].tts
    assert tts is not None
    assert summary.success_count == 6
    assert summary.failure_count == 0
    assert pack.assetBaseUrl.endswith("/sokqa/creators/creator/packs/content/versions/v1")
    assert tts.questionAudioPath == "audio/quiz-pack__q_q-1_question.mp3"
    assert tts.questionAudioUrl is None
    assert tts.questionText == "エーアイ の説明はどれですか?"
    assert tts.choiceTexts == ["人工知能", "会計", "在庫", "販売"]
    assert tts.explanationText == "エーアイ は人工知能です。"
    assert tts.choiceAudioPaths == [
        "audio/quiz-pack__q_q-1_choice_0.mp3",
        "audio/quiz-pack__q_q-1_choice_1.mp3",
        "audio/quiz-pack__q_q-1_choice_2.mp3",
        "audio/quiz-pack__q_q-1_choice_3.mp3",
    ]
    assert tts.choiceAudioUrls is None
    assert tts.explanationAudioPath == "audio/quiz-pack__q_q-1_explanation.mp3"
    assert tts.explanationAudioUrl is None
    assert {entry["content_type"] for entry in storage.saved_bytes} == {"audio/mpeg"}
    assert {entry["object_name"] for entry in storage.saved_bytes} == {
        "audio/quiz-pack__q_q-1_question.mp3",
        "audio/quiz-pack__q_q-1_choice_0.mp3",
        "audio/quiz-pack__q_q-1_choice_1.mp3",
        "audio/quiz-pack__q_q-1_choice_2.mp3",
        "audio/quiz-pack__q_q-1_choice_3.mp3",
        "audio/quiz-pack__q_q-1_explanation.mp3",
    }


def test_record_document_audio_url_is_attached() -> None:
    pack = _document_pack()
    units = extract_recording_units_from_document_pack(pack)
    storage = FakeStorageClient()

    summary = record_pack_audio(
        pack,
        units,
        "sokqa/creators/creator/packs/content/versions/v1",
        storage_client=storage,
        synthesize_fn=lambda text: b"mp3",
    )

    assert summary.success_count == 1
    assert pack.documents[0].tts is not None
    assert pack.assetBaseUrl.endswith("/sokqa/creators/creator/packs/content/versions/v1")
    assert pack.documents[0].tts.audioPath == "audio/doc-pack__doc_doc-1.mp3"
    assert pack.documents[0].tts.audioUrl is None
    assert pack.documents[0].tts.text == "エーアイ の説明です。"
    assert storage.saved_bytes[0]["object_name"] == "audio/doc-pack__doc_doc-1.mp3"


@pytest.mark.parametrize("audio_path", ["/audio/doc.mp3", "audio/../doc.mp3", "https://cdn.example.test/audio/doc.mp3"])
def test_audio_paths_must_be_relative(audio_path: str) -> None:
    with pytest.raises(ValueError):
        DocumentTts(audioPath=audio_path)


def test_record_pack_audio_passes_voice_options_to_default_synthesizer(monkeypatch) -> None:
    pack = _document_pack()
    units = extract_recording_units_from_document_pack(pack)
    storage = FakeStorageClient()
    captured = {}

    def fake_synthesize_text_to_mp3(text, *, language_code=None, voice_name=None, speaking_rate=None, pitch=None):
        captured["text"] = text
        captured["language_code"] = language_code
        captured["voice_name"] = voice_name
        captured["speaking_rate"] = speaking_rate
        captured["pitch"] = pitch
        return b"mp3"

    monkeypatch.setattr(tts_recorder, "synthesize_text_to_mp3", fake_synthesize_text_to_mp3)

    summary = record_pack_audio(
        pack,
        units,
        "sokqa/creators/creator/packs/content/versions/v1",
        storage_client=storage,
        synthesize_fn=tts_recorder.synthesize_text_to_mp3,
        language_code="ja-JP",
        voice_name="ja-JP-Chirp3-HD-Achernar",
        speaking_rate=1.2,
        pitch=-2.0,
    )

    assert summary.success_count == 1
    assert captured == {
        "text": "AI の説明です。",
        "language_code": "ja-JP",
        "voice_name": "ja-JP-Chirp3-HD-Achernar",
        "speaking_rate": 1.2,
        "pitch": -2.0,
    }


def test_recorded_audio_urls_are_excluded_from_unrecorded_targets() -> None:
    quiz = _quiz_pack()
    assert quiz.questions[0].tts is not None
    quiz.questions[0].tts.questionAudioPath = "audio/question.mp3"
    quiz.questions[0].tts.choiceAudioPaths = [None, "audio/choice-1.mp3", None, None]
    quiz.questions[0].tts.explanationAudioUrl = "https://cdn/explanation.mp3"
    units = extract_recording_units_from_quiz_pack(quiz)

    assert next(unit for unit in units if unit.item_id == "q_q-1_question").is_recorded is True
    assert next(unit for unit in units if unit.item_id == "q_q-1_choice_0").is_recorded is False
    assert next(unit for unit in units if unit.item_id == "q_q-1_choice_1").is_recorded is True
    assert next(unit for unit in units if unit.item_id == "q_q-1_explanation").is_recorded is True

    document = _document_pack()
    assert document.documents[0].tts is not None
    document.documents[0].tts.audioPath = "audio/doc.mp3"
    doc_units = extract_recording_units_from_document_pack(document)

    assert doc_units[0].is_recorded is True


def test_clear_audio_removes_choice_audio_array_when_all_entries_are_null() -> None:
    quiz = _quiz_pack()
    assert quiz.questions[0].tts is not None
    quiz.questions[0].tts.choiceAudioPaths = ["audio/choice-0.mp3", None, None, None]
    quiz.questions[0].tts.choiceAudioUrls = [None, None, None, None]
    units = extract_recording_units_from_quiz_pack(quiz)
    unit = next(unit for unit in units if unit.item_id == "q_q-1_choice_0")

    cleared = clear_pack_audio_urls(quiz, [unit])

    assert cleared == 1
    assert quiz.questions[0].tts.choiceAudioPaths is None
    assert quiz.questions[0].tts.choiceAudioUrls is None
    assert quiz.questions[0].tts.choiceTexts == ["人工知能", "会計", "在庫", "販売"]
    assert quiz.questions[0].answerIndex == 0


def test_clear_audio_removes_empty_document_tts_without_setting_refresh_flag() -> None:
    document = _document_pack()
    assert document.documents[0].tts is not None
    document.documents[0].tts.text = None
    document.documents[0].tts.audioPath = "audio/doc.mp3"
    units = extract_recording_units_from_document_pack(document)

    cleared = clear_pack_audio_urls(document, [units[0]])

    assert cleared == 1
    assert document.documents[0].tts is None
    dumped = document.model_dump(exclude_none=True)
    assert "tts" not in dumped["documents"][0]


def test_clear_audio_keeps_document_tts_text_without_setting_refresh_flag() -> None:
    document = _document_pack()
    assert document.documents[0].tts is not None
    document.documents[0].tts.audioPath = "audio/doc.mp3"
    units = extract_recording_units_from_document_pack(document)

    cleared = clear_pack_audio_urls(document, [units[0]])

    assert cleared == 1
    assert document.documents[0].tts is not None
    assert document.documents[0].tts.text == "エーアイ の説明です。"
    assert document.documents[0].tts.audioPath is None
    assert document.documents[0].tts.audioUrl is None
    assert document.model_dump(exclude_none=True)["documents"][0]["tts"] == {"text": "エーアイ の説明です。"}


def test_clear_audio_removes_empty_quiz_tts_without_setting_refresh_flag() -> None:
    quiz = _quiz_pack()
    assert quiz.questions[0].tts is not None
    quiz.questions[0].tts.questionText = None
    quiz.questions[0].tts.choiceTexts = None
    quiz.questions[0].tts.explanationText = None
    quiz.questions[0].tts.choiceAudioPaths = ["audio/choice-0.mp3", None, None, None]
    units = extract_recording_units_from_quiz_pack(quiz)
    unit = next(unit for unit in units if unit.item_id == "q_q-1_choice_0")

    cleared = clear_pack_audio_urls(quiz, [unit])

    assert cleared == 1
    assert quiz.questions[0].tts is None
    dumped = quiz.model_dump(exclude_none=True)
    assert "tts" not in dumped["questions"][0]


def test_clear_audio_keeps_choice_audio_array_when_some_entries_remain() -> None:
    quiz = _quiz_pack()
    assert quiz.questions[0].tts is not None
    quiz.questions[0].tts.choiceAudioPaths = ["audio/choice-0.mp3", "audio/choice-1.mp3", None, None]
    quiz.questions[0].tts.choiceAudioUrls = ["https://cdn/choice-0.mp3", None, None, None]
    units = extract_recording_units_from_quiz_pack(quiz)
    unit = next(unit for unit in units if unit.item_id == "q_q-1_choice_0")

    cleared = clear_pack_audio_urls(quiz, [unit])

    assert cleared == 1
    assert quiz.questions[0].tts.choiceAudioPaths == [None, "audio/choice-1.mp3", None, None]
    assert quiz.questions[0].tts.choiceAudioUrls is None
    assert quiz.questions[0].tts.choiceTexts == ["人工知能", "会計", "在庫", "販売"]
    assert quiz.questions[0].answerIndex == 0


def test_force_rerecord_records_previously_recorded_units() -> None:
    pack = _quiz_pack()
    assert pack.questions[0].tts is not None
    pack.questions[0].tts.questionAudioUrl = "https://cdn.example.test/old.mp3"
    units = extract_recording_units_from_quiz_pack(pack)
    storage = FakeStorageClient()

    summary = record_pack_audio(
        pack,
        units[:1],
        "sokqa/creators/creator/packs/content/versions/v1",
        storage_client=storage,
        synthesize_fn=lambda text: b"new-mp3",
        force_rerecord=True,
    )

    assert summary.skipped_units == 0
    assert summary.success_count == 1
    assert storage.saved_bytes[0]["object_name"] == "audio/quiz-pack__q_q-1_question.mp3"
    assert pack.questions[0].tts.questionAudioPath == "audio/quiz-pack__q_q-1_question.mp3"
    assert pack.questions[0].tts.questionAudioUrl is None


def test_one_synthesis_failure_does_not_stop_other_units() -> None:
    pack = _quiz_pack()
    units = extract_recording_units_from_quiz_pack(pack)
    storage = FakeStorageClient()

    def synthesize(text: str) -> bytes:
        if text == "会計":
            raise RuntimeError("synthetic failure")
        return b"mp3"

    summary = record_pack_audio(
        pack,
        units,
        "sokqa/creators/creator/packs/content/versions/v1",
        storage_client=storage,
        synthesize_fn=synthesize,
    )

    assert summary.success_count == 5
    assert summary.failure_count == 1
    assert summary.failed_unit_ids == ["q_q-1_choice_1"]
    assert len(storage.saved_bytes) == 5
    assert pack.questions[0].tts is not None
    assert pack.questions[0].tts.choiceAudioPaths is not None
    assert pack.questions[0].tts.choiceAudioPaths[0] == "audio/quiz-pack__q_q-1_choice_0.mp3"
    assert pack.questions[0].tts.choiceAudioPaths[1] is None


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (0, 1),
        (1, 1),
        (5, 5),
        (99, 10),
    ],
)
def test_max_concurrency_is_clamped(configured: int, expected: int) -> None:
    assert _max_concurrency(configured) == expected


def test_record_generated_file_audio_persists_updated_json() -> None:
    file = GeneratedFile(
        name="quiz-pack.json",
        kind="quiz",
        content=_quiz_pack().model_dump(exclude_none=True),
    )
    units = extract_recording_units_from_quiz_pack(SokqaQuizPack.model_validate(file.content))[:1]
    storage = FakeStorageClient()

    summary = record_generated_file_audio(
        file,
        units,
        "sokqa/creators/creator/packs/content/versions/v1",
        storage_client=storage,
        synthesize_fn=lambda text: b"mp3",
    )

    assert summary.success_count == 1
    assert file.content["assetBaseUrl"].endswith("/sokqa/creators/creator/packs/content/versions/v1")
    assert file.content["questions"][0]["tts"]["questionAudioPath"] == "audio/quiz-pack__q_q-1_question.mp3"
    assert "questionAudioUrl" not in file.content["questions"][0]["tts"]
    assert storage.saved_files
    assert storage.saved_files[0][0] == "quiz-pack"
    assert storage.saved_files[0][2] == "sokqa/creators/creator/packs/content/versions/v1"


def test_record_generated_file_audio_uses_file_name_namespace_to_avoid_cross_file_collisions() -> None:
    storage_a = FakeStorageClient()
    storage_b = FakeStorageClient()
    file_a = GeneratedFile(name="doc_01.json", kind="document", content=_document_pack().model_dump(exclude_none=True))
    file_b = GeneratedFile(name="doc_03.json", kind="document", content=_document_pack().model_dump(exclude_none=True))
    units_a = extract_recording_units_from_document_pack(SokqaDocumentPack.model_validate(file_a.content))
    units_b = extract_recording_units_from_document_pack(SokqaDocumentPack.model_validate(file_b.content))

    record_generated_file_audio(
        file_a,
        units_a,
        "sokqa/creators/creator/packs/content/versions/v1",
        storage_client=storage_a,
        synthesize_fn=lambda text: b"mp3-a",
    )
    record_generated_file_audio(
        file_b,
        units_b,
        "sokqa/creators/creator/packs/content/versions/v1",
        storage_client=storage_b,
        synthesize_fn=lambda text: b"mp3-b",
    )

    assert storage_a.saved_bytes[0]["object_name"] == "audio/doc_01__doc_doc-1.mp3"
    assert storage_b.saved_bytes[0]["object_name"] == "audio/doc_03__doc_doc-1.mp3"
    assert file_a.content["documents"][0]["tts"]["audioPath"] == "audio/doc_01__doc_doc-1.mp3"
    assert file_b.content["documents"][0]["tts"]["audioPath"] == "audio/doc_03__doc_doc-1.mp3"
