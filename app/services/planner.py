import re
from typing import Any

from app.config import get_settings
from app.schemas.common import ReadingPattern, normalize_tts_reading_mode
from app.schemas.request import PlanPackRequest, QuizPackSpec
from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack
from app.services.gemini_client import GeminiClient
from app.services.pack_metadata import resolve_creator_id
from app.services.source_material import normalize_source, source_prompt_block
from app.utils.ids import new_opaque_id, path_token, slugify


SCALE_CHAPTER_RANGES = {
    "quick": (3, 5),
    "standard": (6, 10),
}

RANGE_TITLES = {
    2: ["前半の理解チェック", "後半の理解チェック"],
    3: ["前半の理解チェック", "中盤の理解チェック", "後半の理解チェック"],
    4: ["序盤の理解チェック", "前半の理解チェック", "後半の理解チェック", "終盤の理解チェック"],
}

RANGE_PURPOSES = ["key_concepts", "application", "application", "application"]
MAX_READING_PATTERN_COUNT = 8


FALLBACK_READING_PATTERNS = [
    ReadingPattern(
        id="alphabet_abbreviations",
        title="英略語はアルファベット読みで扱う",
        description="IT、AI、API、OS などの英略語は、必要に応じてカタカナのアルファベット読みとして扱う方針です。",
        examples=["IT -> アイティー", "API -> エーピーアイ", "OS -> オーエス"],
        recommended=True,
    ),
    ReadingPattern(
        id="dot_notation",
        title="ドット記法やファイル名を読み下す",
        description=".git、.env、.gitignore、app.config のようなドットや記号を含む表記を、読み上げで自然に聞こえるように扱う方針です。",
        examples=[".git -> ドット ギット", ".env -> ドット イーエヌブイ", ".gitignore -> ドット ギットイグノア"],
        recommended=True,
    ),
    ReadingPattern(
        id="technical_commands",
        title="コマンドや技術用語を読み下す",
        description="git checkout や npm install のようなコマンド・技術用語を、聞き取りやすい読みとして扱う方針です。",
        examples=["git checkout -> ギット チェックアウト", "npm install -> エヌピーエム インストール"],
        recommended=False,
    ),
    ReadingPattern(
        id="camel_case_terms",
        title="キャメルケースや区切り語を読みやすくする",
        description="localStorage や accessToken のような区切りのある技術語を、自然なまとまりで読めるように扱う方針です。",
        examples=["localStorage -> ローカルストレージ", "accessToken -> アクセストークン"],
        recommended=False,
    ),
    ReadingPattern(
        id="symbols_and_versions",
        title="記号・バージョン番号を聞き取りやすくする",
        description="スラッシュ、ハイフン、バージョン番号などを、聞き取りやすい読みとして扱う方針です。",
        examples=["v1.2 -> バージョン いち てん に", "A/B -> エー スラッシュ ビー"],
        recommended=False,
    ),
]


def _document_count(request: PlanPackRequest) -> int:
    if request.documentCount:
        return request.documentCount
    if request.scale == "quick":
        return 4
    if request.scale == "standard":
        return 8
    return 8


def _question_count(request: PlanPackRequest) -> int:
    return 20 if request.scale == "quick" else 30


def _range_quiz_pack_count(scale: str, document_count: int) -> int:
    if scale in {"quick", "standard"}:
        return 2
    if document_count <= 6:
        return 2
    if document_count <= 10:
        return 3
    return 4


def _split_document_ids(document_ids: list[str], chunk_count: int) -> list[list[str]]:
    if chunk_count <= 0:
        return []
    base_size, remainder = divmod(len(document_ids), chunk_count)
    chunks = []
    start = 0
    for index in range(chunk_count):
        size = base_size + (1 if index < remainder else 0)
        end = start + size
        chunks.append(document_ids[start:end])
        start = end
    return chunks


def _section_count(request: PlanPackRequest) -> int:
    if request.sectionsPerDocument:
        return request.sectionsPerDocument
    return 40


