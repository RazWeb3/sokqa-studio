from app.schemas.sokqa import GeneratedFile, SokqaDocumentPack, SokqaQuizPack


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
                question.question = rewrite_citation_style(question.question)
                question.choices = [rewrite_citation_style(choice) for choice in question.choices]
                question.explanation = rewrite_citation_style(question.explanation)
            file.content = pack.model_dump(exclude_none=True)
        elif file.kind == "document":
            pack = SokqaDocumentPack.model_validate(file.content)
            pack.documents = [item for item in pack.documents if item.text.strip()]
            file.content = pack.model_dump(exclude_none=True)
        repaired.append(file)
    return repaired


def rewrite_citation_style(text: str) -> str:
    repaired = text
    for old, new in CITATION_STYLE_REPLACEMENTS:
        repaired = repaired.replace(old, new)
    return repaired.strip()
