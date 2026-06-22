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
            "Git入門",
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
    assert plan.shortTitle == "Git入門"
    assert plan.description == "Gitの初期化、ステージング、コミット、ブランチ操作を段階的に学ぶパックです。"
    assert plan.documents[0].title == "Git入門 1. Gitの導入とローカルリポジトリの基本操作"
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


def test_planner_keeps_reading_patterns_only_for_llm_mode(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    llm_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="初学者", scale="quick", ttsReadingMode="llm")
    )
    auto_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="初学者", scale="quick", ttsReadingMode="auto")
    )
    rule_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="初学者", scale="quick", ttsReadingMode="rule")
    )
    multilingual_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="初学者", scale="quick", ttsReadingMode="multilingual")
    )
    none_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="初学者", scale="quick", enableTtsOptimize=False)
    )

    assert llm_plan.proposedReadingPatterns
    assert auto_plan.ttsReadingMode == "llm"
    assert auto_plan.proposedReadingPatterns
    assert rule_plan.proposedReadingPatterns == []
    assert multilingual_plan.proposedReadingPatterns == []
    assert none_plan.ttsReadingMode == "none"
    assert none_plan.proposedReadingPatterns == []


def test_structure_policy_and_material_mode_are_recorded_and_prompted(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    request = PlanPackRequest(
        theme="社内手順",
        targetUser="新人",
        scale="quick",
        structurePolicy="listening",
        materialMode="strict",
        sourceText="手順Aだけを説明する。",
    )
    plan = planner.create_course_plan(request)

    assert plan.structurePolicy == "listening"
    assert plan.materialMode == "source_only"
    assert plan.sourceMode == "document_only"

    prompt = planner._planner_prompt(request)
    assert "structurePolicy listening" in prompt
    assert "materialMode source_only" in planner._planner_prompt(request.model_copy(update={"materialMode": "source_only"}))
    assert "materialMode strict" in prompt
    assert "Do not add facts, terms, examples, claims, or inferred details" in prompt
    assert "connected narrative beats" in prompt
    assert "not isolated term labels" in prompt


def test_custom_instructions_are_recorded_and_prompted(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    request = PlanPackRequest(
        theme="接客英語",
        targetUser="初学者",
        scale="quick",
        customInstructions="会話例を多めにし、ホテル受付の場面を中心にする。",
    )
    plan = planner.create_course_plan(request)
    prompt = planner._planner_prompt(request)

    assert plan.customInstructions == "会話例を多めにし、ホテル受付の場面を中心にする。"
    assert "additional conditions: 会話例を多めにし、ホテル受付の場面を中心にする。" in prompt
    assert "Respect the user's additional conditions" in prompt


def test_legacy_sequential_structure_policy_falls_back_to_standard() -> None:
    request = PlanPackRequest(theme="Git", targetUser="初学者", structurePolicy="sequential")

    assert request.structurePolicy == "standard"


def test_listening_planner_uses_default_section_count_range(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="セキュリティ基礎",
            targetUser="初学者",
            scale="quick",
            structurePolicy="listening",
        )
    )

    assert plan.structurePolicy == "listening"
    assert all(35 <= document.targetSectionCount <= 50 for document in plan.documents)


def test_generation_unit_and_counts_shape_plan(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    docs_only = planner.create_course_plan(
        PlanPackRequest(theme="Git", targetUser="初学者", generationUnit="document", docCount=1, quizCount=5)
    )
    quiz_only = planner.create_course_plan(
        PlanPackRequest(theme="Git", targetUser="初学者", generationUnit="quiz", quizCount=1)
    )
    mixed = planner.create_course_plan(
        PlanPackRequest(theme="Git", targetUser="初学者", generationUnit="pack", docCount=1, quizCount=1)
    )

    assert docs_only.generationUnit == "document"
    assert len(docs_only.documents) == 1
    assert docs_only.quizPacks == []
    assert quiz_only.generationUnit == "quiz"
    assert quiz_only.documents == []
    assert len(quiz_only.quizPacks) == 1
    assert len(mixed.documents) == 1
    assert len(mixed.quizPacks) == 1


def test_large_scale_and_manual_metadata_controls(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="簿記",
            targetUser="資格学習者",
            scale="large",
            globalTagsMode="manual",
            manualGlobalTags=["簿記", "仕訳", "試験対策", "余分"],
            descriptionMode="manual",
            manualDescription="手動の説明です。",
            descriptionIncludeDate=True,
            descriptionIncludeAiDisclaimer=True,
        )
    )

    assert len(plan.documents) == 9
    assert len(plan.quizPacks) == 3
    assert plan.globalTags == ["簿記", "仕訳", "試験対策"]
    assert "手動の説明です。" in plan.description
    assert "生成日:" in plan.description
    assert "この内容はAIが生成したものです" in plan.description


def test_auto_description_options_are_appended(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="情報倫理",
            targetUser="社会人",
            scale="quick",
            descriptionIncludeDate=True,
            descriptionIncludeAiDisclaimer=True,
        )
    )

    assert "生成日:" in plan.description
    assert "この内容はAIが生成したものです" in plan.description


