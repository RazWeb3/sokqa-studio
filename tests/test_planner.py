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


def test_mock_planner_returns_no_reading_patterns_without_selected_ids(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="APIとGitの基礎",
            targetUser="社会人",
            scale="quick",
        )
    )

    assert plan.proposedReadingPatterns == []
    assert plan.selectedReadingPatternIds == []


def test_planner_infers_learning_language_and_allows_override(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    inferred = planner.create_course_plan(
        PlanPackRequest(theme="英会話 初級", targetUser="日本語話者", scale="quick")
    )
    overridden = planner.create_course_plan(
        PlanPackRequest(
            theme="英会話 初級",
            targetUser="日本語話者",
            scale="quick",
            learningLanguage="ko",
        )
    )

    assert inferred.learningLanguage == "en"
    assert overridden.learningLanguage == "ko"


def test_planner_defaults_quiz_choice_language_mode_to_learning_when_learning_language_exists(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    inferred = planner.create_course_plan(
        PlanPackRequest(theme="英会話 初級", targetUser="日本語話者", scale="quick", generationUnit="quiz")
    )
    explicit = planner.create_course_plan(
        PlanPackRequest(
            theme="英会話 初級",
            targetUser="日本語話者",
            scale="quick",
            generationUnit="quiz",
            quizPacks=[
                {
                    "id": "quiz_pack_01",
                    "title": "Meaning Check",
                    "purpose": "key_concepts",
                    "questionCount": 10,
                }
            ],
        )
    )
    without_learning = planner.create_course_plan(
        PlanPackRequest(theme="Git", targetUser="社会人", scale="quick", generationUnit="quiz")
    )

    assert inferred.quizPacks[0].choiceLanguageMode == "pack"
    assert explicit.quizPacks[0].choiceLanguageMode == "pack"
    assert without_learning.quizPacks[0].choiceLanguageMode == "pack"


def test_planner_preserves_explicit_quiz_choice_language_mode(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="英会話 初級",
            targetUser="日本語話者",
            scale="quick",
            generationUnit="quiz",
            quizPacks=[
                {
                    "id": "quiz_pack_01",
                    "title": "Meaning Check",
                    "purpose": "key_concepts",
                    "questionCount": 10,
                    "choiceLanguageMode": "pack",
                }
            ],
        )
    )

    assert plan.quizPacks[0].choiceLanguageMode == "pack"


def test_planner_applies_preplan_quiz_choice_language_modes_to_generated_quiz_packs(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    pack_plan = planner.create_course_plan(
        PlanPackRequest(
            theme="英会話 初級",
            targetUser="日本語話者",
            scale="standard",
            generationUnit="pack",
            quizChoiceLanguageModes=["pack", "auto"],
        )
    )
    quiz_plan = planner.create_course_plan(
        PlanPackRequest(
            theme="英会話 初級",
            targetUser="日本語話者",
            scale="quick",
            generationUnit="quiz",
            quizCount=2,
            quizChoiceLanguageModes=["learning", "pack"],
        )
    )

    assert [quiz.choiceLanguageMode for quiz in pack_plan.quizPacks] == ["pack", "auto"]
    assert [quiz.choiceLanguageMode for quiz in quiz_plan.quizPacks] == ["learning", "pack"]


def test_planner_keeps_reading_patterns_only_for_llm_mode(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_gemini_plan_parts(request, model):
        return (
            request.theme,
            "説明",
            "短題",
            [
                PlanDocument(
                    id="doc_01",
                    title="導入",
                    goal="基本を理解する",
                    keyPoints=["概要", "前提", "確認"],
                    targetSectionCount=35,
                )
            ],
            [
                ReadingPattern(
                    id="api_terms",
                    title="API表記を読み下す",
                    description="API表記を自然に読む。",
                    examples=["API -> エーピーアイ"],
                    recommended=True,
                )
            ],
        )

    monkeypatch.setattr(planner, "_gemini_plan_parts", fake_gemini_plan_parts)

    llm_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="社会人", scale="quick", documentCount=1, ttsReadingMode="llm")
    )
    auto_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="社会人", scale="quick", documentCount=1, ttsReadingMode="auto")
    )
    rule_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="社会人", scale="quick", documentCount=1, ttsReadingMode="rule")
    )
    multilingual_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="社会人", scale="quick", documentCount=1, ttsReadingMode="multilingual")
    )
    none_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="社会人", scale="quick", documentCount=1, enableTtsOptimize=False)
    )

    assert [pattern.id for pattern in llm_plan.proposedReadingPatterns] == ["api_terms"]
    assert auto_plan.ttsReadingMode == "llm"
    assert [pattern.id for pattern in auto_plan.proposedReadingPatterns] == ["api_terms"]
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
        targetUser="社会人",
        scale="quick",
        customInstructions="会話例を多めにし、ホテル受付の場面を中心にする。",
    )
    plan = planner.create_course_plan(request)
    prompt = planner._planner_prompt(request)

    assert plan.customInstructions == "会話例を多めにし、ホテル受付の場面を中心にする。"
    assert "additional conditions: 会話例を多めにし、ホテル受付の場面を中心にする。" in prompt
    assert "Respect the user's additional conditions" in prompt


