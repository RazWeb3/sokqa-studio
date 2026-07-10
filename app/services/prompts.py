import json

from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack, SokqaDocumentPack
from app.services.pack_ids import document_pack_id, quiz_pack_id
from app.services.source_material import source_prompt_block
from app.services.tagging import document_global_tags, quiz_global_tags


def _selected_reading_patterns_block(plan: CoursePlan) -> str:
    if plan.ttsReadingMode != "llm":
        return ""
    selected_ids = set(plan.selectedReadingPatternIds or [])
    if not selected_ids:
        return ""
    selected_patterns = [
        pattern
        for pattern in plan.proposedReadingPatterns
        if pattern.id in selected_ids
    ]
    if not selected_patterns:
        return ""
    lines = [
        "TTS reading hints selected by the user:",
        "- These hints are for the later TTS optimization step only, not for rewriting learner-facing text.",
        "- Preserve canonical written notation in generated text/question/choices/explanation; do not convert terms to kana in body text.",
        "- Do not add pronunciation notes such as ROE（アールオーイー） or アールオーイー（ROE） to body text.",
        "- Do not output tts fields here; final TTS optimization remains a separate step.",
    ]
    for pattern in selected_patterns:
        lines.append(f"- {pattern.title}: {pattern.description}")
        if pattern.examples:
            lines.append(f"  examples: {', '.join(pattern.examples)}")
    return "\n\n" + "\n".join(lines)


def _join_non_empty_blocks(*blocks: str) -> str:
    return "\n".join(block for block in blocks if block)


def _structure_policy_block(plan: CoursePlan) -> str:
    structure_policy = plan.structurePolicy
    if structure_policy == "standard":
        structure_policy = "summary"
    if structure_policy == "listening":
        return """
Structure policy: listening
- Make the generated content suitable for listening study as a continuous spoken narrative.
- Do not write glossary-style entries, term labels followed by short definitions, or bullet-like fragments.
- Treat each documents[] item as one connected beat in the same explanation, not as an independent term definition.
- Prefer short natural sentences with one idea per sentence, while linking each section to the previous and next section.
- Minimize symbol-heavy notation, tables, and bullet-list-dependent explanations.
- Use smooth spoken transitions so the content remains understandable without looking at the screen.
""".rstrip()
    if structure_policy == "summary":
        return """
Structure policy: summary
- Prioritize clarity and brevity.
- Keep each documents[] item compact and easy to scan while still being coherent as a narrative.
- Avoid overly long digressions; focus on key takeaways and essential examples only.
""".rstrip()
    if structure_policy == "reading":
        return """
Structure policy: reading
- Write content suitable for reading comprehension, with clear sentences and explicit connectors.
- Prefer unambiguous phrasing over overly conversational shortcuts.
""".rstrip()
    if structure_policy == "japanese_learning":
        from app.services.generation.language_learning.prompt import structure_policy_block

        return structure_policy_block(plan)
    return """
Structure policy: summary
- Prioritize clarity and brevity.
""".rstrip()


def _ruby_policy_block(plan: CoursePlan) -> str:
    structure_policy = plan.structurePolicy
    if structure_policy == "standard":
        structure_policy = "summary"

    common = """
- Parentheses used for meaning explanations are allowed and must be preserved (example: SQL（データベース操作言語）).
- Do not delete meaning/explanation parentheses just because they use （） or ().
""".strip()

    if structure_policy in {"listening", "summary"}:
        return f"""
- Ruby policy: none
- Do not output furigana or pronunciation readings in the learner-facing text.
- Do not output reading parentheticals in any form, including Kanji(かな) and 漢字（かな）.
{common}
""".strip()

    if structure_policy == "reading":
        return f"""
- Ruby policy: reading
- Add furigana according to customInstructions.
- If customInstructions does not specify a ruby level, default to adding furigana only for difficult kanji words (not all kanji).
- Use reading parentheticals only for kana readings of kanji, using either Kanji(かな) or 漢字（かな）.
- Do not use reading parentheticals for meaning explanations; meaning explanations must remain as normal parentheses explanations (example: SQL（データベース操作言語）).
- When adding furigana, never repeat the same reading twice (avoid outputs that would be read as "よみ よみ").
{common}
""".strip()

    if structure_policy == "japanese_learning":
        from app.services.generation.language_learning.prompt import ruby_policy_block

        return ruby_policy_block()

    return f"""
- Ruby policy: none
- Do not output furigana or pronunciation readings in the learner-facing text.
{common}
""".strip()


