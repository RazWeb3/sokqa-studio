import json
import logging
import re
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


class GeminiClient:
    """Small Gemini JSON adapter.

    The generation services still use deterministic mock content by default.
    This adapter keeps the boundary ready for replacing each generator step
    with Vertex/Gemini JSON prompts without changing route contracts.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    def generate_json(self, prompt: str, model: str | None = None, temperature: float | None = None) -> dict[str, Any]:
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
        response = client.models.generate_content(**request)
        text = getattr(response, "text", "") or ""
        if not text.strip():
            logger.warning(
                "Gemini returned empty text. model=%s finish_reason=%s response=%r",
                request["model"],
                _finish_reason(response),
                repr(response)[:500],
            )
        else:
            logger.debug(
                "Gemini raw text prefix. model=%s finish_reason=%s text=%r",
                request["model"],
                _finish_reason(response),
                text[:500],
            )
        return parse_json_response(text)

    def test_connection(self) -> dict[str, Any]:
        return self.generate_json(
            'Return strict JSON only with this shape: {"ok": true, "provider": "vertex", "message": "connected"}.',
            model=self.settings.planner_model,
        )


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    return json.loads(cleaned)


def _finish_reason(response: Any) -> Any:
    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            return getattr(candidates[0], "finish_reason", None) or getattr(candidates[0], "finishReason", None)
    except Exception:
        return None
    return None
