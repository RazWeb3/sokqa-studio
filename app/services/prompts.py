from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack, SokqaDocumentPack
from app.services.source_material import source_prompt_block


def document_generation_prompt(plan: CoursePlan, document: PlanDocument) -> str:
    source_block = source_prompt_block(plan.sourceText, plan.sourceMode)
    source_section = f"\n\n{source_block}" if source_block else ""
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
- Each text must be real explanatory learning content, not just a title or label.
- Each text should be 2 to 4 Japanese sentences for listening study.
- Do not copy existing learning materials verbatim.

Course:
- title: {plan.title}
- target user: {plan.targetUser}
- difficulty: {plan.difficulty}
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
- Distribute answerIndex across questions. Do not use the same answerIndex for every question.
- Each question must be a meaningful question sentence based on the source documents. Do not use serial labels such as "{quiz_pack.title} 1".
- Each choices array must contain 4 meaningful strings, not objects.
- Each explanation must be specific to that question. Do not repeat the same explanation for all questions.
- Do not output tts in the first quiz generation step.
- Every question must be grounded in the source documents.
- Do not copy existing exam questions verbatim.

Course:
- title: {plan.title}
- target user: {plan.targetUser}

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
