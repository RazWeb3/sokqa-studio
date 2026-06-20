from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack, SokqaDocumentPack
from app.services.source_material import source_prompt_block


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


def document_generation_prompt(plan: CoursePlan, document: PlanDocument) -> str:
    source_block = source_prompt_block(plan.sourceText, plan.sourceMode)
    source_section = f"\n\n{source_block}" if source_block else ""
    reading_policy_section = _selected_reading_patterns_block(plan)
    return f"""Create one Sokqa document JSON.

Rules:
- Return strict JSON only.
- type must be "document".
- schemaVersion must be 1.
- language must be "{plan.language}".
- Root id must be "{plan.id}_{document.id}".
- Root title must be "{document.title}".
- Create exactly {document.targetSectionCount} items in documents[].
- Each documents[] id must be "doc-1", "doc-2", "doc-3", and so on.
- Each documents[] item must have text only.
- Do not output tts in the first document generation step.
- Do not output tags in document items.
- Preserve canonical written notation in body text, such as IT, ROE, .git, .env, GitHub, and similar terms. Do not convert them to kana readings in text.
- Do not add pronunciation-only parentheticals in body text; parentheses may be used only for meaning explanations, not readings.
- Each text must be real explanatory learning content, not just a title or label.
- Each text should be 2 to 4 Japanese sentences for listening study.
- Do not copy existing learning materials verbatim.

Course:
- title: {plan.title}
- target user: {plan.targetUser}
- difficulty: {plan.difficulty}
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
  "id": "{plan.id}_{document.id}",
  "type": "document",
  "schemaVersion": 1,
  "title": "{document.title}",
  "description": "{document.goal}",
  "language": "{plan.language}",
  "author": "{plan.author}",
  "globalTags": ["{plan.id}", "{plan.difficulty}"],
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
    source_text = "\n".join(
        f"- {doc.title}: " + " ".join(item.text for item in doc.documents[:5])
        for doc in source_documents
    )
    reading_policy_section = _selected_reading_patterns_block(plan)
    integration_rules = ""
    if quiz_pack.purpose == "integrated_review":
        integration_rules = """
- This is the integrated quiz pack. Do not create simple knowledge-check questions that can be answered within a single document.
- Limit questions to integrated, applied, or practical scenario questions that connect multiple documents or fields.
- Avoid repeating the same topics, angles, or issues covered by the range-specific quiz packs.
"""
    return f"""Create one Sokqa quiz JSON from the provided document content.

Rules:
- Return strict JSON only.
- type must be "quiz".
- schemaVersion must be 1.
- language must be "{plan.language}".
- Root id must be "{plan.id}_{quiz_pack.id}".
- Root title must be "{quiz_pack.title}".
- questions must have exactly 4 choices.
- answerIndex must be an integer from 0 to 3.
- Output answerIndex as a JSON number, never as a string. Use 2, not "2".
- Distribute answerIndex across questions. Do not use the same answerIndex for every question.
- Each question must be a meaningful question sentence based on the source documents. Do not use serial labels such as "{quiz_pack.title} 1".
- Each choices array must contain 4 meaningful strings, not objects.
- Each explanation must be specific to that question. Do not repeat the same explanation for all questions.
- The choice at answerIndex must be the single correct answer. The explanation must explain that exact correct choice, and must not explain a different choice.
- Before returning JSON, self-check that question, choices, answerIndex, and explanation are logically consistent for every question.
- Do not output tts in the first quiz generation step.
- Preserve canonical written notation in question, choices, and explanation, such as IT, ROE, .git, .env, GitHub, and similar terms. Do not convert them to kana readings in body text.
- Do not add pronunciation-only parentheticals in question, choices, or explanation; parentheses may be used only for meaning explanations, not readings.
- Every question must be grounded in the source documents.
- Ground content in the source, but do not mention the source or documents in learner-facing text.
- Write directly for learners. Do not use hearsay/citation wording such as "ドキュメントでは", "ドキュメントによると", "資料によると", "記載されています", "述べられています", "書かれています", or "推奨されています".
- Do not copy existing exam questions verbatim.
{integration_rules}

Course:
- title: {plan.title}
- target user: {plan.targetUser}
{reading_policy_section}

Quiz pack:
- id: {quiz_pack.id}
- title: {quiz_pack.title}
- purpose: {quiz_pack.purpose}
- question count: {quiz_pack.questionCount}

Source documents:
{source_text}

Required JSON shape:
{{
  "id": "{plan.id}_{quiz_pack.id}",
  "type": "quiz",
  "schemaVersion": 1,
  "title": "{quiz_pack.title}",
  "description": "{plan.title}のドキュメント本文に基づく{quiz_pack.title}です。",
  "language": "{plan.language}",
  "author": "{plan.author}",
  "globalTags": ["{plan.id}", "{quiz_pack.purpose}"],
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
