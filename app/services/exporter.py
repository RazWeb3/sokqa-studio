from app.schemas.sokqa import CoursePlan, GeneratedFile, ManifestItem, PackManifest, SokqaDocumentPack, SokqaQuizPack


def build_generated_files(document_packs: list[SokqaDocumentPack], quiz_packs: list[SokqaQuizPack]) -> list[GeneratedFile]:
    files: list[GeneratedFile] = []
    for index, pack in enumerate(document_packs, start=1):
        files.append(
            GeneratedFile(
                name=f"doc_{index:02d}.json",
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


def build_manifest(plan: CoursePlan, files: list[GeneratedFile]) -> PackManifest:
    return PackManifest(
        id=f"{plan.id}_manifest",
        title=plan.title,
        description=plan.description,
        language=plan.language,
        author=plan.author,
        scale=plan.scale,
        globalTags=[plan.id, plan.difficulty],
        items=[
            ManifestItem(kind=file.kind, url=file.url or "")
            for file in files
            if file.kind in {"document", "quiz"}
        ],
    )
