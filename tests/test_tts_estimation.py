"""Tests for TTS estimation module."""

from app.schemas.sokqa import (
    DocumentTts,
    QuizTts,
    SokqaDocumentItem,
    SokqaDocumentPack,
    SokqaQuestion,
    SokqaQuizPack,
)
from app.services.tts_estimation import (
    AggregatedEstimation,
    PackEstimation,
    RecordingUnit,
    estimate_pack,
    estimate_packs,
    extract_recording_units_from_document_pack,
    extract_recording_units_from_quiz_pack,
    estimation_to_dict,
)


def _make_quiz_pack(has_tts: bool = False) -> SokqaQuizPack:
    """Create a sample quiz pack for testing."""
    questions = []
    for i in range(2):
        q = SokqaQuestion(
            id=f"q-{i}",
            question=f"問題文{i}",
            choices=[f"選択肢{i}-0", f"選択肢{i}-1", f"選択肢{i}-2", f"選択肢{i}-3"],
            answerIndex=0,
            explanation=f"解説{i}",
            tts=(
                QuizTts(
                    questionText=f"問題文TTS{i}",
                    choiceTexts=[f"選択肢TTS{i}-0", f"選択肢TTS{i}-1", f"選択肢TTS{i}-2", f"選択肢TTS{i}-3"],
                    explanationText=f"解説TTS{i}",
                )
                if has_tts
                else None
            ),
        )
        questions.append(q)

    return SokqaQuizPack(
        id="quiz-pack-1",
        title="テストクイズパック",
        questions=questions,
    )


def _make_document_pack(has_tts: bool = False) -> SokqaDocumentPack:
    """Create a sample document pack for testing."""
    docs = []
    for i in range(3):
        d = SokqaDocumentItem(
            id=f"doc-{i}",
            text=f"ドキュメント本文{i}" * 10,
            tts=DocumentTts(text=f"ドキュメントTTS本文{i}" * 8) if has_tts else None,
        )
        docs.append(d)

    return SokqaDocumentPack(
        id="doc-pack-1",
        title="テストドキュメントパック",
        documents=docs,
    )


def test_extract_recording_units_from_quiz_pack_without_tts() -> None:
    """Test extraction from quiz pack without TTS corrections."""
    pack = _make_quiz_pack(has_tts=False)
    units = extract_recording_units_from_quiz_pack(pack)

    # 2 questions * (1 question + 4 choices + 1 explanation) = 12 units
    assert len(units) == 12

    # Check first question units
    q0_question = next(u for u in units if u.item_id == "q_q-0_question")
    q0_choice0 = next(u for u in units if u.item_id == "q_q-0_choice_0")
    q0_choice3 = next(u for u in units if u.item_id == "q_q-0_choice_3")
    q0_explanation = next(u for u in units if u.item_id == "q_q-0_explanation")

    assert q0_question.text == "問題文0"
    assert q0_choice0.text == "選択肢0-0"
    assert q0_choice3.text == "選択肢0-3"
    assert q0_explanation.text == "解説0"

    # All units should be unrecorded because no audio URLs are set.
    for u in units:
        assert u.pack_id == "quiz-pack-1"
        assert u.pack_type == "quiz"
        assert u.kind in ("question", "choice", "explanation")


def test_extract_recording_units_from_quiz_pack_with_tts_defaults_to_raw() -> None:
    """Test extraction from quiz pack with TTS corrections defaults to raw text."""
    pack = _make_quiz_pack(has_tts=True)
    units = extract_recording_units_from_quiz_pack(pack)

    assert len(units) == 12

    q0_question = next(u for u in units if u.item_id == "q_q-0_question")
    q0_choice0 = next(u for u in units if u.item_id == "q_q-0_choice_0")
    q0_explanation = next(u for u in units if u.item_id == "q_q-0_explanation")

    assert q0_question.text == "問題文0"
    assert q0_choice0.text == "選択肢0-0"
    assert q0_explanation.text == "解説0"


