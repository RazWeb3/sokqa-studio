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


def _structure_policy_block(plan: CoursePlan) -> str:
    if plan.structurePolicy == "listening":
        return """
Structure policy: listening
- Make the generated content suitable for listening study as a continuous spoken narrative.
- Do not write glossary-style entries, term labels followed by short definitions, or bullet-like fragments.
- Treat each documents[] item as one connected beat in the same explanation, not as an independent term definition.
- Prefer short natural sentences with one idea per sentence, while linking each section to the previous and next section.
- Minimize symbol-heavy notation, tables, and bullet-list-dependent explanations.
- Use smooth spoken transitions so the content remains understandable without looking at the screen.
""".rstrip()
    return """
Structure policy: standard
- Use the existing balanced Sokqa course style.
- Balance conceptual explanation, practical examples, and review.
""".rstrip()


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
    parts = [
        plan.title,
        plan.description,
        plan.shortTitle or "",
        plan.targetUser,
        *[document.title for document in plan.documents],
        *[document.goal for document in plan.documents],
        *[point for document in plan.documents for point in document.keyPoints],
    ]
    text = " ".join(str(part) for part in parts if part).lower()
    markers = ["日本語", "にほんご", "japanese", "jlpt", "n5", "n4", "ひらがな", "カタカナ"]
    return plan.language != "ja" and any(marker in text for marker in markers)


def _japanese_learning_difficulty_block(plan: CoursePlan) -> str:
    if not _is_japanese_learning_plan(plan):
        return ""
    if plan.difficulty == "beginner":
        guidance = """
- For learner-facing Japanese examples and target-language spans, avoid kanji in principle.
- Prefer hiragana and katakana, and use only JLPT N5-level vocabulary.
- Examples: use じこしょうかい instead of 自己紹介, あいさつ instead of 挨拶, and はじめて あう instead of 初対面.
""".rstrip()
    elif plan.difficulty == "advanced":
        guidance = """
- Learner-facing Japanese examples may use natural Japanese without kanji restrictions.
- Keep the surrounding explanation in the pack language unless a Japanese span is intentionally shown as learning content.
""".rstrip()
    else:
        guidance = """
- Learner-facing Japanese examples may use kanji up to roughly JLPT N4 level.
- Add readings or simpler phrasing when needed for accessibility.
""".rstrip()
    return f"""
Japanese-learning difficulty guidance:
{guidance}
""".rstrip()


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


def _json_output_rules_block() -> str:
    return """
JSON output rules:
- 出力は必ずJSONのみ。
- Markdown、説明文、コードブロックは禁止。
- JSON内の文字列は必ずエスケープする。
- 学習者向けの本文・設問・選択肢・解説に、バッククォート(`)やMarkdown記号（#, *, _, >）を含めない。
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
    structure_policy = _structure_policy_block(plan)
    material_policy = _material_mode_block(plan)
    japanese_learning_policy = _japanese_learning_difficulty_block(plan)
    custom_instructions = _custom_instructions_block(plan)
    root_id = document_pack_id(plan, document)
    global_tags = json.dumps(document_global_tags(plan, document), ensure_ascii=False)
    text_length_rule = (
        "- Each text should be 3 to 6 sentences in the pack language when needed for a flowing spoken explanation; connect it to the surrounding sections."
        if plan.structurePolicy == "listening"
        else "- Each text should be 2 to 4 sentences in the pack language for listening study."
    )
    listening_rule = (
        "\n- For structurePolicy listening, avoid starting sections with a term name followed by its definition; write as an ongoing explanation with context and transitions."
        if plan.structurePolicy == "listening"
        else ""
    )
    return f"""Create one Sokqa document JSON.

Rules:
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
- globalTags must use this exact maximum-3 list in the pack language: {global_tags}.
- Preserve canonical written notation in body text, such as IT, ROE, .git, .env, GitHub, and similar terms. Do not convert them to kana readings in text.
- Do not add pronunciation-only parentheticals in body text; parentheses may be used only for meaning explanations, not readings.
- Placeholder policy (strict):
  - Use only 〜 or ◯◯ as placeholders in learner-facing text.
  - Do not replace the placeholder ◯◯ with ASCII placeholder tokens like OO or oo. This rule applies only to placeholder notation; keep correct spellings of normal words that naturally contain "oo" (good, book, school, too, food, etc.).
  - Do not output square-bracket placeholders such as [名前], [会社名], or [自分の名前]. Square brackets are reserved for TTS language tags.
  - Do not use any square-bracket tag or code such as [en-US], [ja-JP], en-US, or ja-JP in learner-facing text.
  - Language tagging belongs only to the later TTS optimization step, never to documents[].text.
  - Bad: do not use square brackets to wrap a placeholder. Good: "I'm from 〜."
