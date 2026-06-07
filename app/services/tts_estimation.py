"""TTS estimation: extract recording target texts and estimate credit cost.

This module provides functions to:
1. Extract recording target texts from quiz/document packs
2. Estimate credit cost based on character count
3. Aggregate estimates across multiple packs

No TTS API calls, no GCS operations, no billing - pure calculation only.
"""

from dataclasses import dataclass
from typing import Any, Literal

from app.config import get_settings
from app.schemas.sokqa import SokqaDocumentItem, SokqaDocumentPack, SokqaQuestion, SokqaQuizPack

RecordingTextSource = Literal["raw", "corrected"]


@dataclass(frozen=True)
class RecordingUnit:
    """A single recording unit (target for TTS audio generation)."""

    item_id: str  # e.g., "q_q-1_question", "q_q-1_choice_0", "q_q-1_explanation", "doc_doc-1"
    text: str  # The actual text to be recorded
    char_count: int  # len(text)
    pack_id: str  # Pack ID this unit belongs to
    pack_type: str  # "quiz" or "document"
    kind: str  # "question", "choice", "explanation", "document"
    is_recorded: bool  # True if the original item already has an audio URL


@dataclass(frozen=True)
class PackEstimation:
    """Estimation result for a single pack."""

    pack_id: str
    pack_type: str  # "quiz" or "document"
    total_units: int
    recorded_units: int
    unrecorded_units: int
    total_chars: int
    recorded_chars: int
    unrecorded_chars: int
    estimated_credits: float
    units: list[RecordingUnit]


@dataclass(frozen=True)
class AggregatedEstimation:
    """Aggregated estimation across multiple packs."""

    pack_estimations: list[PackEstimation]
    total_units: int
    recorded_units: int
    unrecorded_units: int
    total_chars: int
    recorded_chars: int
    unrecorded_chars: int
    estimated_credits: float


def _is_recorded(item: SokqaDocumentItem | SokqaQuestion, unit_kind: str, choice_index: int | None = None) -> bool:
    """Check if a recording unit is already recorded.

    Args:
        item: The document item or question
        unit_kind: "document", "question", "choice", or "explanation"
        choice_index: The choice index when unit_kind is "choice"

    Returns:
        True if already recorded (has audio URL), False otherwise
    """
    if not item.tts:
        return False
    if unit_kind == "document":
        return bool(getattr(item.tts, "audioUrl", None))
    if unit_kind == "question":
        return bool(getattr(item.tts, "questionAudioUrl", None))
    if unit_kind == "explanation":
        return bool(getattr(item.tts, "explanationAudioUrl", None))
    if unit_kind == "choice":
        urls = getattr(item.tts, "choiceAudioUrls", None)
        return bool(urls and choice_index is not None and choice_index < len(urls) and urls[choice_index])
    return False


def _get_document_text(item: SokqaDocumentItem, text_source: RecordingTextSource = "raw") -> str:
    """Get the recording target text for a document item.

    raw: item.text
    corrected: item.tts.text > item.text
    """
    if text_source == "corrected" and item.tts and item.tts.text:
        return item.tts.text
    return item.text


def _get_question_text(question: SokqaQuestion, text_source: RecordingTextSource = "raw") -> str:
    """Get the recording target text for a question."""
    if text_source == "corrected" and question.tts and question.tts.questionText:
        return question.tts.questionText
    return question.question


def _get_choice_text(question: SokqaQuestion, index: int, text_source: RecordingTextSource = "raw") -> str:
    """Get the recording target text for a choice."""
    if text_source == "corrected" and question.tts and question.tts.choiceTexts and index < len(question.tts.choiceTexts):
        return question.tts.choiceTexts[index]
    return question.choices[index]


def _get_explanation_text(question: SokqaQuestion, text_source: RecordingTextSource = "raw") -> str:
    """Get the recording target text for an explanation."""
    if text_source == "corrected" and question.tts and question.tts.explanationText:
        return question.tts.explanationText
    return question.explanation


def extract_recording_units_from_quiz_pack(
    pack: SokqaQuizPack,
    text_source: RecordingTextSource = "raw",
) -> list[RecordingUnit]:
    """Extract all recording units from a quiz pack."""
    units: list[RecordingUnit] = []

    for question in pack.questions:
        q_id = question.id

        # Question text
        q_text = _get_question_text(question, text_source)
        units.append(
            RecordingUnit(
                item_id=f"q_{q_id}_question",
                text=q_text,
                char_count=len(q_text),
                pack_id=pack.id,
                pack_type="quiz",
                kind="question",
                is_recorded=_is_recorded(question, "question"),
            )
        )

        # Choice texts
        for i in range(len(question.choices)):
            choice_text = _get_choice_text(question, i, text_source)
            units.append(
                RecordingUnit(
                    item_id=f"q_{q_id}_choice_{i}",
                    text=choice_text,
                    char_count=len(choice_text),
                    pack_id=pack.id,
                    pack_type="quiz",
                    kind="choice",
                    is_recorded=_is_recorded(question, "choice", i),
                )
            )

        # Explanation text
        exp_text = _get_explanation_text(question, text_source)
        units.append(
            RecordingUnit(
                item_id=f"q_{q_id}_explanation",
                text=exp_text,
                char_count=len(exp_text),
                pack_id=pack.id,
                pack_type="quiz",
                kind="explanation",
                is_recorded=_is_recorded(question, "explanation"),
            )
        )

    return units