def test_legacy_sequential_structure_policy_falls_back_to_summary() -> None:
    request = PlanPackRequest(theme="Git", targetUser="社会人", structurePolicy="sequential")

    assert request.structurePolicy == "summary"


def test_listening_planner_uses_default_section_count_range(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="セキュリティ基礎",
            targetUser="社会人",
            scale="quick",
            structurePolicy="listening",
        )
    )

    assert plan.structurePolicy == "listening"
    assert all(35 <= document.targetSectionCount <= 50 for document in plan.documents)


def test_mock_planner_uses_deterministic_varied_section_counts(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    request = PlanPackRequest(
        theme="ITパスポート試験対策",
        targetUser="IT初心者の社会人",
        scale="standard",
        structurePolicy="listening",
    )
    first = planner.create_course_plan(request)
    second = planner.create_course_plan(request)
    first_counts = [document.targetSectionCount for document in first.documents]
    second_counts = [document.targetSectionCount for document in second.documents]

    assert first_counts == second_counts
    assert all(35 <= count <= 50 for count in first_counts)
    assert len(set(first_counts)) > 1


def test_planner_prompt_removes_42_midpoint_bias_for_section_counts() -> None:
    prompt = planner._planner_prompt(
        PlanPackRequest(
            theme="Git基礎",
            targetUser="社会人",
            scale="quick",
        )
    )

    assert "Prefer 42" not in prompt
    assert "same midpoint" in prompt
    assert '"targetSectionCount": 38' in prompt
    assert '"targetSectionCount": 45' in prompt
    assert '"targetSectionCount": 41' in prompt


def test_generation_unit_and_counts_shape_plan(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    docs_only = planner.create_course_plan(
        PlanPackRequest(theme="Git", targetUser="社会人", generationUnit="document", docCount=1, quizCount=5)
    )
    quiz_only = planner.create_course_plan(
        PlanPackRequest(theme="Git", targetUser="社会人", generationUnit="quiz", quizCount=1)
    )
    mixed = planner.create_course_plan(
        PlanPackRequest(theme="Git", targetUser="社会人", generationUnit="pack", docCount=1, quizCount=1)
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
            targetUser="社会人",
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
        PlanPackRequest(theme="ITパスポート 経営戦略", targetUser="社会人", scale="quick")
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
            targetUser="社会人",
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
    assert "Judge genre primarily from theme and customInstructions" in prompt
    assert 'Do not infer technical patterns from short general words such as "it", "ai", or "os"' in prompt


def test_llm_success_does_not_merge_fallback_noise_for_lyrics_theme() -> None:
    patterns = planner._reading_patterns_from_planner_response(
        {
            "proposedReadingPatterns": [
                {
                    "id": "lyrics_kanji",
                    "title": "歌詞内の難読語を読み下す",
                    "description": "歌詞で出る難読語の読みを安定させます。",
                    "examples": ["黄昏 -> たそがれ"],
                    "recommended": True,
                }
            ]
        },
        PlanPackRequest(theme="歌詞教材", targetUser="学習者", scale="quick"),
    )

    assert [pattern.id for pattern in patterns] == ["lyrics_kanji"]
    assert all(pattern.id not in {"dot_notation", "technical_commands", "camel_case_terms"} for pattern in patterns)
    assert all(
        ".git" not in example and "npm install" not in example and "localStorage" not in example
        for pattern in patterns
        for example in pattern.examples
    )


def test_it_passport_theme_does_not_add_technical_noise_when_llm_returns_exam_patterns() -> None:
    patterns = planner._reading_patterns_from_planner_response(
        {
            "proposedReadingPatterns": [
                {
                    "id": "exam_terms",
                    "title": "試験名と略語を読み下す",
                    "description": "ITパスポート教材で頻出の試験名と略語を読みます。",
                    "examples": ["ITパスポート -> アイティーパスポート", "CBT -> シービーティー"],
                    "recommended": True,
                }
            ]
        },
        PlanPackRequest(theme="ITパスポート", targetUser="社会人", scale="quick"),
    )

    assert [pattern.id for pattern in patterns] == ["exam_terms"]
    assert all(pattern.id not in {"dot_notation", "technical_commands", "camel_case_terms"} for pattern in patterns)
    assert all(
        ".git" not in example and "npm install" not in example and "localStorage" not in example and "v1.2" not in example
        for pattern in patterns
        for example in pattern.examples
    )


def test_missing_or_empty_proposed_reading_patterns_returns_empty_list() -> None:
    request = PlanPackRequest(theme="ITパスポート", targetUser="社会人", scale="quick")

    assert planner._reading_patterns_from_planner_response({}, request) == []
    assert planner._reading_patterns_from_planner_response({"proposedReadingPatterns": []}, request) == []


def test_llm_failure_returns_empty_reading_patterns_while_documents_fallback(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fail_gemini_plan_parts(request, model):
        raise RuntimeError("planner failure")

    monkeypatch.setattr(planner, "_gemini_plan_parts", fail_gemini_plan_parts)

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="ITパスポート",
            targetUser="社会人",
            scale="quick",
            documentCount=1,
        )
    )

    assert plan.proposedReadingPatterns == []
    assert len(plan.documents) == 1
    assert plan.documents[0].title == "ITパスポート 1. ITパスポートの重要領域 1"


def test_recommended_value_from_llm_is_preserved() -> None:
    patterns = planner._reading_patterns_from_planner_response(
        {
            "proposedReadingPatterns": [
                {
                    "id": "api_reading",
                    "title": "APIをアルファベット読みする",
                    "description": "APIの読みを安定させます。",
                    "examples": ["API -> エーピーアイ"],
                    "recommended": True,
                }
            ]
        },
        PlanPackRequest(theme="英会話 初級", targetUser="社会人", scale="quick"),
    )

    assert patterns[0].id == "api_reading"
    assert patterns[0].recommended is True


def test_examples_validation_filters_invalid_entries_and_keeps_valid_patterns() -> None:
    patterns = planner._reading_patterns_from_planner_response(
        {
            "proposedReadingPatterns": [
                {
                    "id": "invalid_examples",
                    "title": "不正例だけの候補",
                    "description": "examples がすべて不正です。",
                    "examples": [
                        "フィッシング -> フィッシング",
                        "broken example",
                        " -> よみ",
                    ],
                    "recommended": True,
                },
                {
                    "id": "valid_examples",
                    "title": "有効な候補",
                    "description": "有効な examples を持つ候補です。",
                    "examples": [
                        "有線LAN -> ゆうせんラン",
                        "A/B -> エー ビー",
                    ],
                    "recommended": True,
                },
                {
                    "id": "missing_description",
                    "title": "説明欠落",
                    "examples": ["API -> エーピーアイ"],
                    "recommended": True,
                },
            ]
        },
        PlanPackRequest(theme="ネットワーク基礎", targetUser="社会人", scale="quick"),
    )

    assert [pattern.id for pattern in patterns] == ["invalid_examples", "valid_examples"]
    invalid_pattern = patterns[0]
    valid_pattern = patterns[1]
    assert invalid_pattern.examples == []
    assert invalid_pattern.recommended is False
    assert valid_pattern.examples == ["有線LAN -> ゆうせんラン", "A/B -> エー ビー"]
    assert valid_pattern.recommended is True


def test_planner_stores_deterministic_generation_guidance_for_listening_policy(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="ITパスポート",
            targetUser="IT初心者の社会人",
            difficulty="beginner",
            scale="quick",
            structurePolicy="listening",
        )
    )

    # 決定論生成: generationGuidance が None でなく、確定入力から組み立てられた日本語目的文を格納していること
    assert plan.generationGuidance is not None
    assert "音声で連続して聞き流される用途" in plan.generationGuidance
    assert "記号プレースホルダー(△△・××・〇〇 等)" in plan.generationGuidance
    assert "IT初心者の社会人(初学者)" in plan.generationGuidance


def test_planner_generation_guidance_refers_to_custom_instructions_without_inlining(monkeypatch) -> None:
    settings = planner.get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = planner.create_course_plan(
        PlanPackRequest(
            theme="接客英語",
            targetUser="社会人",
            difficulty="standard",
            scale="quick",
            structurePolicy="listening",
            customInstructions="ホテル受付の場面を中心にする。",
        )
    )

    assert plan.generationGuidance is not None
    assert "なお、上記に加えユーザー指定の追加条件も目的の一部として尊重すること。" in plan.generationGuidance
    # customInstructions 本文は目的文に展開されないこと
    assert "ホテル受付の場面を中心にする。" not in plan.generationGuidance
    # customInstructions は従来通り plan.customInstructions に保持されること
    assert plan.customInstructions == "ホテル受付の場面を中心にする。"


def test_planner_prompt_normal_mode_uses_topic_explanation_objective() -> None:
    prompt = planner._planner_prompt(
        PlanPackRequest(
            theme="海外旅行",
            targetUser="社会人",
            scale="quick",
            language="ja",
        )
    )

    # 通常モード: 説明対象型の目的関数が選ばれていること
    assert "この教材の目的関数（通常教材: テーマ解説）" in prompt
    # 説明対象型の章立てを促す指示が含まれること
    assert "「海外旅行とは」「旅行計画」" in prompt
    # 言語学習目的関数は含まれないこと
    assert "この教材の目的関数（言語学習: 学習場面ベース）" not in prompt
    assert "学習フレーズの利用場面" not in prompt
    # 共有部は維持されていること
    assert "Return strict JSON only." in prompt
    assert '"targetSectionCount": 38' in prompt
    assert "targetSectionCount must be an integer from 35 to 50" in prompt


def test_planner_prompt_language_learning_mode_uses_scene_based_objective() -> None:
    prompt = planner._planner_prompt(
        PlanPackRequest(
            theme="海外旅行",
            targetUser="社会人",
            scale="quick",
            language="ja",
            learningLanguage="en",
        )
    )

    # 言語学習モード: 学習場面型の目的関数が選ばれていること
    assert "この教材の目的関数（言語学習: 学習場面ベース）" in prompt
    assert "学習フレーズの利用場面" in prompt
    # 場面主語の Good 例が含まれること
    assert "空港で使う基本表現" in prompt
    assert "ホテルで使う表現" in prompt
    # 説明対象型の目的関数は含まれないこと
    assert "この教材の目的関数（通常教材: テーマ解説）" not in prompt
    assert "「海外旅行とは」「旅行計画」" not in prompt
    # 共有部は維持されていること
    assert "Return strict JSON only." in prompt
    assert '"targetSectionCount": 38' in prompt
    assert "targetSectionCount must be an integer from 35 to 50" in prompt


def test_planner_prompt_language_learning_mode_selects_other_languages() -> None:
    ko_prompt = planner._planner_prompt(
        PlanPackRequest(
            theme="海外旅行",
            targetUser="社会人",
            scale="quick",
            language="ja",
            learningLanguage="ko",
        )
    )
    zh_prompt = planner._planner_prompt(
        PlanPackRequest(
            theme="海外旅行",
            targetUser="社会人",
            scale="quick",
            language="ko",
            learningLanguage="en",
        )
    )

    # 他言語ペアでも言語学習用プロンプトが選択されること
    assert "この教材の目的関数（言語学習: 学習場面ベース）" in ko_prompt
    assert "この教材の目的関数（言語学習: 学習場面ベース）" in zh_prompt
    # ハードコードされた言語ペア（pack=ja / learning=en 前提）がないこと
    assert "学習言語 en" not in ko_prompt
    assert "学習言語 ja" not in ko_prompt
    assert "学習言語 ko" in ko_prompt
    assert "学習言語 en" in zh_prompt


def test_planner_prompt_no_hardcoded_language_pair() -> None:
    # 言語ハードコードがないことの確認: 通常モードにも言語学習モードにも
    # 固定の "pack=ja / learning=en" 前提の記述が混ざらない。
    normal_prompt = planner._planner_prompt(
        PlanPackRequest(theme="Git基礎", targetUser="社会人", scale="quick", language="ja")
    )
    en_prompt = planner._planner_prompt(
        PlanPackRequest(theme="海外旅行", targetUser="社会人", scale="quick", language="ja", learningLanguage="en")
    )

    # 変数として埋め込まれるため、リテラルな "pack=ja / learning=en" 前提の文は無い
    for prompt in (normal_prompt, en_prompt):
        assert "pack=ja" not in prompt
        assert "learning=en" not in prompt
        # 言語コードは必ず変数展開形（{...} でなくても、固定ペア前提の記述が無い）で現れる
        assert "学習言語 en" in en_prompt
        assert "学習言語 en" not in normal_prompt


def test_planner_learning_mode_branch_condition_matches_existing() -> None:
    # 分岐条件が (learningLanguage あり かつ pack != learning) と一致していること
    # 1) learningLanguage なし -> 通常モード
    assert planner._is_language_learning_mode(
        PlanPackRequest(theme="海外旅行", targetUser="社会人", scale="quick", language="ja")
    ) is False

    # 2) learningLanguage == packLanguage -> 通常モード（同一言語）
    assert planner._is_language_learning_mode(
        PlanPackRequest(theme="海外旅行", targetUser="社会人", scale="quick", language="ja", learningLanguage="ja")
    ) is False

    # 3) learningLanguage あり かつ pack != learning -> 言語学習モード
    assert planner._is_language_learning_mode(
        PlanPackRequest(theme="海外旅行", targetUser="社会人", scale="quick", language="ja", learningLanguage="en")
    ) is True
    assert planner._is_language_learning_mode(
        PlanPackRequest(theme="海外旅行", targetUser="社会人", scale="quick", language="ja", learningLanguage="ko")
    ) is True

    # 4) learningLanguage なしでもテーマから推論される言語が pack と異なれば言語学習モード
    # theme="英会話 初級" は en を推論。pack=ja と異なるため True。
    assert planner._is_language_learning_mode(
        PlanPackRequest(theme="英会話 初級", targetUser="日本語話者", scale="quick", language="ja")
    ) is True
    # 推論された言語が pack と同じなら False（英語パックで英語を推論）
    assert planner._is_language_learning_mode(
        PlanPackRequest(theme="English conversation", targetUser="English speaker", scale="quick", language="en")
    ) is False
