from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack
from app.utils.ids import path_token


def pack_content_token(plan: CoursePlan) -> str:
    return path_token(plan.contentId or plan.id, plan.id)


def document_pack_id(plan: CoursePlan, document: PlanDocument) -> str:
    return f"{pack_content_token(plan)}_{path_token(document.id, 'doc')}"


def quiz_pack_id(plan: CoursePlan, quiz_pack: PlanQuizPack) -> str:
    return f"{pack_content_token(plan)}_{path_token(quiz_pack.id, 'quiz')}"