def extract_recording_units_from_document_pack(
    pack: SokqaDocumentPack,
    text_source: RecordingTextSource = "raw",
) -> list[RecordingUnit]:
    """Extract all recording units from a document pack."""
    units: list[RecordingUnit] = []

    for item in pack.documents:
        text = _get_document_text(item, text_source)
        units.append(
            RecordingUnit(
                item_id=f"doc_{item.id}",
                text=text,
                char_count=len(text),
                pack_id=pack.id,
                pack_type="document",
                kind="document",
                is_recorded=_is_recorded(item, "document"),
            )
        )

    return units


def extract_recording_units(
    pack: SokqaQuizPack | SokqaDocumentPack,
    text_source: RecordingTextSource = "raw",
) -> list[RecordingUnit]:
    """Extract recording units from a pack (quiz or document)."""
    if isinstance(pack, SokqaQuizPack):
        return extract_recording_units_from_quiz_pack(pack, text_source)
    return extract_recording_units_from_document_pack(pack, text_source)


def estimate_pack(
    pack: SokqaQuizPack | SokqaDocumentPack,
    credit_per_char: float | None = None,
    text_source: RecordingTextSource = "raw",
) -> PackEstimation:
    """Estimate credit cost for a single pack.

    Args:
        pack: The quiz pack or document pack
        credit_per_char: Credit cost per character. Uses config default if not provided.

    Returns:
        PackEstimation with detailed breakdown
    """
    settings = get_settings()
    rate = credit_per_char if credit_per_char is not None else settings.tts_credit_per_char

    units = extract_recording_units(pack, text_source)

    total_units = len(units)
    recorded_units = 0
    unrecorded_units = 0
    total_chars = 0
    recorded_chars = 0
    unrecorded_chars = 0

    for unit in units:
        is_rec = unit.is_recorded
        total_chars += unit.char_count
        if is_rec:
            recorded_units += 1
            recorded_chars += unit.char_count
        else:
            unrecorded_units += 1
            unrecorded_chars += unit.char_count

    estimated_credits = unrecorded_chars * rate

    return PackEstimation(
        pack_id=pack.id,
        pack_type=pack.type,
        total_units=total_units,
        recorded_units=recorded_units,
        unrecorded_units=unrecorded_units,
        total_chars=total_chars,
        recorded_chars=recorded_chars,
        unrecorded_chars=unrecorded_chars,
        estimated_credits=estimated_credits,
        units=units,
    )


def estimate_packs(
    packs: list[SokqaQuizPack | SokqaDocumentPack],
    credit_per_char: float | None = None,
    text_source: RecordingTextSource = "raw",
) -> AggregatedEstimation:
    """Estimate credit cost for multiple packs (aggregated).

    Args:
        packs: List of quiz packs and/or document packs
        credit_per_char: Credit cost per character. Uses config default if not provided.

    Returns:
        AggregatedEstimation with per-pack and total breakdown
    """
    settings = get_settings()
    rate = credit_per_char if credit_per_char is not None else settings.tts_credit_per_char

    pack_estimations = [estimate_pack(pack, rate, text_source) for pack in packs]

    total_units = sum(p.total_units for p in pack_estimations)
    recorded_units = sum(p.recorded_units for p in pack_estimations)
    unrecorded_units = sum(p.unrecorded_units for p in pack_estimations)
    total_chars = sum(p.total_chars for p in pack_estimations)
    recorded_chars = sum(p.recorded_chars for p in pack_estimations)
    unrecorded_chars = sum(p.unrecorded_chars for p in pack_estimations)
    estimated_credits = unrecorded_chars * rate

    return AggregatedEstimation(
        pack_estimations=pack_estimations,
        total_units=total_units,
        recorded_units=recorded_units,
        unrecorded_units=unrecorded_units,
        total_chars=total_chars,
        recorded_chars=recorded_chars,
        unrecorded_chars=unrecorded_chars,
        estimated_credits=estimated_credits,
    )


def estimation_to_dict(estimation: PackEstimation | AggregatedEstimation) -> dict[str, Any]:
    """Convert estimation result to a JSON-serializable dict."""
    if isinstance(estimation, PackEstimation):
        return {
            "packId": estimation.pack_id,
            "packType": estimation.pack_type,
            "totalUnits": estimation.total_units,
            "recordedUnits": estimation.recorded_units,
            "unrecordedUnits": estimation.unrecorded_units,
            "totalChars": estimation.total_chars,
            "recordedChars": estimation.recorded_chars,
            "unrecordedChars": estimation.unrecorded_chars,
            "estimatedCredits": estimation.estimated_credits,
            "units": [
                {
                    "itemId": u.item_id,
                    "text": u.text,
                    "charCount": u.char_count,
                    "packId": u.pack_id,
                    "packType": u.pack_type,
                    "kind": u.kind,
                    "isRecorded": u.is_recorded,
                }
                for u in estimation.units
            ],
        }
    else:
        return {
            "packEstimations": [estimation_to_dict(p) for p in estimation.pack_estimations],
            "totalUnits": estimation.total_units,
            "recordedUnits": estimation.recorded_units,
            "unrecordedUnits": estimation.unrecorded_units,
            "totalChars": estimation.total_chars,
            "recordedChars": estimation.recorded_chars,
            "unrecordedChars": estimation.unrecorded_chars,
            "estimatedCredits": estimation.estimated_credits,
        }