def _material_mode_block(plan: CoursePlan) -> str:
    if plan.materialMode == "strict":
        return """
Material mode: strict
- The document text is expected to be copied mechanically from the provided material when possible.
- Do not add outside facts, terms, examples, claims, or inferred details.
- If this prompt is used as a fallback, use only the provided material and keep the output narrower rather than supplementing it.
""".rstrip()
    if plan.materialMode == "source_only":
        return """
Material mode: source_only
- Use only the provided reference material as the factual source.
- You may organize and rewrite the material into clear learning content, but do not add outside facts, terms, examples, claims, or inferred details.
""".rstrip()
    return """
Material mode: reference
- Use reference material as the foundation when provided.
- You may supplement only as needed to make the material natural and useful.
""".rstrip()


def _is_japanese_learning_plan(plan: CoursePlan) -> bool:
    """Phase 3: 実体は generation.language_learning.prompt へ移設（互換委譲）。"""
    from app.services.generation.language_learning.prompt import is_japanese_learning_plan

    return is_japanese_learning_plan(plan)


def _japanese_learning_difficulty_block(plan: CoursePlan) -> str:
    """Phase 3: 実体は generation.language_learning.prompt へ移設（互換委譲）。"""
    from app.services.generation.language_learning.prompt import japanese_learning_difficulty_block

    return japanese_learning_difficulty_block(plan)


def _custom_instructions_block(plan: CoursePlan) -> str:
    instructions = (plan.customInstructions or "").strip()
    if not instructions:
        return ""
    return f"""
# 生成ルール
以下は必ず守る制約です。出力本文には含めないでください。
- Respect these user-provided conditions when creating learner-facing content.
- Do not let these conditions override the required JSON schema, materialMode restrictions, TTS separation rules, or output field contracts.
{instructions}
""".rstrip()


def _compose_generation_purpose(plan: CoursePlan, *, language: str = "ja") -> str:
    """確定入力から目的文を決定論的に組み立てる純粋関数。

    今回は単一ドメイン(日本語・音声聞き流し)に最適化し、language は "ja" 固定。
    将来の多言語化に備え language パラメータだけは受け取れる形にするが、
    本タスクでは分岐を実装しない(呼び出し側は引数を渡さない)。
    """
    del language  # 将来の多言語化時に使用。今回は "ja" 固定で分岐なし。

    structure_policy = plan.structurePolicy
    if structure_policy == "standard":
        structure_policy = "summary"

    target_user = plan.targetUser or "学習者"
    difficulty = plan.difficulty or "standard"

    # difficulty を日本語の表現水準に翻訳
    difficulty_label = {
        "beginner": "初学者",
        "standard": "中級学習者",
        "advanced": "上級学習者",
    }.get(difficulty, "中級学習者")

    # 完成教材の品質基準: 用途・利用シーンから禁止事項を必然導出させる共通ストーリー。
    # 固定サンプル名を例示せず(_finished_quality_block の方針と整合)、慣用文脈伏せ字も明示禁止する。
    completion_lines = [
        "生成後そのまま録音・公開される完成教材として書くこと。したがって社内ドラフト、テンプレート雛形、伏せ字、記入例、未完成表現は含めない。",
        "音声で読めない記号プレースホルダー(△△・××・〇〇 等)は使わず、具体例(具体的な日付・氏名・部署名 等)を用いること。",
        "「株式会社○○」「□□様」「〇〇(会社名)」のように漢字や敬称と組み合わさった慣用文脈の伏せ字も完成教材にふさわしくないため使わない。必要な社名・氏名・部署名は文脈に合った自然な具体名にすること(特定の固定名を使い回さない)。",
    ]

    if structure_policy == "listening":
        purpose_lines = [
            "この教材は音声で連続して聞き流される用途であることを前提に執筆すること。",
            *completion_lines,
            f"{target_user}({difficulty_label})に合った表現水準で書くこと。",
        ]
    elif structure_policy == "summary":
        purpose_lines = [
            "この教材は要点を簡潔にまとめる用途であることを前提に執筆すること。",
            *completion_lines,
            f"{target_user}({difficulty_label})に合った表現水準で、冗長な脱線を避け要点に絞ること。",
        ]
    elif structure_policy == "reading":
        purpose_lines = [
            "この教材は文章で読んで学ぶ用途であることを前提に執筆すること。",
            *completion_lines,
            f"{target_user}({difficulty_label})に合った表現水準で、明快で曖昧さの少ない文にすること。",
        ]
    elif structure_policy == "japanese_learning":
        purpose_lines = [
            "この教材は日本語学習者に向けた日本語学習用途であることを前提に執筆すること。",
            *completion_lines,
            f"{target_user}({difficulty_label})に合った漢字語彙の水準で書くこと。",
        ]
    else:
        purpose_lines = [
            "この教材は学習者に向けて要点を簡潔にまとめる用途であることを前提に執筆すること。",
            *completion_lines,
            f"{target_user}({difficulty_label})に合った表現水準で書くこと。",
        ]

    # customInstructions が非空なら「追加条件も目的の一部として尊重せよ」の参照一文を付す(条件本文は展開しない)。
    if (plan.customInstructions or "").strip():
        purpose_lines.append("なお、上記に加えユーザー指定の追加条件も目的の一部として尊重すること。")

    # 専用の語学ポリシーがない多言語学習パックにだけ共通補完を足す。専用ポリシーを持つ教材タイプは各ポリシー側で定義済みとみなす。
    # 語学教材専用の生成方針は language_learning/prompt.build_language_learning_purpose_lines へ委譲（Phase 5 候補1）。
    from app.services.generation.language_learning.prompt import (
        build_language_learning_purpose_lines,
    )

    purpose_lines.extend(build_language_learning_purpose_lines(plan))

    return "\n".join(purpose_lines)