- Pack-language purity (strict):
  - Learner-facing sentences must be written in the pack language ({plan.language}).
  - Do not leave untranslated foreign words inside pack-language sentences (example of forbidden raw word in Japanese: nuanced).
  - If a non-pack-language learning phrase is included, write it as plain learner-facing text without any language tag or language code.
- Each text must be real explanatory learning content, not just a title or label.
- {text_length_rule[2:]}
- The documents[] array should follow the document's key points in order.{listening_rule}
- Do not copy existing learning materials verbatim.
{_json_output_rules_block()}

Course:
- title: {plan.title}
- target user: {plan.targetUser}
- difficulty: {plan.difficulty}
{structure_policy}
{material_policy}
{japanese_learning_policy}
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
"""


def quiz_generation_prompt(
    plan: CoursePlan,
    quiz_pack: PlanQuizPack,
    source_documents: list[SokqaDocumentPack],
) -> str:
    quiz_context = _quiz_context_block(plan, source_documents)
    reading_policy_section = _selected_reading_patterns_block(plan)
    structure_policy = _structure_policy_block(plan)
    material_policy = _material_mode_block(plan)
    japanese_learning_policy = _japanese_learning_difficulty_block(plan)
    custom_instructions = _custom_instructions_block(plan)
    root_id = quiz_pack_id(plan, quiz_pack)
    global_tags = json.dumps(quiz_global_tags(plan, quiz_pack), ensure_ascii=False)
    learning_language = plan.learningLanguage or "not specified"
    if quiz_pack.choiceLanguageMode == "pack":
        choice_language_rule = (
            f"- Write all four choices in each question in the pack language ({plan.language})."
        )
    elif quiz_pack.choiceLanguageMode == "learning":
        choice_language_rule = (
            f"- Write all four choices in each question in the learning language ({learning_language})."
        )
    else:
        choice_language_rule = (
            f"- Choose either the pack language ({plan.language}) or learning language ({learning_language}) per question. "
            "All four choices within one question must use the same chosen language. Never mix languages inside one four-choice set."
        )
    integration_rules = ""
    if quiz_pack.purpose == "integrated_review":
        integration_rules = """
- This is the integrated quiz pack. Do not create simple knowledge-check questions that can be answered within a single document.
- Limit questions to integrated, applied, or practical scenario questions that connect multiple documents or fields.
- Avoid repeating the same topics, angles, or issues covered by the range-specific quiz packs.
"""
    return f"""Create one Sokqa quiz JSON from the provided quiz context.

Rules:
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
- globalTags must use this exact maximum-3 list in the pack language: {global_tags}.
- Preserve canonical written notation in question, choices, and explanation, such as IT, ROE, .git, .env, GitHub, and similar terms. Do not convert them to kana readings in body text.
- Do not add pronunciation-only parentheticals in question, choices, or explanation; parentheses may be used only for meaning explanations, not readings.
- Placeholder policy (strict):
  - Use only 〜 or ◯◯ as placeholders in learner-facing text.
  - Do not replace the placeholder ◯◯ with ASCII placeholder tokens like OO or oo. This rule applies only to placeholder notation; keep correct spellings of normal words that naturally contain "oo" (good, book, school, too, food, etc.).
  - Do not output square-bracket placeholders such as [名前], [会社名], or [自分の名前]. Square brackets are reserved for TTS language tags.
  - Do not use any square-bracket tag or code such as [en-US], [ja-JP], en-US, or ja-JP in learner-facing text.
  - Language tagging belongs only to the later TTS optimization step, never to question, choices, or explanation.
  - Bad: do not use square brackets to wrap a placeholder. Good: "I'm from 〜."
- Pack-language purity (strict):
  - Learner-facing sentences must be written in the pack language ({plan.language}).
  - Do not leave untranslated foreign words inside pack-language sentences (example of forbidden raw word in Japanese: nuanced).
  - If a non-pack-language learning phrase is included, write it as plain learner-facing text without any language tag or language code.
- Every question must be grounded in the quiz context.
- Even if the quiz context contains square-bracket placeholders or ASCII placeholder tokens like OO/oo, do not copy them. Normalize placeholders to 〜 or ◯◯ in your output.
- Ground content in the quiz context, but do not mention the source or documents in learner-facing text, including sourceText or material labels.
- Write directly for learners. Do not use hearsay/citation wording such as "ドキュメントでは", "ドキュメントによると", "資料によると", "記載されています", "述べられています", "書かれています", or "推奨されています".
- Do not copy existing exam questions verbatim.
{_json_output_rules_block()}
{integration_rules}

Course:
- title: {plan.title}
- target user: {plan.targetUser}
{structure_policy}
{material_policy}
{japanese_learning_policy}
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
"""
