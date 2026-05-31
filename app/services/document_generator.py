from app.schemas.sokqa import CoursePlan, PlanDocument, SokqaDocumentItem, SokqaDocumentPack
from app.config import get_settings
from app.services.gemini_client import GeminiClient
from app.services.prompts import document_generation_prompt


def generate_document_pack(plan: CoursePlan, document: PlanDocument) -> SokqaDocumentPack:
    if get_settings().gemini_provider == "gemini":
        try:
            content = GeminiClient().generate_json(document_generation_prompt(plan, document))
            return SokqaDocumentPack.model_validate(content)
        except Exception:
            pass

    return generate_mock_document_pack(plan, document)


def generate_mock_document_pack(plan: CoursePlan, document: PlanDocument) -> SokqaDocumentPack:
    items: list[SokqaDocumentItem] = []
    for index in range(1, document.targetSectionCount + 1):
        point = document.keyPoints[(index - 1) % len(document.keyPoints)]
        text = (
            f"{document.title}のセクション{index}です。"
            f"{point}について、{plan.targetUser}にも分かるように短く確認します。"
            f"{plan.title}では、用語の意味と実際の使われ方を結びつけて覚えることが大切です。"
        )
        items.append(SokqaDocumentItem(id=f"{document.id}_sec_{index:02d}", text=text, tags=[plan.id, document.id]))

    return SokqaDocumentPack(
        id=f"{plan.id}_{document.id}",
        title=document.title,
        description=document.goal,
        language=plan.language,
        author=plan.author,
        globalTags=[plan.id, plan.difficulty],
        documents=items,
    )
