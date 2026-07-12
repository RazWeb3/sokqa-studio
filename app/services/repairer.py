import logging
from typing import Any

from pydantic import ValidationError

from app.schemas.sokqa import GeneratedFile, SokqaDocumentPack, SokqaQuizPack


logger = logging.getLogger(__name__)


QUIZ_REPAIR_INSTRUCTIONS = """
Repair quiz JSON conservatively.
- Check that each answerIndex points to the single correct choice.
- Check that each explanation explains the choice at answerIndex, not another choice.
- If answerIndex, choices, and explanation are inconsistent, prefer rewriting explanation to match the correct choice; change answerIndex only when clearly necessary.
- Do not rewrite learner-facing natural language with string replacement. Citation/hearsay wording must be handled at generation time and reported by validation only.
- Do not change the learning content, correct answer, or choice order when fixing style.
""".strip()


def repair_files(files: list[GeneratedFile]) -> list[GeneratedFile]:
    repaired: list[GeneratedFile] = []
    for file in files:
        try:
            if file.kind == "quiz":
                pack = SokqaQuizPack.model_validate(_normalize_quiz_content_for_repair(file.content))
                file.content = pack.model_dump(exclude_none=True)
            elif file.kind == "document":
                pack = SokqaDocumentPack.model_validate(file.content)
                pack.documents = [item for item in pack.documents if item.text.strip()]
                file.content = pack.model_dump(exclude_none=True)
        except ValidationError:
            # Never turn a malformed response into a superficially valid quiz
            # with fabricated choices or answers.  The original content stays
            # intact for the pack-level structural gate.
            logger.warning("repair skipped structurally invalid file=%s", file.name)
        repaired.append(file)
    return repaired


def _normalize_quiz_content_for_repair(content: Any) -> Any:
    if not isinstance(content, dict):
        return content
    normalized = dict(content)
    raw_questions = normalized.get("questions")
    if not isinstance(raw_questions, list):
        return normalized

    normalized_questions: list[Any] = []
    for raw_question in raw_questions:
        if not isinstance(raw_question, dict):
            normalized_questions.append(raw_question)
            continue
        question = dict(raw_question)
        choices = question.get("choices")
        if isinstance(choices, list):
            question["choices"] = list(choices)
        answer_index = question.get("answerIndex")
        if isinstance(answer_index, bool) or not isinstance(answer_index, int) or answer_index < 0 or answer_index >= len(question.get("choices") or []):
            # A correct answer cannot be inferred safely.  Keep the malformed
            # value so the pack-level persistence gate can reject it instead
            # of manufacturing a learner-visible answer.
            question["answerIndex"] = answer_index
        normalized_questions.append(question)
    normalized["questions"] = normalized_questions
    return normalized