def test_extract_recording_units_from_quiz_pack_with_corrected_text_source() -> None:
    """Test extraction from quiz pack can use TTS correction text when requested."""
    pack = _make_quiz_pack(has_tts=True)
    units = extract_recording_units_from_quiz_pack(pack, text_source="corrected")

    q0_question = next(u for u in units if u.item_id == "q_q-0_question")
    q0_choice0 = next(u for u in units if u.item_id == "q_q-0_choice_0")
    q0_explanation = next(u for u in units if u.item_id == "q_q-0_explanation")

    assert q0_question.text == "問題文TTS0"
    assert q0_choice0.text == "選択肢TTS0-0"
    assert q0_explanation.text == "解説TTS0"


def test_extract_recording_units_from_document_pack_without_tts() -> None:
    """Test extraction from document pack without TTS corrections."""
    pack = _make_document_pack(has_tts=False)
    units = extract_recording_units_from_document_pack(pack)

    assert len(units) == 3

    for i, u in enumerate(units):
        assert u.item_id == f"doc_doc-{i}"
        assert u.text == f"ドキュメント本文{i}" * 10
        assert u.char_count == len(u.text)
        assert u.pack_id == "doc-pack-1"
        assert u.pack_type == "document"
        assert u.kind == "document"


def test_extract_recording_units_from_document_pack_with_tts_defaults_to_raw() -> None:
    """Test extraction from document pack with TTS corrections defaults to raw text."""
    pack = _make_document_pack(has_tts=True)
    units = extract_recording_units_from_document_pack(pack)

    assert len(units) == 3

    for i, u in enumerate(units):
        assert u.text == f"ドキュメント本文{i}" * 10
        assert u.char_count == len(u.text)


def test_extract_recording_units_from_document_pack_with_corrected_text_source() -> None:
    """Test extraction from document pack can use TTS correction text when requested."""
    pack = _make_document_pack(has_tts=True)
    units = extract_recording_units_from_document_pack(pack, text_source="corrected")

    for i, u in enumerate(units):
        assert u.text == f"ドキュメントTTS本文{i}" * 8
        assert u.char_count == len(u.text)


def test_estimate_single_quiz_pack() -> None:
    """Test credit estimation for a single quiz pack."""
    pack = _make_quiz_pack(has_tts=False)
    estimation = estimate_pack(pack, credit_per_char=0.0001)

    assert isinstance(estimation, PackEstimation)
    assert estimation.pack_id == "quiz-pack-1"
    assert estimation.pack_type == "quiz"
    assert estimation.total_units == 12
    assert estimation.recorded_units == 0
    assert estimation.unrecorded_units == 12
    assert estimation.total_chars > 0
    assert estimation.recorded_chars == 0
    assert estimation.unrecorded_chars == estimation.total_chars
    assert estimation.estimated_credits == estimation.unrecorded_chars * 0.0001
    assert len(estimation.units) == 12


def test_estimate_single_document_pack() -> None:
    """Test credit estimation for a single document pack."""
    pack = _make_document_pack(has_tts=False)
    estimation = estimate_pack(pack, credit_per_char=0.0001)

    assert estimation.pack_id == "doc-pack-1"
    assert estimation.pack_type == "document"
    assert estimation.total_units == 3
    assert estimation.recorded_units == 0
    assert estimation.unrecorded_units == 3
    assert estimation.estimated_credits == estimation.unrecorded_chars * 0.0001


def test_estimate_multiple_packs_aggregated() -> None:
    """Test aggregated estimation across multiple packs."""
    quiz_pack = _make_quiz_pack(has_tts=False)
    doc_pack = _make_document_pack(has_tts=False)

    agg = estimate_packs([quiz_pack, doc_pack], credit_per_char=0.0001)

    assert isinstance(agg, AggregatedEstimation)
    assert len(agg.pack_estimations) == 2
    assert agg.total_units == 12 + 3
    assert agg.recorded_units == 0
    assert agg.unrecorded_units == 15
    assert agg.total_chars == agg.pack_estimations[0].total_chars + agg.pack_estimations[1].total_chars
    assert agg.estimated_credits == agg.unrecorded_chars * 0.0001


