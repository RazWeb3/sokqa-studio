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
            targetUser="社会人",
            scale="quick",
        )
    )

    assert plan.proposedReadingPatterns
    assert plan.selectedReadingPatternIds == []
    assert all(pattern.id for pattern in plan.proposedReadingPatterns)


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

    assert inferred.quizPacks[0].choiceLanguageMode == "learning"
    assert explicit.quizPacks[0].choiceLanguageMode == "learning"
    assert without_learning.quizPacks[0].choiceLanguageMode == "auto"


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
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    llm_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="社会人", scale="quick", ttsReadingMode="llm")
    )
    auto_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="社会人", scale="quick", ttsReadingMode="auto")
    )
    rule_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="社会人", scale="quick", ttsReadingMode="rule")
    )
    multilingual_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="社会人", scale="quick", ttsReadingMode="multilingual")
    )
    none_plan = planner.create_course_plan(
        PlanPackRequest(theme="APIとGitの基礎", targetUser="社会人", scale="quick", enableTtsOptimize=False)
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
        targetUser="社会人",
        scale="quick",
        customInstructions="会話例を多めにし、ホテル受付の場面を中心にする。",
    )
    plan = planner.create_course_plan(request)
    prompt = planner._planner_prompt(request)

    assert plan.customInstructions == "会話例を多めにし、ホテル受付の場面を中心にする。"
    assert "additional conditions: 会話例を多めにし、ホテル受付の場面を中心にする。" in prompt
    assert "Respect the user's additional conditions" in prompt


def test_legacy_sequential_structure_policy_falls_back_to_standard() -> None:
    request = PlanPackRequest(theme="Git", targetUser="社会人", structurePolicy="sequential")

    assert request.structurePolicy == "standard"


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


def test_fallback_reading_patterns_include_dot_notation_for_git_theme() -> None:
    request = PlanPackRequest(
        theme="Gitと環境変数",
        targetUser="社会人",
        scale="quick",
        sourceText=".env と .gitignore を扱います。",
    )
    patterns = planner._apply_recommended_guard(planner._fallback_reading_patterns(request), request)

    dot_pattern = next(pattern for pattern in patterns if pattern.id == "dot_notation")
    alphabet = next(pattern for pattern in patterns if pattern.id == "alphabet_abbreviations")
    assert alphabet.recommended is True
    assert dot_pattern.recommended is False
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


def test_non_technical_lyrics_theme_does_not_trigger_technical_fallback_from_it_in_source_text() -> None:
    request = PlanPackRequest(
        theme="歌詞教材",
        targetUser="学習者",
        scale="quick",
        sourceText="I need it now.",
    )
    patterns = planner._fallback_reading_patterns(request)
    ids = [pattern.id for pattern in patterns]

    assert "literary_difficult_words" in ids
    assert "dot_notation" not in ids
    assert "technical_commands" not in ids
    assert "camel_case_terms" not in ids


def test_english_conversation_theme_does_not_trigger_technical_fallback_from_it_in_source_text() -> None:
    request = PlanPackRequest(
        theme="英会話 初級",
        targetUser="社会人",
        scale="quick",
        sourceText="Save it for later.",
    )
    patterns = planner._fallback_reading_patterns(request)
    ids = [pattern.id for pattern in patterns]

    assert "language_kanji_readings" in ids
    assert "alphabet_abbreviations" not in ids
    assert "dot_notation" not in ids


def test_source_text_alone_does_not_make_lyrics_theme_technical() -> None:
    request = PlanPackRequest(
        theme="歌詞教材",
        targetUser="学習者",
        scale="quick",
        customInstructions="韻律を重視する。",
        sourceText="npm localStorage .git",
    )
    patterns = planner._fallback_reading_patterns(request)
    ids = [pattern.id for pattern in patterns]

    assert "literary_difficult_words" in ids
    assert "alphabet_abbreviations" not in ids
    assert "dot_notation" not in ids


def test_technical_theme_still_returns_technical_fallback_patterns() -> None:
    request = PlanPackRequest(
        theme="ITパスポート試験対策",
        targetUser="IT初心者の社会人",
        scale="quick",
    )
    patterns = planner._fallback_reading_patterns(request)
    ids = [pattern.id for pattern in patterns]

    assert "alphabet_abbreviations" in ids
    assert "dot_notation" in ids
    assert "exam_official_names" in ids