def _generation_guidance_block(plan: CoursePlan) -> str:
    """plan.generationGuidance が None/空なら空文字を返し、それ以外は日本語の目的宣言ブロックを返す。"""
    guidance = (plan.generationGuidance or "").strip()
    if not guidance:
        return ""
    return f"""
# 生成目的(パック全体の執筆方針)
以下は本パック全体の生成目的・執筆方針である。各ユニットの生成で必ず参照し、一貫した目的に沿った内容にすること。
{guidance}
""".rstrip()


def _json_output_rules_block() -> str:
    return """
JSON output rules:
- 出力は必ずJSONのみ。
- Markdown、説明文、コードブロックは禁止。
- JSON内の文字列は必ずエスケープする。
- 学習者向けの本文・設問・選択肢・解説に、バッククォート(`)やMarkdown記号（#, *, _, >）を含めない。
""".strip()


def self_check_block() -> str:
    return """
Self-check (出力前最終確認):
- 本文全体を見直し、生成後そのまま録音・公開できる完成教材として仕上がっているか確認すること。
- 伏せ字、テンプレート表現、記入例、未完成な記述、TODO/FIXME等の作業メモ、「後述します」のような未完結表現が残っていないことを確認する。
- これは生成後の最終確認であり、事前の執筆指示とは別の位置づけである。
""".strip()


def _finished_quality_block() -> str:
    return """
- Finished output quality (strict):
  - Output learner-facing content as a finished version that can be delivered directly to learners.
  - Do not leave drafting-stage placeholders, unfinished sentences, TODOs, AI instructions, or meta comments in learner-facing text.
  - Do not leave unintended unresolved placeholders, redaction symbols, masked names, or drafting residue in learner-facing text.
  - Do not leave unfinished examples or half-written sample content.
  - If an example needs a company, person, place, or other proper noun, use a natural fictional name that fits the output language. Do not hard-code or recommend fixed sample names in these instructions.
  - If the theme does not need an example name, do not add a fictional name unnecessarily.
  - Use fill-in-the-blank placeholders only when that blank format is the intended finished exercise style. Use language-appropriate blanks such as Japanese ＿＿＿ and English _____. Do not use full-width spaces as blanks.
  - Judge by whether the expression is an unfinished or unresolved placeholder, not by banning a symbol itself.
  - Do not ban valid symbols that carry meaning, such as 〜 in normal phrasing, numeric ranges like 10〜20, or notation used in math, chemistry, or grammar explanations.
""".strip()