def test_auto_metadata_defaults_use_pack_language_when_not_specified(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="Security Strategy",
            targetUser="New managers",
            scale="quick",
            language="en",
            descriptionIncludeDate=True,
            descriptionIncludeAiDisclaimer=True,
        )
    )

    assert plan.title == "Security Strategy Learning Pack"
    assert plan.description.startswith("A Sokqa learning pack about Security Strategy for New managers.")
    assert "Generated on:" in plan.description
    assert "This content was generated by AI." in plan.description
    assert "生成日:" not in plan.description
    assert plan.documents[0].title.startswith("SecurityStrategy 1. Key Area 1 of Security Strategy")
    assert plan.quizPacks[0].title == "SecurityStrategy Integrated Review (Chapters 1-3: Full Range)"
    assert any("Security" in tag or "Strategy" in tag for tag in plan.globalTags)


def test_mock_planner_prefixes_document_and_quiz_titles() -> None:
    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="Git入門",
            targetUser="初学者",
            scale="quick",
            documentCount=4,
        )
    )

    assert plan.shortTitle == "Git入門"
    assert [document.title for document in plan.documents] == [
        "Git入門 1. Git入門の重要領域 1",
        "Git入門 2. Git入門の重要領域 2",
        "Git入門 3. Git入門の重要領域 3",
        "Git入門 4. Git入門の重要領域 4",
    ]
    assert len(plan.quizPacks) == 1
    assert plan.quizPacks[0].title == "Git入門 総合確認（1〜4章: 全範囲）"


def test_planner_sets_language_appropriate_course_global_tags() -> None:
    ja_plan = planner.create_course_plan(
        PlanPackRequest(theme="ITパスポート 経営戦略", targetUser="初学者", scale="quick")
    )
    en_plan = planner.create_course_plan(
        PlanPackRequest(theme="Security Strategy", targetUser="Learners", scale="quick", language="en")
    )

    assert len(ja_plan.globalTags) <= 3
    assert len(en_plan.globalTags) <= 3
    assert any("ITパスポート" in tag or "経営戦略" in tag for tag in ja_plan.globalTags)
    assert any("Security" in tag or "Strategy" in tag for tag in en_plan.globalTags)


def test_planner_prompt_names_common_reading_pattern_categories() -> None:
    prompt = planner._planner_prompt(
        PlanPackRequest(
            theme="Git基礎",
            targetUser="初学者",
            scale="quick",
        )
    )

    assert ".git -> ドットギット" in prompt
    assert ".env -> ドットイーエヌブイ" in prompt
    assert ".gitignore -> ドットギットイグノア" in prompt
    assert "OS -> オーエス" in prompt
    assert "API -> エーピーアイ" in prompt
    assert "git checkout -> ギット チェックアウト" in prompt
    assert "localStorage -> ローカルストレージ" in prompt
    assert "source and reading must not be identical" in prompt
    assert "Do not use already-natural katakana words as examples" in prompt


def test_fallback_reading_patterns_include_dot_notation_for_git_theme() -> None:
    request = PlanPackRequest(
        theme="Gitと環境変数",
        targetUser="初学者",
        scale="quick",
        sourceText=".env と .gitignore を扱います。",
    )
    patterns = planner._apply_recommended_guard(planner._fallback_reading_patterns(request), request)

    dot_pattern = next(pattern for pattern in patterns if pattern.id == "dot_notation")
    assert dot_pattern.recommended is True
    assert ".git -> ドット ギット" in dot_pattern.examples
    assert ".env -> ドット イーエヌブイ" in dot_pattern.examples
    assert ".gitignore -> ドット ギットイグノア" in dot_pattern.examples


