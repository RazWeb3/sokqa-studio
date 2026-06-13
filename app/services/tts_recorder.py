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
    audio_path: str | None = None
    error: str | None = None
    used_text_source: str = "raw"


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
    force_rerecord: bool = False,
    language_code: str | None = None,
    voice_name: str | None = None,
    speaking_rate: float | None = None,
    pitch: float | None = None,
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
        audio_namespace=_audio_namespace_from_file_name(file.name),
        storage_client=storage,
        synthesize_fn=synthesize_fn,
        max_concurrency=max_concurrency,
        force_rerecord=force_rerecord,
        language_code=language_code,
        voice_name=voice_name,
        speaking_rate=speaking_rate,
        pitch=pitch,
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
    audio_namespace: str | None = None,
    storage_client: StorageClient | None = None,
    synthesize_fn: Synthesizer = synthesize_text_to_mp3,
    max_concurrency: int | None = None,
    force_rerecord: bool = False,
    language_code: str | None = None,
    voice_name: str | None = None,
    speaking_rate: float | None = None,
    pitch: float | None = None,
) -> RecordingSummary:
    """Record unrecorded units, upload MP3 files, and attach audio URLs in-place."""
    storage = storage_client or StorageClient()
    asset_base_url = _asset_base_url(storage, storage_prefix)
    targets = [unit for unit in units if unit.pack_id == pack.id and (force_rerecord or not unit.is_recorded)]
    skipped_units = len(units) - len(targets)
    workers = _max_concurrency(max_concurrency)
    namespace = _safe_audio_stem(audio_namespace or pack.id)

    def record_unit(unit: RecordingUnit) -> RecordingResult:
        try:
            audio_path = f"audio/{namespace}__{_safe_audio_stem(unit.item_id)}.mp3"
            audio = _synthesize_audio(
                synthesize_fn,
                unit.text,
                language_code=language_code,
                voice_name=voice_name,
                speaking_rate=speaking_rate,
                pitch=pitch,
            )
            audio_url = storage.save_bytes(
                unit.pack_id,
                audio_path,
                audio,
                content_type="audio/mpeg",
                storage_prefix=storage_prefix,
            )
            return RecordingResult(
                unit_id=unit.item_id,
                success=True,
                audio_url=audio_url,
                audio_path=audio_path,
                used_text_source=unit.used_text_source,
            )
        except Exception as exc:
            return RecordingResult(
                unit_id=unit.item_id,
                success=False,
                error=str(exc),
                used_text_source=unit.used_text_source,
            )

    results: list[RecordingResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(record_unit, unit) for unit in targets]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    unit_by_id = {unit.item_id: unit for unit in targets}
    for result in results:
        if result.success and result.audio_path:
            pack.assetBaseUrl = asset_base_url
            _attach_audio_path(pack, unit_by_id[result.unit_id], result.audio_path)

    failed_unit_ids = [result.unit_id for result in results if not result.success]
    return RecordingSummary(
        total_units=len(units),
        skipped_units=skipped_units,
        success_count=sum(1 for result in results if result.success),
        failure_count=len(failed_unit_ids),
        failed_unit_ids=failed_unit_ids,
        results=sorted(results, key=lambda result: result.unit_id),
    )


def _synthesize_audio(
    synthesize_fn: Synthesizer,
    text: str,
    *,
    language_code: str | None = None,
    voice_name: str | None = None,
    speaking_rate: float | None = None,
    pitch: float | None = None,
) -> bytes:
    if synthesize_fn is synthesize_text_to_mp3:
        return synthesize_text_to_mp3(
            text,
            language_code=language_code,
            voice_name=voice_name,
            speaking_rate=speaking_rate,
            pitch=pitch,
        )
    return synthesize_fn(text)


def _max_concurrency(value: int | None = None) -> int:
    configured = value if value is not None else get_settings().cloud_tts_max_concurrency
    return min(max(1, int(configured)), 10)


def _asset_base_url(storage: StorageClient, storage_prefix: str) -> str:
    if hasattr(storage, "public_url_for_prefix"):
        return storage.public_url_for_prefix(storage_prefix)
    return f"{get_settings().public_base_url.rstrip('/')}/{storage_prefix.strip('/')}"


