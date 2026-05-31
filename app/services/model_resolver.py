from dataclasses import dataclass

from app.config import get_settings


@dataclass(frozen=True)
class TaskModels:
    planner: str
    document: str
    quiz: str


def resolve_task_models(plan=None, request=None) -> TaskModels:
    settings = get_settings()
    request_model = getattr(request, "model", None)
    plan_model = getattr(plan, "model", None)
    base_override = request_model or plan_model

    planner = (
        getattr(request, "plannerModel", None)
        or getattr(plan, "plannerModel", None)
        or base_override
        or settings.planner_model
    )
    document = (
        getattr(request, "docModel", None)
        or getattr(plan, "docModel", None)
        or base_override
        or settings.document_model
    )
    quiz = (
        getattr(request, "quizModel", None)
        or getattr(plan, "quizModel", None)
        or base_override
        or settings.quiz_model
    )
    return TaskModels(planner=planner, document=document, quiz=quiz)
