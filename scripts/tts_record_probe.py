"""Minimal real Cloud TTS recording probe.

This script intentionally calls Google Cloud Text-to-Speech for at most two
recording units, then saves MP3 files through the configured StorageClient.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings
from app.schemas.sokqa import QuizTts, SokqaDocumentPack, SokqaQuestion, SokqaQuizPack
from app.services.pack_metadata import pack_storage_prefix
from app.services.tts_estimation import RecordingUnit, extract_recording_units
from app.services.tts_recorder import RecordingSummary, record_pack_audio

MAX_UNITS = 2


def _sample_pack() -> SokqaQuizPack:
    return SokqaQuizPack(
        id="tts-record-probe",
        title="Cloud TTS録音確認",
        language="ja",
        questions=[
            SokqaQuestion(
                id="q-probe-1",
                question="AI と IT の違いを確認します。",
                choices=[
                    "AI は人工知能です",
                    "IT は情報技術です",
                    "HTTP は通信で使われます",
                    "CPU は演算を行います",
                ],
                answerIndex=0,
                explanation="短い検証用の録音です。",
                tts=QuizTts(
                    questionText="エーアイ と アイティー の違いを確認します。",
                    choiceTexts=[
                        "エーアイ は人工知能です",
                        "アイティー は情報技術です",
                        "エイチティーティーピー は通信で使われます",
                        "シーピーユー は演算を行います",
                    ],
                    explanationText="短い検証用の録音です。",
                ),
            )
        ],
    )


def _load_pack(path: Path | None) -> SokqaQuizPack | SokqaDocumentPack:
    if path is None:
        return _sample_pack()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("type") == "document":
        return SokqaDocumentPack.model_validate(data)
    if data.get("type") == "quiz":
        return SokqaQuizPack.model_validate(data)
    raise ValueError(f"Unsupported pack type in {path}")


def _default_storage_prefix(pack_id: str) -> str:
    stamp = datetime.now(timezone.utc).astimezone().strftime("v%Y%m%d_%H%M%S")
    return pack_storage_prefix("creator_probe", pack_id, stamp)


def _select_units(units: list[RecordingUnit], limit: int) -> list[RecordingUnit]:
    if limit < 1 or limit > MAX_UNITS:
        raise ValueError(f"--limit must be between 1 and {MAX_UNITS}")
    return [unit for unit in units if not unit.is_recorded][:limit]


def _print_plan(units: list[RecordingUnit]) -> None:
    settings = get_settings()
    total_chars = sum(unit.char_count for unit in units)
    total_credits = total_chars * settings.tts_credit_per_char
    print("Recording plan")
    print(f"- backend: {settings.storage_backend}")
    print(f"- units: {len(units)}")
    print(f"- totalChars: {total_chars}")
    print(f"- estimatedCredits: {total_credits:.6f}")
    for index, unit in enumerate(units, start=1):
        credits = unit.char_count * settings.tts_credit_per_char
        print(f"{index}. {unit.item_id}")
        print(f"   kind: {unit.kind}")
        print(f"   chars: {unit.char_count}")
        print(f"   estimatedCredits: {credits:.6f}")
        print(f"   text: {unit.text}")


def _local_audio_path(storage_prefix: str, audio_url: str) -> str | None:
    settings = get_settings()
    if settings.storage_backend != "local":
        return None
    marker = f"/{storage_prefix}/"
    normalized_url = audio_url.replace("\\", "/")
    if marker not in normalized_url:
        return None
    object_name = normalized_url.split(marker, 1)[1]
    return str(Path.cwd() / settings.local_storage_dir / storage_prefix / object_name)


def _print_summary(summary: RecordingSummary, storage_prefix: str) -> None:
    print("RecordingSummary")
    print(f"- totalUnits: {summary.total_units}")
    print(f"- skippedUnits: {summary.skipped_units}")
    print(f"- successCount: {summary.success_count}")
    print(f"- failureCount: {summary.failure_count}")
    print(f"- failedUnitIds: {summary.failed_unit_ids}")
    print("Results")
    for result in summary.results:
        print(f"- {result.unit_id}: {'success' if result.success else 'failed'}")
        if result.audio_url:
            print(f"  audioUrl: {result.audio_url}")
            local_path = _local_audio_path(storage_prefix, result.audio_url)
            if local_path:
                print(f"  localPath: {local_path}")
        if result.error:
            print(f"  error: {result.error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Record one or two real Cloud TTS audio units.")
    parser.add_argument("--pack-json", type=Path, default=None, help="Optional generated quiz/document JSON file.")
    parser.add_argument("--storage-prefix", default=None, help="Optional storage prefix. Defaults to a probe version path.")
    parser.add_argument("--limit", type=int, default=1, help="Number of units to record. Hard-limited to 1 or 2.")
    args = parser.parse_args()

    try:
        pack = _load_pack(args.pack_json)
        units = _select_units(extract_recording_units(pack), args.limit)
        if not units:
            print("No unrecorded units found.")
            return 0

        storage_prefix = args.storage_prefix or _default_storage_prefix(pack.id)
        print(f"packId: {pack.id}")
        print(f"storagePrefix: {storage_prefix}")
        _print_plan(units)
        summary = record_pack_audio(pack, units, storage_prefix)
        _print_summary(summary, storage_prefix)
        return 0 if summary.failure_count == 0 else 1
    except Exception as exc:
        print(f"Probe failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