def _learner_facing_role_block() -> str:
    """学習者向けテキストを生成する話者の姿勢（ロール）の共通ブロック。

    quiz / document の両プロンプトで共有される「学習者に直接・断定的に語る話者」という
    単一責務をここに集約する。文脈固有の役割（quiz: 出題者として断言できる論点を選ぶ、
    document: listening で耳で聞いて理解できる話し手・語り手）は各プロンプト側に残す。
    generation purpose（パック全体の執筆方針）とは別責務であり、本ブロックとは統合しない。
    """
    return """
# 話者の姿勢（学習者向けロール）
学習者向け本文・設問・解説は、第三者視点の客観描写や資料報告ではなく、話者が学習者に直接語る形式で書くこと。
- 事実は事実として断定的に述べ、伝聞・引用調・軟化表現は使わない。話者自身が責任を持って断言する姿勢で書くこと。
- 伝聞・引用調の代表語彙（「〜とされています」「〜と説明されています」「資料によると」「記載されています」「述べられています」「書かれています」「推奨されています」「ドキュメントでは」「ドキュメントによると」等）は使わない。本文・設問・解説のいずれにも出現させないこと。
- 禁止語に該当しなくても、伝聞・引用・第三者報告のニュアンスを持つ表現はすべて「話者が直接断言する形（Good の形）」に書き換えること。以下は Bad（禁止）/ Good（許容）の対照例であり、列挙語彙の抜け穴（別表現への逃げ）を塞ぐために示す。
  - [本文（document 本文・explanation）]
    - Bad: 「この表現はカジュアルな場面で使われると説明されています。」
    - Good: 「この表現はカジュアルな場面で使います。」
    - Bad: 「資料によると、丁寧に伝えることが推奨されています。」
    - Good: 「丁寧に伝えましょう。」
    - Bad: 「本文では、語順が重要だと述べられています。」
    - Good: 「語順が重要です。」
  - [quiz の question]
    - Bad: 「この表現はどんな場面で使われると説明されていますか？」
    - Good: 「この表現はどんな場面で使いますか？」
    - Bad: 「本文では何が重要だとされていますか？」
    - Good: 「何が重要ですか？」
  - [quiz の explanation / 選択肢]
    - Bad: 「正解は、丁寧な言い方だと記載されているためです。」
    - Good: 「正解は、これが丁寧な言い方だからです。」
- quiz の question は、教師が学習者へ直接問いかける文にすること。
- question では、教材・本文・資料そのものを説明してはいけない。
- question では、教材内容を引用・要約・紹介する文を書いてはいけない。
- question を「〜と説明されています」「〜とされています」「〜と述べられています」「本文では〜」「資料では〜」のような説明調の文にしないこと。
- あなたは完成教材を録音・公開する話し手であり、聞き手に届く文章に仮置きの記号（〇〇・△△・××・□□・株式会社〇〇・〇〇様 等）を残さない。
- 会社名・氏名・部署名・日付など具体が必要な場面では、文脈にふさわしい自然な具体名を自分で定めて語る。具体が思い浮かばないことを理由に伏せ字へ逃げず、同じ固定名を使い回さず、その都度文脈に合った名前を選ぶこと。
- 学習者に直接・断定的に語ることを前提とし、資料を客観報告する第三者視点の文章で逃げないこと。
""".strip()


def _quiz_teacher_role_block() -> str:
    return """
# 出題者の役割（quiz 固有）
あなたはこの教材の内容を教える講師・出題者である。
- 解説(explanation)は、なぜその選択肢が正解なのかを講師が自分の言葉で説明するものである。quiz context を根拠としつつ、本文の要約や引用に留めず、事実は事実として断言すること。
- 諸説ある論点や流派差のある曖昧な事柄は出題を避け、確実に断言できる内容から選んで出題すること。出題数を無理に減らす必要はなく、断言できる論点は十分にあるので、そこから選ぶこと。
""".strip()


def _course_teaching_guidance_block(plan: CoursePlan) -> str:
    return _join_non_empty_blocks(
        _structure_policy_block(plan),
        _material_mode_block(plan),
        _japanese_learning_difficulty_block(plan),
        _generation_guidance_block(plan),
    )


