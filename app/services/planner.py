from app.config import get_settings
from app.schemas.common import TtsRule
from app.schemas.request import PlanPackRequest, QuizPackSpec
from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack
from app.utils.ids import slugify


DEFAULT_QUIZ_PACKS = [
    ("quiz_key_concepts", "基礎理解チェック", "key_concepts"),
    ("quiz_application", "実践理解チェック", "application"),
    ("quiz_integrated_review", "総合復習クイズ", "integrated_review"),
]

COMMON_TTS_RULES = [
    TtsRule(source="Sokqa", reading="ソッカ", note="Product name"),
    TtsRule(source="AI", reading="エーアイ"),
    TtsRule(source="IT", reading="アイティー"),
    TtsRule(source="API", reading="エーピーアイ"),
    TtsRule(source="UI", reading="ユーアイ"),
    TtsRule(source="UX", reading="ユーエックス"),
    TtsRule(source="DB", reading="データベース"),
    TtsRule(source="SQL", reading="エスキューエル"),
    TtsRule(source="JSON", reading="ジェイソン"),
    TtsRule(source="CPU", reading="シーピーユー"),
    TtsRule(source="1本", reading="いっぽん"),
    TtsRule(source="1時", reading="いちじ"),
    TtsRule(source="9時", reading="くじ"),
    TtsRule(source="20歳", reading="はたち"),
]


def _document_count(request: PlanPackRequest) -> int:
    if request.documentCount:
        return request.documentCount
    return 2 if request.scale == "quick" else 10


def _question_count(request: PlanPackRequest) -> int:
    return 10 if request.scale == "quick" else 30


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


def create_course_plan(request: PlanPackRequest) -> CoursePlan:
    settings = get_settings()
    pack_id = slugify(request.theme, "sokqa_pack")
    count = _document_count(request)
    documents = []
    for index in range(1, count + 1):
        doc_id = f"doc_{index:02d}"
        documents.append(
            PlanDocument(
                id=doc_id,
                title=f"{request.theme} 第{index}章",
                goal=f"{request.targetUser}が{request.theme}の重要ポイント{index}を聞き流しで理解する",
                keyPoints=[
                    f"{request.theme}の基本概念",
                    "重要用語",
                    "実務や試験での使われ方",
                ],
                targetSectionCount=6 if request.scale == "quick" else 10,
            )
        )

    tts_rules = COMMON_TTS_RULES.copy() if request.includeTts else []
    tts_rules.extend(request.userTtsRules)

    return CoursePlan(
        id=pack_id,
        title=f"{request.theme} 学習パック",
        description=f"{request.targetUser}向けの{request.theme}用Sokqa学習パックです。",
        language=request.language,
        targetUser=request.targetUser,
        difficulty=request.difficulty,
        author=settings.sokqa_author,
        documents=documents,
        quizPacks=_build_quiz_packs(request, [document.id for document in documents]),
        ttsRules=tts_rules,
    )
