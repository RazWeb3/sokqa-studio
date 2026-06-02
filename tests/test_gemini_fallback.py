from app.config import get_settings
from app.schemas.request import GeneratePackRequest, PlanPackRequest
from app.services.pack_agent import generate_pack, plan_pack


def test_gemini_provider_falls_back_to_mock(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    plan = plan_pack(
        PlanPackRequest(
            theme="ITパスポート試験対策",
            targetUser="IT初心者の社会人",
            scale="quick",
        )
    )
    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))

    assert generated.validation.valid is True
    assert len(plan.documents) >= 1
    assert len(generated.manifest.items) == len(plan.documents) + len(plan.quizPacks)
    assert sum(1 for item in generated.manifest.items if item.kind == "document") == len(plan.documents)
    assert sum(1 for item in generated.manifest.items if item.kind == "quiz") == len(plan.quizPacks)