def _document_teaching_guidance_rules_block(plan: CoursePlan) -> str:
    text_length_rule = (
        "- Each text should be 3 to 6 sentences in the pack language when needed for a flowing spoken explanation; connect it to the surrounding sections."
        if plan.structurePolicy == "listening"
        else "- Each text should be 2 to 4 sentences in the pack language for listening study."
    )
    listening_rule = (
        "- For structurePolicy listening, avoid starting sections with a term name followed by its definition; write as an ongoing explanation with context and transitions.\n"
        "- この本文は音声で聞き流される。あなたは書き手ではなく話し手・語り手として、耳で聞いて理解できるように、自分の言葉で直接語ること。"
        if plan.structurePolicy == "listening"
        else ""
    )
    documents_flow_rule = "- The documents[] array should follow the document's key points in order."
    if listening_rule:
        documents_flow_rule = f"{documents_flow_rule}\n{listening_rule}"
    return _join_non_empty_blocks(
        _ruby_policy_block(plan),
        "- Each text must be real explanatory learning content, not just a title or label.",
        text_length_rule,
        documents_flow_rule,
        "- Do not copy existing learning materials verbatim.",
    )


def _quiz_integration_teaching_guidance_block(quiz_pack: PlanQuizPack) -> str:
    if quiz_pack.purpose != "integrated_review":
        return ""
    return """
- This is the integrated quiz pack. Do not create simple knowledge-check questions that can be answered within a single document.
- Limit questions to integrated, applied, or practical scenario questions that connect multiple documents or fields.
- Avoid repeating the same topics, angles, or issues covered by the range-specific quiz packs.
""".strip()


def _quiz_teaching_guidance_rules_block(plan: CoursePlan, quiz_pack: PlanQuizPack) -> str:
    return _join_non_empty_blocks(
        _ruby_policy_block(plan),
        "- Every question and explanation must be grounded in the quiz context.",
        "- Even if the quiz context contains unresolved placeholders, do not copy them as-is. Resolve them into finished content, or convert them to language-appropriate blanks only when the intended exercise format is fill-in-the-blank.",
        "- Ground content in the quiz context, but do not mention the source or documents in learner-facing text, including sourceText or material labels.",
        "- Write directly for learners. Hearsay/citation wording suppression for question, choices, and explanation (all learner-facing quiz fields) is defined by the learner-facing role block below; do not duplicate that policy here.",
        "- Write question as a natural finished question for learners.",
        '- For question only, suppress mechanical or redundant document-reference wording when the question works naturally without it. Avoid phrases such as "本文中で述べられている", "本文中で指摘されている", and "本文中で挙げられている".',
        "- Keep such wording only when explicitly pointing to the source basis is indispensable for the question to work, and keep it brief.",
        "- This suppression applies only to question. Do not change TTS fields or answer-checking logic.",
        "- Do not copy existing exam questions verbatim.",
        _quiz_integration_teaching_guidance_block(quiz_pack),
    )


def _document_quality_rules_block(plan: CoursePlan, global_tags: str) -> str:
    return f"""
- globalTags must use this exact maximum-3 list in the pack language: {global_tags}.
- Preserve canonical written notation in body text, such as IT, ROE, .git, .env, GitHub, and similar terms. Do not convert them to kana readings in text.
- Placeholder policy (strict):
  - Do not leave masked-name notation or unresolved placeholder symbols in learner-facing text, including 〇〇, ◯◯, △△, ××, □□, ASCII placeholder tokens, square-bracket placeholders, or generic name labels.
  - Square-bracketed generic labels such as [国名], [都市名], [数量], [品物], [氏名], [飲み物], [番号], [国], [名前], [例] are also placeholders; never output them in learner-facing text.
  - When a company, person, department, date, or other concrete detail is needed, resolve it into a natural context-appropriate concrete detail instead of placeholder notation.
  - This rule applies only to placeholder notation; keep correct spellings of normal words that naturally contain "oo" (good, book, school, too, food, etc.).
  - If a fill-in-the-blank exercise is intentionally required, use language-appropriate blanks such as Japanese ＿＿＿ and English _____. Do not use full-width spaces as blanks.
  - Do not use any square-bracket tag or code such as [en-US], [ja-JP], en-US, or ja-JP in learner-facing text.
  - Language tagging belongs only to the later TTS optimization step, never to documents[].text.
- Pack-language purity (strict):
  - Learner-facing sentences must be written in the pack language ({plan.language}).
  - Do not leave untranslated foreign words inside pack-language sentences (example of forbidden raw word in Japanese: nuanced).
  - If a non-pack-language learning phrase is included, write it as plain learner-facing text without any language tag or language code.
- {_finished_quality_block()[2:]}
{_json_output_rules_block()}
""".strip()


