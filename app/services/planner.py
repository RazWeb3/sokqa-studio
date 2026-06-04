import re
from typing import Any

from app.config import get_settings
from app.schemas.request import PlanPackRequest, QuizPackSpec
from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack
from app.services.gemini_client import GeminiClient
from app.services.source_material import normalize_source, source_prompt_block
from app.utils.ids import slugify


DEFAULT_QUIZ_PACKS = [
    ("quiz_key_concepts", "基礎理解チェック", "key_concepts"),
    ("quiz_application", "実践理解チェック", "application"),
    ("quiz_integrated_review", "総合復習クイズ", "integrated_review"),
]

SCALE_CHAPTER_RANGES = {
    "quick": (3, 5),
    "standard": (6, 10),
}


def _document_count(request: PlanPackRequest) -> int:
    if request.documentCount:
        return request.documentCount
    if request.scale == "quick":
        return 4
    if request.scale == "standard":
        return 8
    return 8


def _question_count(request: PlanPackRequest) -> int:
    return 10 if request.scale == "quick" else 30


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


def _planner_prompt(request: PlanPackRequest) -> str:
    source_block = source_prompt_block(request.sourceText, request.sourceMode)
    source_section = f"\n\n{source_block}" if source_block else ""
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

Return this JSON shape:
{{
  "title": "pack title",
  "description": "short description, including why this chapter count fits if documentCount was not specified",
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

    packs = DEFAULT_QUIZ_PACKS[:1] if request.scale == "quick" else DEFAULT_QUIZ_PACKS
    return [
        PlanQuizPack(
            id=pack_id,
            title=title,
            purpose=purpose,
            questionCount=_question_count(request),
            difficulty=request.difficulty,
            sourceDocumentIds=document_ids,
        )
        for pack_id, title, purpose in packs
    ]


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


def _gemini_documents(request: PlanPackRequest, model: str | None) -> tuple[str | None, str | None, list[PlanDocument]]:
    data = GeminiClient().generate_json(_planner_prompt(request), model=model)
    title = str(data.get("title") or "").strip() or None
    description = str(data.get("description") or "").strip() or None
    documents = _documents_from_planner_response(data, request)
    errors = _validate_planned_documents(documents, request)
    if errors:
        raise ValueError("; ".join(errors))
    return title, description, documents


def create_course_plan(request: PlanPackRequest, model: str | None = None) -> CoursePlan:
    settings = get_settings()
    pack_id = slugify(request.theme, "sokqa_pack")
    title = f"{request.theme} 学習パック"
    description = f"{request.targetUser}向けの{request.theme}用Sokqa学習パックです。"
    source_text, source_mode = normalize_source(request.sourceText, request.sourceMode)

    if settings.gemini_provider == "gemini":
        try:
            planned_title, planned_description, documents = _gemini_documents(request, model)
            title = planned_title or title
            description = planned_description or description
        except Exception:
            documents = _fallback_documents(request)
    else:
        documents = _fallback_documents(request)

    validation_errors = _validate_planned_documents(documents, request)
    if validation_errors:
        raise ValueError("; ".join(validation_errors))

    tts_rules = request.userTtsRules if request.includeTts else []

    return CoursePlan(
        id=pack_id,
        title=title,
        description=description,
        language=request.language,
        targetUser=request.targetUser,
        difficulty=request.difficulty,
        scale=request.scale,
        author=settings.sokqa_author,
        enableTtsOptimize=request.includeTts and request.enableTtsOptimize,
        ttsReadingMode=request.ttsReadingMode,
        model=request.model,
        docModel=request.docModel,
        quizModel=request.quizModel,
        plannerModel=request.plannerModel,
        sourceText=source_text,
        sourceMode=source_mode,
        documents=documents,
        quizPacks=_build_quiz_packs(request, [document.id for document in documents]),
        ttsRules=tts_rules,
    )