def _requested_document_count(request: PlanPackRequest) -> str:
    if request.documentCount:
        return (
            f"{request.documentCount} chapters exactly. "
            "You must return exactly this many documents."
        )
    if request.scale == "auto":
        return (
            "Not specified. Scale is auto: there is no chapter-count range constraint. "
            "Freely decide the optimal number of chapters for the theme, target user, and difficulty."
        )
    min_count, max_count = SCALE_CHAPTER_RANGES[request.scale]
    return (
        f"Not specified. Scale is {request.scale}: return {min_count}-{max_count} chapters. "
        "Within that range, optimize the chapter count for the theme, target user, and difficulty."
    )


def _requested_section_count(request: PlanPackRequest) -> str:
    if request.sectionsPerDocument:
        return (
            f"{request.sectionsPerDocument} sections per document exactly. "
            "Every document.targetSectionCount must use this number."
        )
    return (
        "Not specified. Propose a suitable targetSectionCount from 30 to 50 for each chapter. "
        "Optimize within this range per chapter; do not vary section count by scale."
    )


def _fallback_short_title(request: PlanPackRequest) -> str:
    text = re.sub(r"\s+", "", request.theme).strip()
    text = re.sub(r"(学習パック|講座|コース)$", "", text)
    if not text:
        return "Sokqa"
    if len(text) > 16:
        return text[:16]
    return text


def _effective_request_tts_mode(request: PlanPackRequest) -> str:
    if not request.includeTts or not request.enableTtsOptimize:
        return "none"
    return normalize_tts_reading_mode(request.ttsReadingMode) or get_settings().tts_reading_mode


def _planner_prompt(request: PlanPackRequest) -> str:
    source_block = source_prompt_block(request.sourceText, request.sourceMode)
    source_section = f"\n\n{source_block}" if source_block else ""
    tts_mode = _effective_request_tts_mode(request)
    reading_pattern_rules = (
        """
- Also propose optional reading-pattern policies that may help TTS generation for this theme.
- proposedReadingPatterns are selectable policies, not fixed word dictionaries. Do not mix them with ttsRules.
- Each reading pattern should describe a general reading strategy, include 1 to 3 examples, and use a stable snake_case id.
- Consider these common categories and propose the ones that are relevant to the theme:
  - dot notation and symbol-heavy file/config names, e.g. ".git -> ドットギット", ".env -> ドットイーエヌブイ", ".gitignore -> ドットギットイグノア".
  - alphabet reading for abbreviations, e.g. "OS -> オーエス", "API -> エーピーアイ", "URL -> ユーアールエル".
  - commands and technical phrases, e.g. "git checkout -> ギット チェックアウト".
  - camelCase or delimiter-separated terms, e.g. "localStorage -> ローカルストレージ".
- Prioritize categories that match the theme/source text. Do not force unrelated patterns just to fill the list.
- Examples must use the concrete "source -> reading" format so users can judge the pattern quickly.
""".rstrip()
        if tts_mode == "llm"
        else """
- Do not propose reading-pattern policies because the selected TTS mode will not use them.
- Return proposedReadingPatterns as an empty array.
""".rstrip()
    )
    return f"""
Return strict JSON only. Do not use markdown fences.

Design a Sokqa CoursePlan outline for a learning pack.
The user will review and edit this outline before generation, so focus on a concrete, useful chapter plan.

Input:
- theme: {request.theme}
- targetUser: {request.targetUser}
- difficulty: {request.difficulty}
- scale: {request.scale}
- language: {request.language}
- requested documentCount: {_requested_document_count(request)}
- requested sectionsPerDocument: {_requested_section_count(request)}
{source_section}

Rules:
- The documents array is the most important output.
- Also return shortTitle: a short pack identifier used as a title prefix, such as "Git入門". Keep it concise.
- Chapter titles must describe the actual topic content. Do not return generic titles such as "第1章" or "{request.theme} 第1章".
- The document order must be a natural learning path from basics to application/review.
- Each document must have a unique, theme-specific title.
- Each document.goal must describe what the learner will understand in that specific chapter.
- Each document.keyPoints must be specific to that chapter. Do not reuse the same keyPoints across chapters.
- If documentCount was specified, return exactly that many documents.
- If documentCount was not specified and scale is quick, return 3-5 documents.
- If documentCount was not specified and scale is standard, return 6-10 documents.
- If documentCount was not specified and scale is auto, there is no range constraint; propose the optimal chapter count for the theme and difficulty.
- If sectionsPerDocument was specified, every targetSectionCount must exactly match it.
- keyPoints should contain 3 to 6 concise items.
- If sectionsPerDocument was not specified, targetSectionCount must be an integer from 30 to 50 for every document.
- targetSectionCount must be an integer from 1 to 120.
{reading_pattern_rules}

Return this JSON shape:
{{
  "title": "pack title",
  "shortTitle": "short pack identifier",
  "description": "short description, including why this chapter count fits if documentCount was not specified",
  "proposedReadingPatterns": [
    {{
      "id": "alphabet_abbreviations",
      "title": "short pattern title",
      "description": "what to do when generating learner text",
      "examples": ["IT -> アイティー"],
      "recommended": true
    }}
  ],
  "documents": [
    {{
      "title": "specific chapter title",
      "goal": "chapter-specific learning goal",
      "keyPoints": ["specific point 1", "specific point 2", "specific point 3"],
      "targetSectionCount": 40
    }}
  ]
}}
""".strip()