def test_multi_category_theme_merges_language_and_technical_patterns_without_duplicates() -> None:
    request = PlanPackRequest(
        theme="英語の技術書",
        targetUser="読者",
        scale="quick",
        customInstructions="API と localStorage の読みを安定させる",
    )
    patterns = planner._fallback_reading_patterns(request)
    ids = [pattern.id for pattern in patterns]

    assert "alphabet_abbreviations" in ids
    assert "language_kanji_readings" in ids
    assert len(ids) == len(set(ids))


def test_unmatched_theme_returns_no_fallback_reading_patterns() -> None:
    request = PlanPackRequest(
        theme="心理学入門",
        targetUser="社会人",
        scale="quick",
    )

    assert planner._fallback_reading_patterns(request) == []


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
            targetUser="社会人",
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
            targetUser="社会人",
            scale="quick",
        ),
    )

    dot_patterns = [pattern for pattern in patterns if planner._reading_pattern_signature(pattern) == "dot_notation"]
    assert len(dot_patterns) == 1
    assert len([pattern.id for pattern in patterns]) == len({pattern.id for pattern in patterns})


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
        PlanPackRequest(theme="セキュリティ", targetUser="社会人", scale="quick"),
    )

    security_pattern = next(pattern for pattern in patterns if pattern.id == "security_terms")
    assert security_pattern.examples == ["有線LAN -> ゆうせんラン"]
    assert security_pattern.recommended is False


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
        PlanPackRequest(theme="セキュリティ", targetUser="社会人", scale="quick"),
    )

    kana_pattern = next(pattern for pattern in patterns if pattern.id == "kana_only")
    assert kana_pattern.examples == []
    assert kana_pattern.recommended is False


def test_non_technical_theme_does_not_make_technical_candidates_recommended_from_source_text() -> None:
    request = PlanPackRequest(
        theme="歌詞教材",
        targetUser="学習者",
        scale="quick",
        sourceText="API と Git を歌詞に含む。",
    )

    fallback_patterns = planner._apply_recommended_guard(planner._fallback_reading_patterns(request), request)
    merged_patterns = planner._reading_patterns_from_planner_response(
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
        request,
    )

    assert all(pattern.id not in {"alphabet_abbreviations", "dot_notation"} for pattern in fallback_patterns)
    api_pattern = next(pattern for pattern in merged_patterns if pattern.id == "api_reading")
    assert api_pattern.recommended is False


def test_technical_theme_uses_fixed_recommended_rules() -> None:
    request = PlanPackRequest(
        theme="ITパスポート試験対策",
        targetUser="IT初心者の社会人",
        scale="quick",
        sourceText="本文に技術語がなくてもよい。",
    )
    patterns = planner._apply_recommended_guard(planner._fallback_reading_patterns(request), request)

    recommended_by_id = {pattern.id: pattern.recommended for pattern in patterns}
    assert recommended_by_id["alphabet_abbreviations"] is True
    assert recommended_by_id["dot_notation"] is False
    assert recommended_by_id["technical_commands"] is False
    assert recommended_by_id["camel_case_terms"] is False
    assert recommended_by_id["symbols_and_versions"] is False


def test_llm_technical_recommended_is_overridden_to_false_for_non_technical_theme() -> None:
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
            theme="英会話 初級",
            targetUser="社会人",
            scale="quick",
        ),
    )

    api_pattern = next(pattern for pattern in patterns if pattern.id == "api_reading")
    assert api_pattern.recommended is False


def test_fallback_and_llm_same_signature_share_same_recommended_rule() -> None:
    request = PlanPackRequest(
        theme="ITパスポート試験対策",
        targetUser="IT初心者の社会人",
        scale="quick",
    )
    fallback_patterns = planner._apply_recommended_guard(planner._fallback_reading_patterns(request), request)
    llm_patterns = planner._apply_recommended_guard(
        [
            ReadingPattern(
                id="api_reading",
                title="APIをアルファベット読みする",
                description="APIやURLをアルファベット読みで扱います。",
                examples=["API -> エーピーアイ"],
                recommended=False,
            )
        ],
        request,
    )

    fallback_alphabet = next(pattern for pattern in fallback_patterns if pattern.id == "alphabet_abbreviations")
    llm_alphabet = llm_patterns[0]
    assert planner._reading_pattern_signature(fallback_alphabet) == planner._reading_pattern_signature(llm_alphabet)
    assert fallback_alphabet.recommended is llm_alphabet.recommended is True