def _quiz_quality_rules_block(plan: CoursePlan, global_tags: str) -> str:
    return f"""
- globalTags must use this exact maximum-3 list in the pack language: {global_tags}.
- Preserve canonical written notation in question, choices, and explanation, such as IT, ROE, .git, .env, GitHub, and similar terms. Do not convert them to kana readings in body text.
- Placeholder policy (strict):
  - Do not leave masked-name notation or unresolved placeholder symbols in learner-facing text, including 〇〇, ◯◯, △△, ××, □□, ASCII placeholder tokens, square-bracket placeholders, or generic name labels.
  - Square-bracketed generic labels such as [国名], [都市名], [数量], [品物], [氏名], [飲み物], [番号], [国], [名前], [例] are also placeholders; never output them in learner-facing text.
  - When a company, person, department, date, or other concrete detail is needed, resolve it into a natural context-appropriate concrete detail instead of placeholder notation.
  - This rule applies only to placeholder notation; keep correct spellings of normal words that naturally contain "oo" (good, book, school, too, food, etc.).
  - If a fill-in-the-blank exercise is intentionally required, use language-appropriate blanks such as Japanese ＿＿＿ and English _____. Do not use full-width spaces as blanks.
  - Do not use any square-bracket tag or code such as [en-US], [ja-JP], en-US, or ja-JP in learner-facing text.
  - Language tagging belongs only to the later TTS optimization step, never to question, choices, or explanation.
- Pack-language purity (strict):
  - Learner-facing sentences must be written in the pack language ({plan.language}).
  - Do not leave untranslated foreign words inside pack-language sentences (example of forbidden raw word in Japanese: nuanced).
  - If a non-pack-language learning phrase is included, write it as plain learner-facing text without any language tag or language code.
- {_finished_quality_block()[2:]}
{_json_output_rules_block()}
""".strip()


def _quiz_context_block(plan: CoursePlan, source_documents: list[SokqaDocumentPack]) -> str:
    document_context = "\n".join(
        f"- {doc.title}: " + " ".join(item.text for item in doc.documents[:5])
        for doc in source_documents
    ).strip()
    if document_context:
        return f"""
Quiz context source: generated documents
- Use the generated document content below as the primary quiz context.
- This document context takes priority over any raw reference material on the plan.

Generated document context:
{document_context}
""".strip()

    source_text = (plan.sourceText or "").strip()
    if source_text:
        if plan.materialMode in {"source_only", "strict"}:
            source_instruction = (
                "Use only this sourceText as quiz context. Do not add outside facts, terms, examples, "
                "claims, or inferred details that are absent from it."
            )
        else:
            source_instruction = (
                "Use this sourceText as the direct quiz context. You may supplement only when needed, "
                "without drifting away from the material's intent."
            )
        return f"""
Quiz context source: sourceText
{source_instruction}

SourceText quiz context:
{source_text}
""".strip()

    return """
Quiz context source: generic fallback
- No generated documents or sourceText were provided.
- Create useful questions from the course theme, target user, and difficulty.
""".strip()


