import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class LlmJsonParseContext:
    generation_unit: str = "unknown"
    model: str | None = None
    theme: str | None = None
    doc_id: str | None = None
    quiz_id: str | None = None
    title: str | None = None
    source_text: str | None = None
    additional_instructions: str | None = None
    tts_reading_mode: str | None = None
    language: str | None = None
    difficulty: str | None = None
    scale: str | None = None
    file_name: str | None = None
    phase: str | None = None
    run_index: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def safe_meta(self) -> dict[str, Any]:
        source_text = self.source_text or ""
        additional = self.additional_instructions or ""
        meta = {
            "theme": self.theme,
            "docId": self.doc_id,
            "quizId": self.quiz_id,
            "title": self.title,
            "sourceTextLength": len(source_text),
            "sourceTextPreview": source_text[:200],
            "additionalInstructionsLength": len(additional),
            "ttsReadingMode": self.tts_reading_mode,
            "language": self.language,
            "difficulty": self.difficulty,
            "scale": self.scale,
            "generationUnit": self.generation_unit,
            "model": self.model,
        }
        meta.update(self.extra)
        return meta


class LlmJsonParseError(ValueError):
    def __init__(self, message: str, *, attempts: list[dict[str, str]], saved_prefix: str | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.saved_prefix = saved_prefix


def parse_llm_json_or_raise(raw_text: str, context: LlmJsonParseContext | None = None) -> tuple[Any, str]:
    context = context or LlmJsonParseContext()
    attempts: list[dict[str, str]] = []

    for method, candidate in _parse_candidates(raw_text):
        try:
            return json.loads(candidate), method
        except json.JSONDecodeError as exc:
            attempts.append(_attempt(method, exc))

    for candidate_source in _repair_candidates(raw_text):
        repaired = _repair_json(candidate_source)
        try:
            return json.loads(repaired), "repaired"
        except json.JSONDecodeError as exc:
            attempts.append(_attempt("repaired", exc))

    message = attempts[-1]["error"] if attempts else "No JSON parse attempts were made"
    saved_prefix = save_failed_llm_response(raw_text, context, message, attempts)
    raise LlmJsonParseError(message, attempts=attempts, saved_prefix=saved_prefix)


def save_failed_llm_response(
    raw_text: str,
    context: LlmJsonParseContext,
    error_message: str,
    attempts: list[dict[str, str]],
) -> str:
    output_dir = Path.cwd() / "tmp" / "failed_generations"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    unit = _safe_token(context.generation_unit or "unknown")
    target = _safe_token(context.doc_id or context.quiz_id or "01")
    prefix = f"{timestamp}_{unit}_{target}"

    (output_dir / f"{prefix}_raw.txt").write_text(raw_text, encoding="utf-8")
    error_lines = [
        f"error: {error_message}",
        f"generationUnit: {context.generation_unit}",
        f"docId: {context.doc_id}",
        f"quizId: {context.quiz_id}",
        f"model: {context.model}",
        "",
        "attempts:",
    ]
    error_lines.extend(f"- {attempt['method']}: {attempt['error']}" for attempt in attempts)
    (output_dir / f"{prefix}_error.txt").write_text("\n".join(error_lines), encoding="utf-8")
    (output_dir / f"{prefix}_prompt_meta.json").write_text(
        json.dumps(context.safe_meta(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(output_dir / prefix)


def _parse_candidates(raw_text: str) -> list[tuple[str, str]]:
    cleaned = raw_text.strip()
    candidates = [("direct", cleaned)]
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        candidates.append(("code_block", fence_match.group(1).strip()))
    object_text = _extract_outer_json_object(cleaned)
    if object_text and object_text != cleaned:
        candidates.append(("object_extract", object_text))
    return candidates


def _repair_candidates(raw_text: str) -> list[str]:
    candidates = []
    for _method, candidate in _parse_candidates(raw_text):
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _repair_json(text: str) -> str:
    repaired = text.strip()
    if repaired.lower().startswith("json"):
        repaired = repaired[4:].strip()
    repaired = re.sub(r"^```(?:json)?\s*", "", repaired, flags=re.IGNORECASE)
    repaired = re.sub(r"\s*```$", "", repaired)
    extracted = _extract_outer_json_object(repaired)
    if extracted:
        repaired = extracted
    return re.sub(r",\s*([}\]])", r"\1", repaired)


def _extract_outer_json_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end + 1].strip()


def _attempt(method: str, exc: json.JSONDecodeError) -> dict[str, str]:
    return {
        "method": method,
        "error": f"{exc.msg}: line {exc.lineno} column {exc.colno} (char {exc.pos})",
    }


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    return token.strip("_")[:80] or "unknown"