def _build_quiz_packs(request: PlanPackRequest, document_ids: list[str]) -> list[PlanQuizPack]:
    if request.quizPacks:
        return [
            PlanQuizPack(
                id=slugify(spec.id, "quiz"),
                title=spec.title,
                purpose=spec.purpose if spec.purpose in {"key_concepts", "application", "integrated_review"} else "custom",
                questionCount=spec.questionCount,
                difficulty=spec.difficulty,
                sourceDocumentIds=document_ids,
            )
            for spec in request.quizPacks
        ]

    range_count = _range_quiz_pack_count(request.scale, len(document_ids))
    chunks = _split_document_ids(document_ids, range_count)
    question_count = _question_count(request)
    quiz_packs = [
        PlanQuizPack(
            id=f"quiz_range_{index + 1:02d}",
            title=RANGE_TITLES[range_count][index],
            purpose=RANGE_PURPOSES[index],
            questionCount=question_count,
            difficulty=request.difficulty,
            sourceDocumentIds=chunk,
        )
        for index, chunk in enumerate(chunks)
    ]
    quiz_packs.append(
        PlanQuizPack(
            id="quiz_integrated_review",
            title="総合・応用クイズ",
            purpose="integrated_review",
            questionCount=question_count,
            difficulty=request.difficulty,
            sourceDocumentIds=document_ids,
        )
    )
    return quiz_packs


def _fallback_documents(request: PlanPackRequest) -> list[PlanDocument]:
    count = _document_count(request)
    section_count = _section_count(request)
    documents = []
    for index in range(1, count + 1):
        doc_id = f"doc_{index:02d}"
        documents.append(
            PlanDocument(
                id=doc_id,
                title=f"{request.theme}の重要領域 {index}",
                goal=f"{request.targetUser}が{request.theme}の領域{index}で扱う基本事項と実践上の注意点を理解する",
                keyPoints=[
                    f"{request.theme}の領域{index}で最初に押さえる用語",
                    f"領域{index}で起こりやすい誤解",
                    f"{request.targetUser}が実務や学習で使う場面",
                ],
                targetSectionCount=section_count,
            )
        )
    return documents


def _normalize_key_points(value: Any, theme: str, index: int) -> list[str]:
    if not isinstance(value, list):
        return [
            f"{theme}の基本事項",
            "重要用語",
            "実践での使い方",
        ]
    points = [str(item).strip() for item in value if str(item).strip()]
    if len(points) < 3:
        points.extend([f"{theme}の重要ポイント{index}", "関連用語", "確認すべき注意点"])
    return points[:6]


