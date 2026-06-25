from typing import Any

from app.config import get_settings
from app.schemas.request import PlanSuggestConditionsRequest, SuggestedCondition
from app.services.gemini_client import GeminiClient
from app.services.language_detection import language_base
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
    return _inject_source_quality_suggestion(suggestions, request)


def _inject_source_quality_suggestion(
    suggestions: list[SuggestedCondition], request: PlanSuggestConditionsRequest
) -> list[SuggestedCondition]:
    if not request.hasSourceMaterial:
        return suggestions
    suggestion = _source_quality_suggestion(request.displayLanguage)
    if not suggestion:
        return suggestions
    if any(item.id == suggestion.id or item.text == suggestion.text for item in suggestions):
        return suggestions
    merged = [suggestion, *suggestions]
    return merged[:MAX_SUGGESTION_COUNT]


def _source_quality_suggestion(language: str | None) -> SuggestedCondition | None:
    base = language_base(language) or "en"
    templates: dict[str, dict[str, str]] = {
        "ja": {
            "title": "日本語表記・読みの厳密確認",
            "text": "資料内の日本語表記・読み・ローマ字表記は推測で補完せず、必ず参考資料を基準にしてください。不明な読みや表記は推測せず、そのまま扱うか、解釈の可能性として説明してください。",
            "reason": "推測による表記揺れ・誤ローマ字を防ぎ、歌詞など固有表現の誤りを減らすため",
        },
        "en": {
            "title": "Strict Japanese Notation Check",
            "text": "Do not guess Japanese spelling, readings, or romanization found in the reference material. Always follow the source. If something is unclear, keep it as-is or describe it as a possible interpretation.",
            "reason": "Reduces hallucinated readings and wrong romanization in source-based materials.",
        },
        "zh": {
            "title": "严格核对日语表记与读音",
            "text": "不要推测补全参考资料中的日语表记、读音与罗马字。必须以资料为准。遇到不明确的读音或表记，不要猜测，保持原样，或作为可能的解释进行说明。",
            "reason": "减少推测导致的表记偏差与错误罗马字，提升歌词类教材质量。",
        },
        "ko": {
            "title": "일본어 표기·읽기 엄밀 확인",
            "text": "자료에 있는 일본어 표기·읽기·로마자 표기는 추측으로 보완하지 말고 반드시 자료를 기준으로 하세요. 불명확한 경우는 추측하지 말고 그대로 두거나 가능한 해석으로 설명하세요.",
            "reason": "추측으로 인한 표기 오류와 잘못된 로마자를 줄이기 위해서입니다.",
        },
        "es": {
            "title": "Verificación estricta del japonés",
            "text": "No infieras la escritura, la lectura ni la romanización del japonés presentes en el material de referencia. Sigue siempre la fuente. Si algo no está claro, mantenlo tal cual o descríbelo como una interpretación posible.",
            "reason": "Reduce lecturas inventadas y romanizaciones incorrectas en materiales basados en fuentes.",
        },
        "fr": {
            "title": "Vérification stricte du japonais",
            "text": "Ne devine pas l’orthographe, la lecture ni la romanisation du japonais présentes dans la source. Base-toi toujours sur le document. Si un point est ambigu, conserve-le tel quel ou présente-le comme une interprétation possible.",
            "reason": "Réduit les lectures inventées et les romanisations erronées dans les contenus basés sur des sources.",
        },
        "de": {
            "title": "Strenge Prüfung japanischer Notation",
            "text": "Errate keine japanische Schreibweise, Lesung oder Romanisierung aus dem Referenzmaterial. Richte dich immer nach der Quelle. Wenn etwas unklar ist, lass es unverändert oder beschreibe es als mögliche Interpretation.",
            "reason": "Verhindert erfundene Lesungen und falsche Romanisierung bei quellenbasierten Materialien.",
        },
        "it": {
            "title": "Verifica rigorosa del giapponese",
            "text": "Non dedurre grafia, lettura o romanizzazione del giapponese presenti nel materiale di riferimento. Attieniti sempre alla fonte. Se qualcosa è ambiguo, lascialo com’è o descrivilo come possibile interpretazione.",
            "reason": "Riduce letture inventate e romanizzazioni errate nei materiali basati su fonti.",
        },
        "pt": {
            "title": "Verificação rigorosa do japonês",
            "text": "Não deduza a escrita, a leitura nem a romanização do japonês presentes no material de referência. Siga sempre a fonte. Se algo estiver ambíguo, mantenha como está ou descreva como uma interpretação possível.",
            "reason": "Reduz leituras inventadas e romanizações incorretas em materiais baseados em fontes.",
        },
        "id": {
            "title": "Verifikasi ketat notasi Jepang",
            "text": "Jangan menebak penulisan, pembacaan, atau romanisasi Jepang yang ada di materi referensi. Selalu ikuti sumber. Jika ada yang tidak jelas, biarkan apa adanya atau jelaskan sebagai kemungkinan interpretasi.",
            "reason": "Mengurangi pembacaan halusinasi dan romanisasi salah pada materi berbasis sumber.",
        },
    }
    content = templates.get(base) or templates["en"]
    return SuggestedCondition(
        id="strict_japanese_notation_reading",
        title=content["title"][:80],
        text=content["text"][:400],
        reason=content["reason"][:160],
    )


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
            language=request.displayLanguage,
            difficulty=request.difficulty,
            extra={
                "packLanguage": request.language,
                "displayLanguage": request.displayLanguage,
            },
        ),
    )
    return _suggestions_from_response(data)


def _suggestion_prompt(request: PlanSuggestConditionsRequest) -> str:
    additional_conditions = request.customInstructions or "none"
    return f"""
Return strict JSON only. Do not use markdown fences.
出力は必ずJSONのみ。Markdown、説明文、コードブロックは禁止。

入力された theme / targetUser / difficulty / packLanguage / displayLanguage から、Sokqa学習パックの品質を上げる追加条件候補を3〜5件生成してください。
候補はユーザーが採用すると customInstructions にそのまま追記されます。
既存の追加条件と同じ意味の候補は避けてください。
提案タイトル・提案理由・提案内容は、必ず displayLanguage で出力してください。
packLanguage や learningLanguage に引っ張られて出力言語を変えてはいけません。

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
- packLanguage: {request.language}
- displayLanguage: {request.displayLanguage}
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
