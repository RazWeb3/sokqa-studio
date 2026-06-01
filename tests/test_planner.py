from app.schemas.request import PlanPackRequest
from app.schemas.sokqa import PlanDocument
from app.services import planner


def test_gemini_planned_title_and_description_are_used(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_gemini_documents(request, model):
        return (
            "Git実践入門: リポジトリ操作と履歴管理",
            "Gitの初期化、ステージング、コミット、ブランチ操作を段階的に学ぶパックです。",
            [
                PlanDocument(
                    id="doc_01",
                    title="Gitの導入とローカルリポジトリの基本操作",
                    goal="git initでローカルリポジトリを作り、作業ツリーの状態を理解する",
                    keyPoints=["git init", "作業ツリー", "ローカルリポジトリ"],
                    targetSectionCount=6,
                )
            ],
        )

    monkeypatch.setattr(planner, "_gemini_documents", fake_gemini_documents)

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="Git基礎講座",
            targetUser="Gitを初めて使う開発者",
            scale="quick",
            documentCount=1,
        )
    )

    assert plan.title == "Git実践入門: リポジトリ操作と履歴管理"
    assert plan.description == "Gitの初期化、ステージング、コミット、ブランチ操作を段階的に学ぶパックです。"
    assert plan.documents[0].title == "Gitの導入とローカルリポジトリの基本操作"
    assert plan.documents[0].goal == "git initでローカルリポジトリを作り、作業ツリーの状態を理解する"
    assert plan.documents[0].keyPoints == ["git init", "作業ツリー", "ローカルリポジトリ"]
