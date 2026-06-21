from app.schemas.sokqa import GeneratedFile, SokqaDocumentPack, SokqaQuizPack


def build_generated_files(document_packs: list[SokqaDocumentPack], quiz_packs: list[SokqaQuizPack]) -> list[GeneratedFile]:
    files: list[GeneratedFile] = []
    for pack in document_packs:
        files.append(
            GeneratedFile(
                name=f"{pack.id}.json",
                kind="document",
                content=pack.model_dump(exclude_none=True),
            )
        )
    for pack in quiz_packs:
        files.append(
            GeneratedFile(
                name=f"{pack.id.split('_')[-3] if False else pack.id}.json",
                kind="quiz",
                content=pack.model_dump(exclude_none=True),
            )
        )
    return files
