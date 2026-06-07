"""Record Sokqa TTS audio units and attach their URLs to pack data."""

from __future__ import annotations

import concurrent.futures
import re
from dataclasses import dataclass, field
from typing import Callable

from app.config import get_settings
from app.schemas.sokqa import DocumentTts, GeneratedFile, QuizTts, SokqaDocumentPack, SokqaQuizPack
from app.services.storage_client import StorageClient
from app.services.tts_estimation import RecordingUnit
from app.services.tts_synthesizer import synthesize_text_to_mp3

Synthesizer = Callable[[str], bytes]


@dataclass(frozen=True)
class RecordingResult:
    unit_id: str
    success: bool
    audio_url: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class RecordingSummary:
    total_units: int
    skipped_units: int
    success_count: int
    failure_count: int
    failed_unit_ids: list[str] = field(default_factory=list)
    results: list[RecordingResult] = field(default_factory=list)


def record_generated_file_audio(
    file: GeneratedFile,
    units: list[RecordingUnit],
    storage_prefix: str,
    *,
    storage_client: StorageClient | None = None,
    synthesize_fn: Synthesizer = synthesize_text_to_mp3,
    max_concurrency: int | None = None,
) -> RecordingSummary:
    """Record audio for a generated pack file and persist the updated JSON."""
    if file.kind == "document":
        pack = SokqaDocumentPack.model_validate(file.content)
    elif file.kind == "quiz":
        pack = SokqaQuizPack.model_validate(file.content)
    else:
        raise ValueError("file.kind must be document or quiz")

    storage = storage_client or StorageClient()
    summary = record_pack_audio(
        pack,
        units,
        storage_prefix,
        storage_client=storage,
        synthesize_fn=synthesize_fn,
        max_concurrency=max_concurrency,
    )
    file.content = pack.model_dump(exclude_none=True)
    if summary.success_count:
        storage.save_files(pack.id, [file], storage_prefix)
    return summary


def record_pack_audio(
    pack: SokqaDocumentPack | SokqaQuizPack,
    units: list[RecordingUnit],
    storage_prefix: str,
    *,
    storage_client: StorageClient | None = None,
    synthesize_fn: Synthesizer = synthesize_text_to_mp3,
    max_concurrency: int | None = None,
) -> RecordingSummary:
    """Record unrecorded units, upload MP3 files, and attach audio URLs in-place."""
    storage = storage_client or StorageClient()
    targets = [unit for unit in units if not unit.is_recorded and unit.pack_id == pack.id]
    skipped_units = len(units) - len(targets)
    workers = _max_concurrency(max_concurrency)

    def record_unit(unit: RecordingUnit) -> RecordingResult:
        try:
            audio = synthesize_fn(unit.text)
            audio_url = storage.save_bytes(
                unit.pack_id,
                f"audio/{_safe_audio_stem(unit.item_id)}.mp3",
                audio,
                content_type="audio/mpeg",
                storage_prefix=storage_prefix,
            )
            return RecordingResult(unit_id=unit.item_id, success=True, audio_url=audio_url)
        except Exception as exc:
            return RecordingResult(unit_id=unit.item_id, success=False, error=str(exc))

    results: list[RecordingResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(record_unit, unit) for unit in targets]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    unit_by_id = {unit.item_id: unit for unit in targets}
    for result in results:
        if result.success and result.audio_url:
            _attach_audio_url(pack, unit_by_id[result.unit_id], result.audio_url)

    failed_unit_ids = [result.unit_id for result in results if not result.success]
    return RecordingSummary(
        total_units=len(units),
        skipped_units=skipped_units,
        success_count=sum(1 for result in results if result.success),
        failure_count=len(failed_unit_ids),
        failed_unit_ids=failed_unit_ids,
        results=sorted(results, key=lambda result: result.unit_id),
    )


def _max_concurrency(value: int | None = None) -> int:
    configured = value if value is not None else get_settings().cloud_tts_max_concurrency
    return min(max(1, int(configured)), 10)


def _safe_audio_stem(item_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", item_id).strip("._")
    return safe or "audio"


def _attach_audio_url(pack: SokqaDocumentPack | SokqaQuizPack, unit: RecordingUnit, audio_url: str) -> None:
    if isinstance(pack, SokqaDocumentPack):
        _attach_document_audio_url(pack, unit, audio_url)
    else:
        _attach_quiz_audio_url(pack, unit, audio_url)


def _attach_document_audio_url(pack: SokqaDocumentPack, unit: RecordingUnit, audio_url: str) -> None:
    item_id = _document_id_from_unit(unit.item_id)
    for item in pack.documents:
        if item.id == item_id:
            item.tts = item.tts or DocumentTts()
            item.tts.audioUrl = audio_url
            return


def _attach_quiz_audio_url(pack: SokqaQuizPack, unit: RecordingUnit, audio_url: str) -> None:
    question_id, choice_index = _quiz_target_from_unit(unit.item_id, unit.kind)
    for question in pack.questions:
        if question.id != question_id:
            continue
        question.tts = question.tts or QuizTts()
        if unit.kind == "question":
            question.tts.questionAudioUrl = audio_url
        elif unit.kind == "explanation":
            question.tts.explanationAudioUrl = audio_url
        elif unit.kind == "choice" and choice_index is not None:
            urls = list(question.tts.choiceAudioUrls or [])
            while len(urls) < len(question.choices):
                urls.append(None)
            urls[choice_index] = audio_url
            question.tts.choiceAudioUrls = urls
        return


def _document_id_from_unit(unit_id: str) -> str:
    return unit_id[4:] if unit_id.startswith("doc_") else unit_id


def _quiz_target_from_unit(unit_id: str, kind: str) -> tuple[str, int | None]:
    if not unit_id.startswith("q_"):
        return unit_id, None
    body = unit_id[2:]
    if kind == "question" and body.endswith("_question"):
        return body[: -len("_question")], None
    if kind == "explanation" and body.endswith("_explanation"):
        return body[: -len("_explanation")], None
    if kind == "choice":
        question_id, _, index_text = body.rpartition("_choice_")
        try:
            return question_id, int(index_text)
        except ValueError:
            return question_id, None
    return body, None