def document_generation_prompt(plan: CoursePlan, document: PlanDocument) -> str:
    source_block = source_prompt_block(plan.sourceText, plan.sourceMode)
    source_section = f"\n\n{source_block}" if source_block else ""
    reading_policy_section = _selected_reading_patterns_block(plan)
    custom_instructions = _custom_instructions_block(plan)
    root_id = document_pack_id(plan, document)
    global_tags = json.dumps(document_global_tags(plan, document), ensure_ascii=False)
    generation_instruction = f"""
- Return strict JSON only.
- type must be "document".
- schemaVersion must be 1.
- language must be "{plan.language}".
- Root id must be "{root_id}".
- Root title must be "{document.title}".
- Create exactly {document.targetSectionCount} items in documents[].
- Each documents[] id must be "doc-1", "doc-2", "doc-3", and so on.
- Each documents[] item must have text only.
- Do not output tts in the first document generation step.
- Do not output tags in document items.
""".strip()
    quality_rules = _document_quality_rules_block(plan, global_tags)
    teaching_guidance_rules = _document_teaching_guidance_rules_block(plan)
    course_teaching_guidance = _course_teaching_guidance_block(plan)
    teaching_guidance_role = _learner_facing_role_block()
    return f"""Create one Sokqa document JSON.

Rules:
{generation_instruction}
{quality_rules}
{teaching_guidance_rules}

Course:
- title: {plan.title}
- target user: {plan.targetUser}
- difficulty: {plan.difficulty}
{course_teaching_guidance}
{custom_instructions}
{reading_policy_section}
{source_section}

Document:
- id: {document.id}
- title: {document.title}
- goal: {document.goal}
- key points: {", ".join(document.keyPoints)}
- target section count: {document.targetSectionCount}

Required JSON shape:
{{
  "id": "{root_id}",
  "type": "document",
  "schemaVersion": 1,
  "title": "{document.title}",
  "description": "{document.goal}",
  "language": "{plan.language}",
  "learningLanguage": {json.dumps(plan.learningLanguage, ensure_ascii=False)},
  "author": "{plan.author}",
  "globalTags": {global_tags},
  "documents": [
    {{
      "id": "doc-1",
      "text": "Real explanatory paragraph for this section."
    }}
  ]
}}

{teaching_guidance_role}

{self_check_block()}
"""