def _sanitize_section_count(value: Any, request: PlanPackRequest) -> int:
    if request.sectionsPerDocument:
        return request.sectionsPerDocument
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = _section_count(request)
    return max(30, min(50, count))


def _documents_from_planner_response(data: dict[str, Any], request: PlanPackRequest) -> list[PlanDocument]:
    raw_documents = data.get("documents")
    if not isinstance(raw_documents, list):
        return []

    if request.documentCount:
        raw_documents = raw_documents[: request.documentCount]

    documents = []
    for index, raw in enumerate(raw_documents, start=1):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        goal = str(raw.get("goal") or "").strip()
        if not title or not goal:
            continue
        documents.append(
            PlanDocument(
                id=f"doc_{index:02d}",
                title=title,
                goal=goal,
                keyPoints=_normalize_key_points(raw.get("keyPoints"), request.theme, index),
                targetSectionCount=_sanitize_section_count(raw.get("targetSectionCount"), request),
            )
        )
    return documents


def _short_title_from_planner_response(data: dict[str, Any], request: PlanPackRequest) -> str:
    short_title = str(data.get("shortTitle") or "").strip()
    if not short_title:
        return _fallback_short_title(request)
    short_title = re.sub(r"\s+", "", short_title)
    return short_title[:16] or _fallback_short_title(request)


def _strip_document_title_prefix(title: str, short_title: str) -> str:
    pattern = rf"^{re.escape(short_title)}\s+\d+\.\s*"
    return re.sub(pattern, "", title).strip()


def _prefix_document_titles(documents: list[PlanDocument], short_title: str) -> list[PlanDocument]:
    titled_documents: list[PlanDocument] = []
    for index, document in enumerate(documents, start=1):
        base_title = _strip_document_title_prefix(document.title, short_title)
        titled_documents.append(document.model_copy(update={"title": f"{short_title} {index}. {base_title}"}))
    return titled_documents


def _document_index(document_id: str, document_ids: list[str]) -> int | None:
    try:
        return document_ids.index(document_id) + 1
    except ValueError:
        match = re.search(r"(\d+)$", document_id)
        return int(match.group(1)) if match else None


def _chapter_range_label(indexes: list[int], total_count: int) -> str:
    if not indexes:
        return f"1〜{total_count}章" if total_count > 1 else "1章"
    start, end = min(indexes), max(indexes)
    return f"{start}章" if start == end else f"{start}〜{end}章"


def _topic_words_for_documents(documents: list[PlanDocument], indexes: list[int], *, max_words: int = 2) -> str:
    words: list[str] = []
    for index in indexes:
        if index < 1 or index > len(documents):
            continue
        document = documents[index - 1]
        source = re.sub(r"^.+?\s+\d+\.\s*", "", document.title).strip()
        parts = re.split(r"[、,・／/と&＆:：\s]+", source)
        for part in parts:
            word = part.strip("（）()「」『』")
            if word and word not in words:
                words.append(word)
            if len(words) >= max_words:
                return "・".join(words)
    return ""


def _title_quiz_packs(quiz_packs: list[PlanQuizPack], documents: list[PlanDocument], short_title: str) -> list[PlanQuizPack]:
    document_ids = [document.id for document in documents]
    total_count = len(documents)
    titled_quizzes: list[PlanQuizPack] = []
    range_index = 1
    for quiz_pack in quiz_packs:
        indexes = [
            index
            for document_id in quiz_pack.sourceDocumentIds
            if (index := _document_index(document_id, document_ids)) is not None
        ]
        if quiz_pack.purpose == "integrated_review" or set(quiz_pack.sourceDocumentIds) == set(document_ids):
            chapter_range = _chapter_range_label(list(range(1, total_count + 1)), total_count)
            title = f"{short_title} 総合確認（{chapter_range}: 全範囲）"
        else:
            chapter_range = _chapter_range_label(indexes, total_count)
            topic = _topic_words_for_documents(documents, indexes)
            suffix = f": {topic}" if topic else ""
            title = f"{short_title} 理解チェック{range_index}（{chapter_range}{suffix}）"
            range_index += 1
        titled_quizzes.append(quiz_pack.model_copy(update={"title": title}))
    return titled_quizzes


