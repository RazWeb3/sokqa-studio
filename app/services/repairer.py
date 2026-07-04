import logging

from app.schemas.sokqa import GeneratedFile, SokqaDocumentPack, SokqaQuizPack


logger = logging.getLogger(__name__)


QUIZ_REPAIR_INSTRUCTIONS = """
Repair quiz JSON conservatively.
- Check that each answerIndex points to the single correct choice.
- Check that each explanation explains the choice at answerIndex, not another choice.
- If answerIndex, choices, and explanation are inconsistent, prefer rewriting explanation to match the correct choice; change answerIndex only when clearly necessary.
- Rewrite citation/hearsay wording such as "ドキュメントでは", "ドキュメントによると", "資料によると", "記載されています", "述べられています", "書かれています", and "推奨されています" into direct learner-facing Japanese.
- Do not change the learning content, correct answer, or choice order when fixing style.
""".strip()

CITATION_STYLE_REPLACEMENTS = (
    ("ドキュメントでは、", ""),
    ("ドキュメントでは", ""),
    ("ドキュメントによると、", ""),
    ("ドキュメントによると", ""),
    ("資料によると、", ""),
    ("資料によると", ""),
    ("と記載されています", "です"),
    ("記載されています", "説明できます"),
    ("と述べられています", "です"),
    ("述べられています", "説明できます"),
    ("と書かれています", "です"),
    ("書かれています", "説明できます"),
    ("推奨されています", "適しています"),
)


def repair_files(files: list[GeneratedFile]) -> list[GeneratedFile]:
    repaired: list[GeneratedFile] = []
    for file in files:
        if file.kind == "quiz":
            pack = SokqaQuizPack.model_validate(file.content)
            for question in pack.questions:
                while len(question.choices) < 4:
                    question.choices.append(f"補足選択肢{len(question.choices) + 1}")
                question.choices = question.choices[:4]
                if question.answerIndex < 0 or question.answerIndex > 3:
                    question.answerIndex = 0
                question.question = _rewrite_and_log_citation_style(file.name, question.id, "question", question.question)
                question.choices = [
                    _rewrite_and_log_citation_style(file.name, question.id, f"choices[{index}]", choice)
                    for index, choice in enumerate(question.choices)
                ]
                question.explanation = _rewrite_and_log_citation_style(file.name, question.id, "explanation", question.explanation)
            file.content = pack.model_dump(exclude_none=True)
        elif file.kind == "document":
            pack = SokqaDocumentPack.model_validate(file.content)
            pack.documents = [item for item in pack.documents if item.text.strip()]
            file.content = pack.model_dump(exclude_none=True)
        repaired.append(file)
    return repaired


def _rewrite_and_log_citation_style(file_name: str, unit_id: str, field: str, text: str) -> str:
    after = rewrite_citation_style(text)
    if after != text:
        logger.info(
            "repair.citation_style_rewritten file=%s unit_id=%s field=%s before=%r after=%r",
            file_name,
            unit_id,
            field,
            text,
            after,
        )
    return after


def rewrite_citation_style(text: str) -> str:
    repaired = text
    for old, new in CITATION_STYLE_REPLACEMENTS:
        repaired = repaired.replace(old, new)
    return repaired.strip()
