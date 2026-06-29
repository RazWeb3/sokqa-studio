import logging
from typing import Any

from app.config import get_settings
from app.services.llm_json import LlmJsonParseContext, parse_llm_json_or_raise

logger = logging.getLogger(__name__)


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
        request: dict[str, Any] = {
            "model": model or self.settings.gemini_model,
            "contents": prompt,
        }
        if temperature is not None:
            request["config"] = {"temperature": temperature}
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
