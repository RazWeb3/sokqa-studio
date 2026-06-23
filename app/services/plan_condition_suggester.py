from typing import Any

from app.config import get_settings
from app.schemas.request import PlanSuggestConditionsRequest, SuggestedCondition
from app.services.gemini_client import GeminiClient
from app.services.llm_json import LlmJsonParseContext
from app.utils.ids import slugify


MIN_SUGGESTION_COUNT = 3
MAX_SUGGESTION_COUNT = 5


BANNED_TTS_TERMS = (
    "tts",
    "読み上げ",
    "発音",
    "読み方",
    "略語読み",
    "カタカナ読み",
    "記号読み",
    "読み補正",
    "音声最適化",
    "読みパターン",
    "発話ルール",
    "読み下す",
    "読む",
    "エーピーアイ",
    "ドット記法",
)


def suggest_conditions(request: PlanSuggestConditionsRequest) -> list[SuggestedCondition]:
    settings = get_settings()
    if settings.gemini_provider != "gemini":
        raise RuntimeError("AI提案の取得に失敗しました。\n時間をおいて再度お試しください。")
    suggestions = _gemini_suggestions(request)
    if not suggestions:
        raise RuntimeError("AI提案の取得に失敗しました。\n時間をおいて再度お試しください。")
    return suggestions


def _gemini_suggestions(request: PlanSuggestConditionsRequest) -> list[SuggestedCondition]:
    data = GeminiClient().generate_json(
        _suggestion_prompt(request),
        model=get_settings().planner_model,
        temperature=0.4,
        parse_context=LlmJsonParseContext(
            generation_unit="plan_suggest_conditions",
            model=get_settings().planner_model,
            theme=request.theme,
            additional_instructions=request.customInstructions,
            language=request.language,
            difficulty=request.difficulty,
        ),
    )
    return _suggestions_from_response(data)


def _suggestion_prompt(request: PlanSuggestConditionsRequest) -> str:
    additional_conditions = request.customInstructions or "none"
    return f"""
Return strict JSON only. Do not use markdown fences.
出力は必ずJSONのみ。Markdown、説明文、コードブロックは禁止。

入力された theme / targetUser / difficulty / language から、Sokqa学習パックの品質を上げる追加条件候補を3〜5件生成してください。
候補はユーザーが採用すると customInstructions にそのまま追記されます。
既存の追加条件と同じ意味の候補は避けてください。

追加条件提案は教材内容・説明方法・出題方針のみ提案してください。

以下の内容は提案しないでください。

- TTS
- 読み上げ
- 発音
- 読み方
- 略語読み
- カタカナ読み
- 記号読み
- 読み補正
- 音声最適化
- 読みパターン
- 発話ルール

これらは読みパターン提案機能の責務です。

Input:
- theme: {request.theme}
- targetUser: {request.targetUser}
- difficulty: {request.difficulty}
- language: {request.language}
- existing customInstructions: {additional_conditions}

JSON schema:
{{
  "suggestions": [
    {{
      "id": "stable_snake_case_id",
      "title": "短い候補名",
      "text": "customInstructionsに追記する具体的な条件文。",
      "reason": "提案理由を短く"
    }}
  ]
}}
""".strip()


def _suggestions_from_response(data: dict[str, Any]) -> list[SuggestedCondition]:
    raw_suggestions = data.get("suggestions")
    if not isinstance(raw_suggestions, list):
        return []
    suggestions: list[SuggestedCondition] = []
    seen_texts: set[str] = set()
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_suggestions, start=1):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        text = str(raw.get("text") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if not title or not text or not reason:
            continue
        if _is_tts_related_suggestion(title, text, reason):
            continue
        if text in seen_texts:
            continue
        suggestion_id = slugify(str(raw.get("id") or title), f"suggestion_{index}")
        if suggestion_id in seen_ids:
            suggestion_id = f"{suggestion_id}_{index}"
        seen_texts.add(text)
        seen_ids.add(suggestion_id)
        suggestions.append(SuggestedCondition(id=suggestion_id, title=title[:80], text=text[:400], reason=reason[:160]))
        if len(suggestions) >= MAX_SUGGESTION_COUNT:
            break
    return suggestions


def _is_tts_related_suggestion(title: str, text: str, reason: str) -> bool:
    content = f"{title} {text} {reason}".lower()
    return any(term.lower() in content for term in BANNED_TTS_TERMS)
