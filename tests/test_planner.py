from app.schemas.request import PlanPackRequest
from app.schemas.common import ReadingPattern
from app.schemas.sokqa import PlanDocument
from app.services import planner


def test_gemini_planned_title_and_description_are_used(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_gemini_plan_parts(request, model):
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
            [
                ReadingPattern(
                    id="git_dot_files",
                    title="Gitのドットファイルを読み下す",
                    description=".gitignore などのファイル名を読みやすく扱う",
                    examples=[".gitignore -> ドット ギットイグノア"],
                    recommended=True,
                )
            ],
        )

    monkeypatch.setattr(planner, "_gemini_plan_parts", fake_gemini_plan_parts)

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
    assert plan.proposedReadingPatterns[0].id == "git_dot_files"


def test_mock_planner_returns_reading_patterns_without_selected_ids(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="APIとGitの基礎",
            targetUser="初学者",
            scale="quick",
        )
    )

    assert plan.proposedReadingPatterns
    assert plan.selectedReadingPatternIds == []
    assert all(pattern.id for pattern in plan.proposedReadingPatterns)