def quiz_generation_prompt(
    plan: CoursePlan,
    quiz_pack: PlanQuizPack,
    source_documents: list[SokqaDocumentPack],
) -> str:
    quiz_context = _quiz_context_block(plan, source_documents)
    reading_policy_section = _selected_reading_patterns_block(plan)
    custom_instructions = _custom_instructions_block(plan)
    root_id = quiz_pack_id(plan, quiz_pack)
    global_tags = json.dumps(quiz_global_tags(plan, quiz_pack), ensure_ascii=False)
    learning_language = plan.learningLanguage or "not specified"
    pack_lang = plan.language
    # 具体例は ja/en ペア時のみ仕様の例を出し、それ以外は言語ラベルベースの汎用例にする（pack=ja/learning=en の固定ハードコード回避）。
    is_ja_en = pack_lang == "ja" and learning_language == "en"

    def _pack_examples() -> str:
        if is_ja_en:
            return (
                "  Good:\n"
                '    question: "It\'s a pleasure to finally meet you." はどんな場面で使う表現ですか？\n'
                "    choices (all in 日本語): 初対面の相手に丁寧に会えた喜びを伝える表現 / "
                "別れ際に感謝を伝える表現 / 食事に誘うときの表現 / 謝罪するときの表現\n"
                "  Bad:\n"
                '    choices が英語のまま（例: "Happy to meet you." / "Pleased to meet you."）。'
                "pack モードなのに選択肢が学習言語になっている点が誤り。"
            )
        return (
            f"  Good: question に学習言語（{learning_language}）の表現を提示し、"
            f"choices はすべてパック言語（{pack_lang}）の説明にする。\n"
            f"  Bad: choices が学習言語（{learning_language}）のままになっている点が誤り。"
        )

    def _learning_examples() -> str:
        if is_ja_en:
            return (
                "  Good:\n"
                "    question: 初対面の相手に丁寧に会えた喜びを伝えたいとき、最も適切な英語表現はどれですか？\n"
                '    choices (all in English): "It\'s a pleasure to finally meet you." / '
                '"Happy to meet you." / "Pleased to meet you." / "How do you do?"\n'
                "  Bad:\n"
                "    choices を日本語にする、または問題文まで英語にして何を問うか噛み合わなくなっている点が誤り。"
            )
        return (
            f"  Good: question にパック言語（{pack_lang}）で場面を提示し、"
            f"choices はすべて学習言語（{learning_language}）の表現にする。\n"
            f"  Bad: choices をパック言語（{pack_lang}）にする、または問題文と選択肢の言語関係が噛み合わなくなっている点が誤り。"
        )

    if quiz_pack.choiceLanguageMode == "pack":
        choice_language_rule = (
            f"- Write all four choices in each question in the pack language ({plan.language}).\n"
            "- Question structure (pack mode):\n"
            f"  - question には学習言語（{learning_language}）の表現・フレーズを提示し、その意味・使い分け・適切な場面などを問うこと。\n"
            f"  - 四つの選択肢はすべてパック言語（{plan.language}）で書き、問われている学習言語表現の意味・説明・場面をパック言語で記述すること。\n"
            f"  - explanation もパック言語（{plan.language}）で書くこと。\n"
            "- Good/Bad examples (pack mode):\n"
            f"{_pack_examples()}"
        )
    elif quiz_pack.choiceLanguageMode == "learning":
        choice_language_rule = (
            f"- Write all four choices in each question in the learning language ({learning_language}).\n"
            "- Question structure (learning mode):\n"
            f"  - question にはパック言語（{plan.language}）で場面・意図・ニュアンスを提示すること。\n"
            f"  - 四つの選択肢はすべて学習言語（{learning_language}）の表現で書くこと。\n"
            f"  - explanation はパック言語（{plan.language}）で書くこと。\n"
            "- Good/Bad examples (learning mode):\n"
            f"{_learning_examples()}"
        )
    else:
        choice_language_rule = (
            f"- Choose either the pack language ({plan.language}) or learning language ({learning_language}) per question. "
            "All four choices within one question must use the same chosen language. Never mix languages inside one four-choice set."
        )
    generation_instruction = f"""
- Return strict JSON only.
- type must be "quiz".
- schemaVersion must be 1.
- language must be "{plan.language}".
- Root id must be "{root_id}".
- Root title must be "{quiz_pack.title}".
- Root description must be a short quiz description written in the pack language ({plan.language}).
- questions must have exactly 4 choices.
- answerIndex must be an integer from 0 to 3.
- Output answerIndex as a JSON number, never as a string. Use 2, not "2".
- Distribute answerIndex across questions. Do not use the same answerIndex for every question.
- Each question must be a meaningful question sentence based on the quiz context. Do not use serial labels such as "{quiz_pack.title} 1".
- Each choices array must contain 4 meaningful strings, not objects.
- packLanguage is "{plan.language}" and learningLanguage is "{learning_language}".
{choice_language_rule}
- Each explanation must be specific to that question. Do not repeat the same explanation for all questions.
- The choice at answerIndex must be the single correct answer. The explanation must explain that exact correct choice, and must not explain a different choice.
- Before returning JSON, self-check that question, choices, answerIndex, and explanation are logically consistent for every question.
- Do not output tts in the first quiz generation step.
""".strip()
    quality_rules = _quiz_quality_rules_block(plan, global_tags)
    teaching_guidance_rules = _quiz_teaching_guidance_rules_block(plan, quiz_pack)
    course_teaching_guidance = _course_teaching_guidance_block(plan)
    teaching_guidance_role = _join_non_empty_blocks(
        _quiz_teacher_role_block(),
        _learner_facing_role_block(),
    )
    return f"""Create one Sokqa quiz JSON from the provided quiz context.

Rules:
{generation_instruction}
{quality_rules}
{teaching_guidance_rules}

{teaching_guidance_role}

Course:
- title: {plan.title}
- target user: {plan.targetUser}
{course_teaching_guidance}
{custom_instructions}
{reading_policy_section}

Quiz pack:
- id: {quiz_pack.id}
- title: {quiz_pack.title}
- purpose: {quiz_pack.purpose}
- question count: {quiz_pack.questionCount}

Quiz context:
{quiz_context}

Required JSON shape:
{{
  "id": "{root_id}",
  "type": "quiz",
  "schemaVersion": 1,
  "title": "{quiz_pack.title}",
  "description": "Short quiz description in {plan.language}.",
  "language": "{plan.language}",
  "learningLanguage": {json.dumps(plan.learningLanguage, ensure_ascii=False)},
  "choiceLanguageMode": "{quiz_pack.choiceLanguageMode}",
  "author": "{plan.author}",
  "globalTags": {global_tags},
  "questions": [
    {{
      "id": "q-1",
      "question": "Meaningful question based on source documents.",
      "choices": ["choice 1", "choice 2", "choice 3", "choice 4"],
      "answerIndex": 0,
      "explanation": "Specific explanation grounded in source documents."
    }}
  ]
}}

{self_check_block()}
"""