def _fallback_reading_patterns(request: PlanPackRequest) -> list[ReadingPattern]:
    patterns = [pattern.model_copy(deep=True) for pattern in FALLBACK_READING_PATTERNS]
    theme_text = f"{request.theme} {request.sourceText or ''}".lower()
    if not any(token in theme_text for token in ["git", "api", "it", "ai", "os", "."]):
        return patterns[:1]
    return patterns


def _reading_pattern_signature(pattern: ReadingPattern) -> str:
    text = " ".join([pattern.id, pattern.title, pattern.description, *pattern.examples]).lower()
    if any(token in text for token in [".git", ".env", ".gitignore", "dot notation", "ドット記法", "ドットファイル"]):
        return "dot_notation"
    if any(token in text for token in ["api", "url", "os", "英略語", "アルファベット"]):
        return "alphabet_abbreviations"
    if any(token in text for token in ["checkout", "install", "command", "コマンド"]):
        return "technical_commands"
    if any(token in text for token in ["camel", "localstorage", "キャメル"]):
        return "camel_case_terms"
    if any(token in text for token in ["version", "slash", "バージョン", "スラッシュ"]):
        return "symbols_and_versions"
    return re.sub(r"\s+", "", pattern.title).lower() or pattern.id


def _merge_reading_patterns(
    proposed: list[ReadingPattern],
    fallback: list[ReadingPattern],
    *,
    max_count: int = MAX_READING_PATTERN_COUNT,
) -> list[ReadingPattern]:
    merged: list[ReadingPattern] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    seen_signatures: set[str] = set()
    for pattern in [*fallback, *proposed]:
        title_key = re.sub(r"\s+", "", pattern.title).lower()
        signature = _reading_pattern_signature(pattern)
        if pattern.id in seen_ids or title_key in seen_titles or signature in seen_signatures:
            continue
        merged.append(pattern)
        seen_ids.add(pattern.id)
        seen_titles.add(title_key)
        seen_signatures.add(signature)
    return merged[:max_count]


def _reading_patterns_from_planner_response(data: dict[str, Any], request: PlanPackRequest) -> list[ReadingPattern]:
    raw_patterns = data.get("proposedReadingPatterns")
    fallback_patterns = _fallback_reading_patterns(request)
    if not isinstance(raw_patterns, list):
        return fallback_patterns[:MAX_READING_PATTERN_COUNT]

    patterns: list[ReadingPattern] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_patterns, start=1):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        description = str(raw.get("description") or "").strip()
        if not title or not description:
            continue
        pattern_id = slugify(str(raw.get("id") or title), f"reading_pattern_{index}")
        if pattern_id in seen_ids:
            pattern_id = f"{pattern_id}_{index}"
        seen_ids.add(pattern_id)
        examples = raw.get("examples") if isinstance(raw.get("examples"), list) else []
        patterns.append(
            ReadingPattern(
                id=pattern_id,
                title=title,
                description=description,
                examples=[str(example).strip() for example in examples if str(example).strip()][:3],
                recommended=bool(raw.get("recommended", False)),
            )
        )
    return _merge_reading_patterns(patterns, fallback_patterns)


def _key_points_signature(document: PlanDocument) -> tuple[str, ...]:
    return tuple(point.strip().lower() for point in document.keyPoints)


def _is_generic_title(title: str, theme: str) -> bool:
    compact = re.sub(r"\s+", "", title)
    theme_compact = re.sub(r"\s+", "", theme)
    generic_patterns = [
        r"^第\d+章$",
        r"^第[一二三四五六七八九十]+章$",
        rf"^{re.escape(theme_compact)}第\d+章$",
        rf"^{re.escape(theme_compact)}第[一二三四五六七八九十]+章$",
    ]
    return any(re.match(pattern, compact) for pattern in generic_patterns)