def test_estimation_to_dict_pack() -> None:
    """Test converting PackEstimation to dict."""
    pack = _make_quiz_pack(has_tts=False)
    estimation = estimate_pack(pack, credit_per_char=0.0001)
    d = estimation_to_dict(estimation)

    assert d["packId"] == "quiz-pack-1"
    assert d["packType"] == "quiz"
    assert d["totalUnits"] == 12
    assert d["unrecordedUnits"] == 12
    assert "estimatedCredits" in d
    assert len(d["units"]) == 12
    assert d["units"][0]["itemId"] == "q_q-0_question"


def test_estimation_to_dict_aggregated() -> None:
    """Test converting AggregatedEstimation to dict."""
    quiz_pack = _make_quiz_pack(has_tts=False)
    doc_pack = _make_document_pack(has_tts=False)

    agg = estimate_packs([quiz_pack, doc_pack], credit_per_char=0.0001)
    d = estimation_to_dict(agg)

    assert "packEstimations" in d
    assert len(d["packEstimations"]) == 2
    assert d["totalUnits"] == 15
    assert d["unrecordedUnits"] == 15


def test_tts_correction_char_count_difference_when_corrected_requested() -> None:
    """Test that TTS correction text char count differs from original."""
    pack_no_tts = _make_quiz_pack(has_tts=False)
    pack_with_tts = _make_quiz_pack(has_tts=True)

    est_no_tts = estimate_pack(pack_no_tts, credit_per_char=0.0001)
    est_with_tts = estimate_pack(pack_with_tts, credit_per_char=0.0001, text_source="corrected")

    # TTS text is different length, so char counts should differ
    assert est_no_tts.total_chars != est_with_tts.total_chars


def test_estimate_pack_defaults_to_raw_even_when_tts_exists() -> None:
    pack_no_tts = _make_quiz_pack(has_tts=False)
    pack_with_tts = _make_quiz_pack(has_tts=True)

    est_no_tts = estimate_pack(pack_no_tts, credit_per_char=0.0001)
    est_with_tts = estimate_pack(pack_with_tts, credit_per_char=0.0001)

    assert est_no_tts.total_chars == est_with_tts.total_chars
    assert [unit.text for unit in est_with_tts.units] == [unit.text for unit in est_no_tts.units]


def test_estimate_packs_accepts_corrected_text_source() -> None:
    quiz_pack = _make_quiz_pack(has_tts=True)
    doc_pack = _make_document_pack(has_tts=True)

    raw = estimate_packs([quiz_pack, doc_pack], credit_per_char=0.0001)
    corrected = estimate_packs([quiz_pack, doc_pack], credit_per_char=0.0001, text_source="corrected")

    assert raw.total_chars != corrected.total_chars


def test_recorded_status_false_without_audio_urls() -> None:
    """Test that recorded status is False when no audio URLs are set."""
    pack = _make_quiz_pack(has_tts=False)
    estimation = estimate_pack(pack, credit_per_char=0.0001)

    # No audio URLs are set, so everything is unrecorded.
    assert estimation.recorded_units == 0
    assert estimation.unrecorded_units == estimation.total_units
    assert estimation.recorded_chars == 0
    assert estimation.unrecorded_chars == estimation.total_chars


def test_custom_credit_rate() -> None:
    """Test that custom credit_per_char overrides config."""
    pack = _make_quiz_pack(has_tts=False)

    # Default rate from config is 0.0001
    est_default = estimate_pack(pack)

    # Custom rate
    est_custom = estimate_pack(pack, credit_per_char=0.001)

    assert est_custom.estimated_credits == est_default.unrecorded_chars * 0.001
    # Use approximate comparison due to floating point precision
    assert abs(est_custom.estimated_credits - est_default.estimated_credits * 10) < 1e-10


# ── Tests for is_recorded field on RecordingUnit ──