def test_theme_unrelated_dot_and_command_patterns_are_not_recommended() -> None:
    request = PlanPackRequest(
        theme="ITパスポート試験対策",
        targetUser="IT初心者の社会人",
        scale="quick",
    )
    patterns = planner._apply_recommended_guard(planner._fallback_reading_patterns(request), request)

    alphabet = next(pattern for pattern in patterns if pattern.id == "alphabet_abbreviations")
    dot_pattern = next(pattern for pattern in patterns if pattern.id == "dot_notation")
    commands = next(pattern for pattern in patterns if pattern.id == "technical_commands")

    assert alphabet.recommended is True
    assert dot_pattern.recommended is False
    assert commands.recommended is False


def test_gemini_patterns_are_merged_with_dot_notation_fallback_when_missing() -> None:
    patterns = planner._reading_patterns_from_planner_response(
        {
            "proposedReadingPatterns": [
                {
                    "id": "api_reading",
                    "title": "APIをアルファベット読みする",
                    "description": "APIやURLをアルファベット読みで扱います。",
                    "examples": ["API -> エーピーアイ"],
                    "recommended": True,
                }
            ]
        },
        PlanPackRequest(
            theme="Git入門",
            targetUser="初学者",
            scale="quick",
        ),
    )

    assert any(pattern.id == "dot_notation" for pattern in patterns)
    assert any(".gitignore -> ドット ギットイグノア" in pattern.examples for pattern in patterns)


def test_gemini_dot_pattern_does_not_duplicate_dot_fallback() -> None:
    patterns = planner._reading_patterns_from_planner_response(
        {
            "proposedReadingPatterns": [
                {
                    "id": "git_dot_files",
                    "title": "Gitのドットファイルを読み下す",
                    "description": ".gitignore などのドットファイルを読みます。",
                    "examples": [".gitignore -> ドット ギットイグノア"],
                    "recommended": True,
                }
            ]
        },
        PlanPackRequest(
            theme="Git入門",
            targetUser="初学者",
            scale="quick",
        ),
    )

    dot_patterns = [pattern for pattern in patterns if planner._reading_pattern_signature(pattern) == "dot_notation"]
    assert len(dot_patterns) == 1
    assert len(patterns) <= planner.MAX_READING_PATTERN_COUNT


def test_invalid_reading_pattern_examples_are_removed_and_recommended_is_downgraded() -> None:
    patterns = planner._reading_patterns_from_planner_response(
        {
            "proposedReadingPatterns": [
                {
                    "id": "security_terms",
                    "title": "IT用語の自然な読み上げ",
                    "description": "専門用語を自然に読みます。",
                    "examples": [
                        "フィッシング -> フィッシング",
                        "マルウェア -> マルウェア",
                        "サブネットマスク -> サブネットマスク制",
                        "broken example",
                        "有線LAN -> ゆうせんラン",
                    ],
                    "recommended": True,
                }
            ]
        },
        PlanPackRequest(theme="セキュリティ", targetUser="初学者", scale="quick"),
    )

    security_pattern = next(pattern for pattern in patterns if pattern.id == "security_terms")
    assert security_pattern.examples == ["有線LAN -> ゆうせんラン"]
    assert security_pattern.recommended is True


def test_reading_pattern_without_valid_examples_is_not_recommended() -> None:
    patterns = planner._reading_patterns_from_planner_response(
        {
            "proposedReadingPatterns": [
                {
                    "id": "kana_only",
                    "title": "カタカナ語",
                    "description": "すでにカタカナの語を読みます。",
                    "examples": ["フィッシング -> フィッシング", "マルウェア -> マルウェア", "not an arrow"],
                    "recommended": True,
                }
            ]
        },
        PlanPackRequest(theme="セキュリティ", targetUser="初学者", scale="quick"),
    )

    kana_pattern = next(pattern for pattern in patterns if pattern.id == "kana_only")
    assert kana_pattern.examples == []
    assert kana_pattern.recommended is False
