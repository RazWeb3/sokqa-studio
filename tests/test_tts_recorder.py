import pytest

from app.schemas.sokqa import DocumentTts, GeneratedFile, QuizTts, SokqaDocumentItem, SokqaDocumentPack, SokqaQuestion, SokqaQuizPack
from app.services.tts_estimation import extract_recording_units_from_document_pack, extract_recording_units_from_quiz_pack
from app.services.tts_recorder import _max_concurrency, record_generated_file_audio, record_pack_audio


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
    assert tts.questionAudioUrl.endswith("/audio/q_q-1_question.mp3")
    assert tts.choiceAudioUrls == [
        "https://cdn.example.test/sokqa/creators/creator/packs/content/versions/v1/audio/q_q-1_choice_0.mp3",
        "https://cdn.example.test/sokqa/creators/creator/packs/content/versions/v1/audio/q_q-1_choice_1.mp3",
        "https://cdn.example.test/sokqa/creators/creator/packs/content/versions/v1/audio/q_q-1_choice_2.mp3",
        "https://cdn.example.test/sokqa/creators/creator/packs/content/versions/v1/audio/q_q-1_choice_3.mp3",
    ]
    assert tts.explanationAudioUrl.endswith("/audio/q_q-1_explanation.mp3")
    assert {entry["content_type"] for entry in storage.saved_bytes} == {"audio/mpeg"}
    assert {entry["object_name"] for entry in storage.saved_bytes} == {
        "audio/q_q-1_question.mp3",
        "audio/q_q-1_choice_0.mp3",
        "audio/q_q-1_choice_1.mp3",
        "audio/q_q-1_choice_2.mp3",
        "audio/q_q-1_choice_3.mp3",
        "audio/q_q-1_explanation.mp3",
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
    assert pack.documents[0].tts.audioUrl == "https://cdn.example.test/sokqa/creators/creator/packs/content/versions/v1/audio/doc_doc-1.mp3"
    assert storage.saved_bytes[0]["object_name"] == "audio/doc_doc-1.mp3"


def test_recorded_audio_urls_are_excluded_from_unrecorded_targets() -> None:
    quiz = _quiz_pack()
    assert quiz.questions[0].tts is not None
    quiz.questions[0].tts.questionAudioUrl = "https://cdn/question.mp3"
    quiz.questions[0].tts.choiceAudioUrls = [None, "https://cdn/choice-1.mp3", None, None]
    quiz.questions[0].tts.explanationAudioUrl = "https://cdn/explanation.mp3"
    units = extract_recording_units_from_quiz_pack(quiz)

    assert next(unit for unit in units if unit.item_id == "q_q-1_question").is_recorded is True
    assert next(unit for unit in units if unit.item_id == "q_q-1_choice_0").is_recorded is False
    assert next(unit for unit in units if unit.item_id == "q_q-1_choice_1").is_recorded is True
    assert next(unit for unit in units if unit.item_id == "q_q-1_explanation").is_recorded is True

    document = _document_pack()
    assert document.documents[0].tts is not None
    document.documents[0].tts.audioUrl = "https://cdn/doc.mp3"
    doc_units = extract_recording_units_from_document_pack(document)

    assert doc_units[0].is_recorded is True


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
    assert pack.questions[0].tts.choiceAudioUrls is not None
    assert pack.questions[0].tts.choiceAudioUrls[0].endswith("/audio/q_q-1_choice_0.mp3")
    assert pack.questions[0].tts.choiceAudioUrls[1] is None


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
    assert file.content["questions"][0]["tts"]["questionAudioUrl"].endswith("/audio/q_q-1_question.mp3")
    assert storage.saved_files
    assert storage.saved_files[0][0] == "quiz-pack"
    assert storage.saved_files[0][2] == "sokqa/creators/creator/packs/content/versions/v1"
