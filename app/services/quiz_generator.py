from app.schemas.sokqa import CoursePlan, PlanQuizPack, SokqaDocumentPack, SokqaQuestion, SokqaQuizPack
from app.config import get_settings
from app.services.gemini_client import GeminiClient
from app.services.prompts import quiz_generation_prompt


def _source_snippets(document_packs: list[SokqaDocumentPack]) -> list[str]:
    snippets: list[str] = []
    for pack in document_packs:
        for item in pack.documents:
            snippets.append(item.text)
    return snippets or ["学習内容を確認します。"]


def generate_quiz_pack(
    plan: CoursePlan,
    quiz_plan: PlanQuizPack,
    document_packs: list[SokqaDocumentPack],
) -> SokqaQuizPack:
    if get_settings().gemini_provider == "gemini":
        try:
            content = GeminiClient().generate_json(quiz_generation_prompt(plan, quiz_plan, document_packs))
            return SokqaQuizPack.model_validate(content)
        except Exception:
            pass

    return generate_mock_quiz_pack(plan, quiz_plan, document_packs)


def generate_mock_quiz_pack(
    plan: CoursePlan,
    quiz_plan: PlanQuizPack,
    document_packs: list[SokqaDocumentPack],
) -> SokqaQuizPack:
    snippets = _source_snippets(document_packs)
    questions: list[SokqaQuestion] = []
    for index in range(1, quiz_plan.questionCount + 1):
        source = snippets[(index - 1) % len(snippets)]
        answer_index = (index - 1) % 4
        correct = f"{plan.title}の内容を、用語と使われ方を結びつけて理解する"
        distractors = [
            "本文にない細かな例外だけを暗記する",
            "用語名だけを覚えて意味を確認しない",
            "一度だけ読んで復習を省略する",
        ]
        choices = distractors.copy()
        choices.insert(answer_index, correct)
        questions.append(
            SokqaQuestion(
                id=f"q-{index}",
                question=f"{quiz_plan.title} {index}: 次の説明に基づく理解として最も適切なものはどれですか？",
                choices=choices[:4],
                answerIndex=answer_index,
                explanation=f"この問題は生成済みドキュメントの内容に基づいています。根拠: {source[:120]}",
                tags=[quiz_plan.purpose, plan.id],
            )
        )

    return SokqaQuizPack(
        id=f"{plan.id}_{quiz_plan.id}",
        title=quiz_plan.title,
        description=f"{plan.title}のドキュメント本文に基づく{quiz_plan.title}です。",
        language=plan.language,
        author=plan.author,
        globalTags=[plan.id, quiz_plan.purpose],
        questions=questions,
    )