def _safe_audio_stem(item_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", item_id).strip("._")
    return safe or "audio"


def _audio_namespace_from_file_name(file_name: str) -> str:
    stem = file_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if stem.lower().endswith(".json"):
        stem = stem[:-5]
    return _safe_audio_stem(stem)


def _attach_audio_path(pack: SokqaDocumentPack | SokqaQuizPack, unit: RecordingUnit, audio_path: str) -> None:
    if isinstance(pack, SokqaDocumentPack):
        _attach_document_audio_path(pack, unit, audio_path)
    else:
        _attach_quiz_audio_path(pack, unit, audio_path)


def clear_pack_audio_urls(pack: SokqaDocumentPack | SokqaQuizPack, units: list[RecordingUnit]) -> int:
    """Clear audio URL fields for selected recording units in-place."""
    cleared = 0
    for unit in units:
        if unit.pack_id != pack.id:
            continue
        if isinstance(pack, SokqaDocumentPack):
            cleared += _clear_document_audio_url(pack, unit)
        else:
            cleared += _clear_quiz_audio_url(pack, unit)
    if not _pack_has_audio_reference(pack):
        pack.assetBaseUrl = None
    return cleared


def _attach_document_audio_path(pack: SokqaDocumentPack, unit: RecordingUnit, audio_path: str) -> None:
    item_id = _document_id_from_unit(unit.item_id)
    for item in pack.documents:
        if item.id == item_id:
            item.tts = item.tts or DocumentTts()
            item.tts.audioPath = audio_path
            item.tts.audioUrl = None
            return


def _clear_document_audio_url(pack: SokqaDocumentPack, unit: RecordingUnit) -> int:
    item_id = _document_id_from_unit(unit.item_id)
    for item in pack.documents:
        if item.id == item_id and item.tts and (item.tts.audioUrl or item.tts.audioPath):
            item.tts.audioPath = None
            item.tts.audioUrl = None
            if _document_tts_is_empty(item.tts):
                item.tts = None
            return 1
    return 0


def _attach_quiz_audio_path(pack: SokqaQuizPack, unit: RecordingUnit, audio_path: str) -> None:
    question_id, choice_index = _quiz_target_from_unit(unit.item_id, unit.kind)
    for question in pack.questions:
        if question.id != question_id:
            continue
        question.tts = question.tts or QuizTts()
        if unit.kind == "question":
            question.tts.questionAudioPath = audio_path
            question.tts.questionAudioUrl = None
        elif unit.kind == "explanation":
            question.tts.explanationAudioPath = audio_path
            question.tts.explanationAudioUrl = None
        elif unit.kind == "choice" and choice_index is not None:
            paths = list(question.tts.choiceAudioPaths or [])
            while len(paths) < len(question.choices):
                paths.append(None)
            paths[choice_index] = audio_path
            question.tts.choiceAudioPaths = paths
            question.tts.choiceAudioUrls = None
        return


def _clear_quiz_audio_url(pack: SokqaQuizPack, unit: RecordingUnit) -> int:
    question_id, choice_index = _quiz_target_from_unit(unit.item_id, unit.kind)
    for question in pack.questions:
        if question.id != question_id or not question.tts:
            continue
        if unit.kind == "question" and (question.tts.questionAudioUrl or question.tts.questionAudioPath):
            question.tts.questionAudioPath = None
            question.tts.questionAudioUrl = None
            _cleanup_quiz_audio_fields(question.tts)
            if _quiz_tts_is_empty(question.tts):
                question.tts = None
            return 1
        if unit.kind == "explanation" and (question.tts.explanationAudioUrl or question.tts.explanationAudioPath):
            question.tts.explanationAudioPath = None
            question.tts.explanationAudioUrl = None
            _cleanup_quiz_audio_fields(question.tts)
            if _quiz_tts_is_empty(question.tts):
                question.tts = None
            return 1
        if unit.kind == "choice" and choice_index is not None:
            urls = list(question.tts.choiceAudioUrls or [])
            paths = list(question.tts.choiceAudioPaths or [])
            has_url = choice_index < len(urls) and bool(urls[choice_index])
            has_path = choice_index < len(paths) and bool(paths[choice_index])
            if has_url or has_path:
                if choice_index < len(urls):
                    urls[choice_index] = None
                    question.tts.choiceAudioUrls = urls
                if choice_index < len(paths):
                    paths[choice_index] = None
                    question.tts.choiceAudioPaths = paths
                _cleanup_quiz_audio_fields(question.tts)
                if _quiz_tts_is_empty(question.tts):
                    question.tts = None
                return 1
    return 0


def _document_tts_is_empty(tts: DocumentTts) -> bool:
    return not tts.model_dump(exclude_none=True)


def _cleanup_quiz_audio_fields(tts: QuizTts) -> None:
    if tts.choiceAudioUrls is not None and not any(tts.choiceAudioUrls):
        tts.choiceAudioUrls = None
    if tts.choiceAudioPaths is not None and not any(tts.choiceAudioPaths):
        tts.choiceAudioPaths = None


def _quiz_tts_is_empty(tts: QuizTts) -> bool:
    return not tts.model_dump(exclude_none=True)


def _pack_has_audio_reference(pack: SokqaDocumentPack | SokqaQuizPack) -> bool:
    if isinstance(pack, SokqaDocumentPack):
        return any(item.tts and (item.tts.audioPath or item.tts.audioUrl) for item in pack.documents)
    for question in pack.questions:
        if not question.tts:
            continue
        if question.tts.questionAudioPath or question.tts.questionAudioUrl:
            return True
        if question.tts.explanationAudioPath or question.tts.explanationAudioUrl:
            return True
        if any(question.tts.choiceAudioPaths or []) or any(question.tts.choiceAudioUrls or []):
            return True
    return False


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
