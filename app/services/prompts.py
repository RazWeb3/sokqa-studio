from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack, SokqaDocumentPack


def document_generation_prompt(plan: CoursePlan, document: PlanDocument) -> str:
    return f"""Create one Sokqa document JSON.

Rules:
- Return strict JSON only.
- type must be "document".
- schemaVersion must be 1.
- language must be "{plan.language}".
- Keep each documents[] item short for listening.
- Do not copy existing learning materials verbatim.
- Add tts only when it improves speech.

Course:
- title: {plan.title}
- target user: {plan.targetUser}
- difficulty: {plan.difficulty}

Document:
- id: {document.id}
- title: {document.title}
- goal: {document.goal}
- key points: {", ".join(document.keyPoints)}
- target section count: {document.targetSectionCount}
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
- questions must have exactly 4 choices.
- answerIndex must be an integer from 0 to 3.
- Every question must be grounded in the source documents.
- Do not copy existing exam questions verbatim.
- Add tts only when it improves speech.

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
"""
