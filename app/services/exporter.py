from app.schemas.sokqa import CoursePlan, GeneratedFile, ManifestCreator, ManifestItem, PackManifest, SokqaDocumentPack, SokqaQuizPack
from app.services.pack_metadata import PackBuildMetadata


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


def build_manifest(plan: CoursePlan, files: list[GeneratedFile], metadata: PackBuildMetadata | None = None) -> PackManifest:
    content_id = metadata.content_id if metadata else plan.contentId
    return PackManifest(
        id=f"{content_id or plan.id}_manifest",
        contentId=content_id,
        slug=metadata.slug if metadata else plan.slug,
        versionId=metadata.version_id if metadata else None,
        buildId=metadata.build_id if metadata else None,
        generatedAt=metadata.generated_at if metadata else None,
        creator=ManifestCreator(
            id=metadata.creator_id,
            displayName=metadata.creator_display_name,
        ) if metadata else None,
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