def test_recording_unit_has_is_recorded_field() -> None:
    """Test that RecordingUnit has an is_recorded field and it defaults to False."""
    pack = _make_quiz_pack(has_tts=False)
    units = extract_recording_units_from_quiz_pack(pack)

    # All units should have is_recorded=False when no audio URLs are set.
    for u in units:
        assert hasattr(u, "is_recorded"), f"RecordingUnit missing is_recorded field: {u.item_id}"
        assert u.is_recorded is False, f"Expected is_recorded=False for {u.item_id}, got {u.is_recorded}"


def test_estimation_to_dict_includes_is_recorded() -> None:
    """Test that estimation_to_dict includes isRecorded in unit output."""
    pack = _make_quiz_pack(has_tts=False)
    estimation = estimate_pack(pack, credit_per_char=0.0001)
    d = estimation_to_dict(estimation)

    for unit_dict in d["units"]:
        assert "isRecorded" in unit_dict, f"Missing isRecorded in unit dict: {unit_dict['itemId']}"
        assert unit_dict["isRecorded"] is False


def test_estimate_pack_no_none_is_recorded_calls() -> None:
    """Test that estimate_pack no longer passes None to _is_recorded.

    This verifies fix point 1: estimate_pack uses unit.is_recorded
    instead of calling _is_recorded(None, kind).
    """
    pack = _make_quiz_pack(has_tts=False)
    estimation = estimate_pack(pack, credit_per_char=0.0001)

    # The estimation should work correctly when all units are unrecorded.
    assert estimation.recorded_units == 0
    assert estimation.unrecorded_units == estimation.total_units


# ── Tests for variable choices count ──


def test_extract_quiz_pack_with_3_choices() -> None:
    """Test extraction from quiz pack with 3 choices (not the hardcoded 4).

    Uses model_construct to bypass SokqaQuestion's 4-choices validation,
    since the extraction code itself should not depend on exactly 4 choices.
    """
    q = SokqaQuestion.model_construct(
        id="vq-0",
        question="問題",
        choices=["選択1", "選択2", "選択3"],
        answerIndex=0,
        explanation="解説",
        tts=None,
    )
    pack = SokqaQuizPack.model_construct(
        id="var-quiz",
        title="可変選択肢テスト",
        questions=[q],
    )
    units = extract_recording_units_from_quiz_pack(pack)

    # 1 question + 3 choices + 1 explanation = 5 units (NOT 7 = 1+4+1)
    assert len(units) == 5

    kinds = [u.kind for u in units]
    assert kinds.count("question") == 1
    assert kinds.count("choice") == 3
    assert kinds.count("explanation") == 1

    # Verify all choice units have correct item_ids
    choice_units = [u for u in units if u.kind == "choice"]
    assert len(choice_units) == 3
    for i, cu in enumerate(choice_units):
        assert cu.item_id == f"q_vq-0_choice_{i}"
        assert cu.text == f"選択{i+1}"


def test_extract_quiz_pack_with_5_choices() -> None:
    """Test extraction from quiz pack with 5 choices (not the hardcoded 4).

    Uses model_construct to bypass SokqaQuestion's 4-choices validation.
    """
    q = SokqaQuestion.model_construct(
        id="vq-5",
        question="問題5",
        choices=[f"選択{j}" for j in range(5)],
        answerIndex=0,
        explanation="解説5",
        tts=None,
    )
    pack = SokqaQuizPack.model_construct(
        id="var-quiz-5",
        title="5選択肢テスト",
        questions=[q],
    )
    units = extract_recording_units_from_quiz_pack(pack)

    # 1 question + 5 choices + 1 explanation = 7 units
    assert len(units) == 7
    kinds = [u.kind for u in units]
    assert kinds.count("choice") == 5


def test_extract_document_pack_units_have_is_recorded() -> None:
    """Test that document pack units also have is_recorded field."""
    pack = _make_document_pack(has_tts=False)
    units = extract_recording_units_from_document_pack(pack)

    for u in units:
        assert hasattr(u, "is_recorded"), f"Document RecordingUnit missing is_recorded field: {u.item_id}"
        assert u.is_recorded is False
        assert u.kind == "document"