def _validate_planned_documents(documents: list[PlanDocument], request: PlanPackRequest) -> list[str]:
    errors = []
    if not documents:
        errors.append("documents must not be empty")
    if request.documentCount and len(documents) != request.documentCount:
        errors.append(f"documents must contain exactly {request.documentCount} items")
    if not request.documentCount and request.scale in SCALE_CHAPTER_RANGES and documents:
        min_count, max_count = SCALE_CHAPTER_RANGES[request.scale]
        if not min_count <= len(documents) <= max_count:
            errors.append(f"documents must contain {min_count}-{max_count} items for {request.scale} scale")
    if documents:
        signatures = {_key_points_signature(document) for document in documents}
        if len(documents) > 1 and len(signatures) == 1:
            errors.append("keyPoints must not be identical across all documents")
        for document in documents:
            if _is_generic_title(document.title, request.theme):
                errors.append(f"document title is too generic: {document.title}")
    return errors


def _gemini_plan_parts(
    request: PlanPackRequest, model: str | None
) -> tuple[str | None, str | None, str, list[PlanDocument], list[ReadingPattern]]:
    data = GeminiClient().generate_json(_planner_prompt(request), model=model)
    title = str(data.get("title") or "").strip() or None
    short_title = _short_title_from_planner_response(data, request)
    description = str(data.get("description") or "").strip() or None
    documents = _documents_from_planner_response(data, request)
    reading_patterns = _reading_patterns_from_planner_response(data, request)
    errors = _validate_planned_documents(documents, request)
    if errors:
        raise ValueError("; ".join(errors))
    return title, description, short_title, documents, reading_patterns


def create_course_plan(request: PlanPackRequest, model: str | None = None) -> CoursePlan:
    settings = get_settings()
    pack_id = slugify(request.theme, "sokqa_pack")
    slug = path_token(request.slug or slugify(request.theme, "sokqa-pack").replace("_", "-"), pack_id)
    title = f"{request.theme} 学習パック"
    description = f"{request.targetUser}向けの{request.theme}用Sokqa学習パックです。"
    source_text, source_mode = normalize_source(request.sourceText, request.sourceMode)
    short_title = _fallback_short_title(request)
    tts_mode = _effective_request_tts_mode(request)

    if settings.gemini_provider == "gemini":
        try:
            planned_title, planned_description, short_title, documents, reading_patterns = _gemini_plan_parts(request, model)
            title = planned_title or title
            description = planned_description or description
        except Exception:
            documents = _fallback_documents(request)
            reading_patterns = _fallback_reading_patterns(request)
    else:
        documents = _fallback_documents(request)
        reading_patterns = _fallback_reading_patterns(request)

    validation_errors = _validate_planned_documents(documents, request)
    if validation_errors:
        raise ValueError("; ".join(validation_errors))

    tts_rules = request.userTtsRules if request.includeTts else []
    if tts_mode != "llm":
        reading_patterns = []
    documents = _prefix_document_titles(documents, short_title)
    quiz_packs = _title_quiz_packs(_build_quiz_packs(request, [document.id for document in documents]), documents, short_title)

    return CoursePlan(
        id=pack_id,
        creatorId=resolve_creator_id(request.creatorId),
        creatorDisplayName=request.creatorDisplayName,
        contentId=path_token(request.contentId or new_opaque_id("cnt"), "cnt_default"),
        slug=slug,
        shortTitle=short_title,
        title=title,
        description=description,
        language=request.language,
        targetUser=request.targetUser,
        difficulty=request.difficulty,
        scale=request.scale,
        author=settings.sokqa_author,
        enableTtsOptimize=request.includeTts and request.enableTtsOptimize,
        ttsReadingMode=tts_mode,
        ttsLanguageSettings=request.ttsLanguageSettings if tts_mode == "multilingual" else None,
        model=request.model,
        docModel=request.docModel,
        quizModel=request.quizModel,
        plannerModel=request.plannerModel,
        sourceText=source_text,
        sourceMode=source_mode,
        documents=documents,
        quizPacks=quiz_packs,
        ttsRules=tts_rules,
        proposedReadingPatterns=reading_patterns,
    )
