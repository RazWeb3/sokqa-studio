from app.schemas.sokqa import GeneratedFile, SokqaDocumentPack, SokqaQuizPack


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
            file.content = pack.model_dump(exclude_none=True)
        elif file.kind == "document":
            pack = SokqaDocumentPack.model_validate(file.content)
            pack.documents = [item for item in pack.documents if item.text.strip()]
            file.content = pack.model_dump(exclude_none=True)
        repaired.append(file)
    return repaired
