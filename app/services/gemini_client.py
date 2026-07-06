import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.services.llm_json import LlmJsonParseContext, parse_llm_json_or_raise

logger = logging.getLogger(__name__)


@dataclass
class DebugPromptRecord:
    prompt_type: str  # "planner" / "document" / "quiz" / "tts" / "quality" / "fix"
    target: str  # doc_01, quiz_range_01, planner, tts_reading, etc.
    model: str
    prompt: str  # 最終prompt全文
    generated_at: str = ""  # ISO 8601
    characters: int = 0
    doc_title: str = ""
    quiz_title: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat()
        if not self.characters:
            self.characters = len(self.prompt)


_prompt_records: list[DebugPromptRecord] = []


def record_debug_prompt(
    prompt_type: str,
    target: str,
    model: str,
    prompt: str,
    *,
    doc_title: str = "",
    quiz_title: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    if not get_settings().debug_prompts_enabled:
        return
    _prompt_records.append(
        DebugPromptRecord(
            prompt_type=prompt_type,
            target=target,
            model=model,
            prompt=prompt,
            doc_title=doc_title,
            quiz_title=quiz_title,
            extra=extra or {},
        )
    )


def pop_debug_prompts() -> list[DebugPromptRecord]:
    records = list(_prompt_records)
    _prompt_records.clear()
    return records


def _record_prompt_from_context(
    prompt: str,
    resolved_model: str,
    parse_context: LlmJsonParseContext | None,
) -> None:
    if not get_settings().debug_prompts_enabled:
        return
    ctx = parse_context or LlmJsonParseContext()
    unit = ctx.generation_unit

    prompt_type_map = {
        "plan": "planner",
        "doc": "document",
        "quiz": "quiz",
        "tts_reading": "tts_reading",
        "tts_batch_doc": "tts_batch_doc",
        "tts_batch_quiz": "tts_batch_quiz",
        "tts_quiz_question": "tts_quiz_question",
        "tts_decision": "tts_decision",
        "quality_text": "quality_text",
        "quality_tts": "quality_tts",
        "fix_text": "fix_text",
        "fix_tts": "fix_tts",
        "plan_suggest_conditions": "planner",
    }
    prompt_type = prompt_type_map.get(unit, unit)

    target = ""
    doc_title = ""
    quiz_title = ""
    if unit == "doc":
        target = ctx.doc_id or "document"
        doc_title = ctx.title or ""
    elif unit == "quiz":
        target = ctx.quiz_id or "quiz"
        quiz_title = ctx.title or ""
    elif unit == "plan":
        target = "planner"
    elif ctx.doc_id:
        target = ctx.doc_id
    elif ctx.quiz_id:
        target = ctx.quiz_id
    elif ctx.title:
        target = ctx.title
    else:
        target = unit

    record_debug_prompt(
        prompt_type=prompt_type,
        target=target,
        model=resolved_model,
        prompt=prompt,
        doc_title=doc_title,
        quiz_title=quiz_title,
    )


class GeminiClient:
    """Small Gemini JSON adapter.

    The generation services still use deterministic mock content by default.
    This adapter keeps the boundary ready for replacing each generator step
    with Vertex/Gemini JSON prompts without changing route contracts.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    def generate_json(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float | None = None,
        parse_context: LlmJsonParseContext | None = None,
    ) -> dict[str, Any]:
        if self.settings.gemini_provider == "mock":
            raise RuntimeError("GEMINI_PROVIDER=mock; use deterministic local generators.")

        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("google-genai is not installed") from exc

        if self.settings.google_genai_use_vertexai:
            if not self.settings.google_cloud_project:
                raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for Vertex AI mode")
            client = genai.Client(
                vertexai=True,
                project=self.settings.google_cloud_project,
                location=self.settings.google_cloud_location,
            )
        else:
            client = genai.Client()
        resolved_model = model or self.settings.gemini_model
        request: dict[str, Any] = {
            "model": resolved_model,
            "contents": prompt,
        }
        if temperature is not None:
            request["config"] = {"temperature": temperature}

        _record_prompt_from_context(prompt, resolved_model, parse_context)

        try:
            response = client.models.generate_content(**request)
        except Exception as exc:
            logger.warning("gemini.generate finish_reason=%s", None)
            logger.warning(
                "gemini.generate_content_failed error_type=%s error=%s",
                type(exc).__name__,
                repr(exc),
            )
            raise
        finish_reason = _finish_reason(response)
        text = getattr(response, "text", "") or ""
        if not text.strip():
            logger.warning(
                "Gemini returned empty text. model=%s finish_reason=%s response=%r",
                request["model"],
                finish_reason,
                repr(response)[:500],
            )
        else:
            logger.debug(
                "Gemini raw text prefix. model=%s finish_reason=%s text=%r",
                request["model"],
                finish_reason,
                text[:500],
            )
        context = parse_context or LlmJsonParseContext()
        context.model = context.model or request["model"]
        try:
            parsed, method = parse_llm_json_or_raise(text, context)
        except Exception:
            logger.warning("gemini.generate finish_reason=%s", finish_reason)
            raise
        logger.debug("Gemini JSON parsed. model=%s method=%s", request["model"], method)
        return parsed

    def test_connection(self) -> dict[str, Any]:
        return self.generate_json(
            'Return strict JSON only with this shape: {"ok": true, "provider": "vertex", "message": "connected"}.',
            model=self.settings.planner_model,
        )


def parse_json_response(text: str) -> dict[str, Any]:
    parsed, _method = parse_llm_json_or_raise(text)
    return parsed


def _finish_reason(response: Any) -> Any:
    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            return getattr(candidates[0], "finish_reason", None) or getattr(candidates[0], "finishReason", None)
    except Exception:
        return None
    return None