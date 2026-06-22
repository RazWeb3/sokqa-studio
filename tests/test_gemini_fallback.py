import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.schemas.request import GeneratePackRequest, PlanPackRequest
from app.schemas.sokqa import SokqaDocumentItem, SokqaDocumentPack
from app.services.pack_agent import generate_pack, plan_pack
from app.services.quiz_generator import generate_quiz_pack
from main import app


client = TestClient(app)


def test_gemini_provider_generation_failure_does_not_save_mock_content(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    plan = plan_pack(
        PlanPackRequest(
            theme="ITパスポート試験対策",
            targetUser="IT初心者の社会人",
            scale="quick",
            ttsReadingMode="rule",
        )
    )

    with pytest.raises(RuntimeError, match="保存していません"):
        generate_pack(GeneratePackRequest(plan=plan, persist=False))


def test_gemini_provider_quiz_generation_failure_does_not_save_mock_content(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    plan = plan_pack(
        PlanPackRequest(
            theme="ITパスポート試験対策",
            targetUser="IT初心者の社会人",
            scale="quick",
            ttsReadingMode="rule",
        )
    )
    source_document = SokqaDocumentPack(
        id="doc_pack",
        title="基礎",
        language=plan.language,
        documents=[SokqaDocumentItem(id="doc-1", text="二要素認証を確認します。")],
    )

    with pytest.raises(RuntimeError, match="保存していません"):
        generate_quiz_pack(plan, plan.quizPacks[0], [source_document])


def test_generate_route_returns_clear_generation_failure_message(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    plan = plan_pack(
        PlanPackRequest(
            theme="ITパスポート試験対策",
            targetUser="IT初心者の社会人",
            scale="quick",
            ttsReadingMode="rule",
        )
    )

    response = client.post("/generate-pack", json={"plan": plan.model_dump(mode="json"), "persist": False})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "ドキュメント生成に失敗しました" in detail["message"]
    assert "保存していません" in detail["message"]
    assert "少し時間を置いて再生成" in detail["hint"]
