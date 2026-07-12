from app.schemas.pack_v2 import AddedPackFile, CommitPackRevisionInput, RevisionTarget
from app.schemas.request import GeneratePackRequest
from app.schemas.sokqa import CoursePlan
from app.services.generation.context import GenerationContext
from app.services.generation.language_learning.quality import tts_language_boundary_issues
from app.services.generation.language_learning.tts import normalize_language_tag_structure
from app.services.revision_commit import build_revision_commit


def _plan(*, language: str, learning_language: str | None, structure_policy: str = "summary") -> CoursePlan:
    return CoursePlan(
        id="context-pack", title="Context", description="Context", targetUser="Learner",
        difficulty="standard", language=language, learningLanguage=learning_language,
        structurePolicy=structure_policy, documents=[], quizPacks=[],
    )


def test_same_language_plan_is_standard_across_context() -> None:
    context = GenerationContext.from_request(
        GeneratePackRequest(plan=_plan(language="ja", learning_language="ja"), persist=False),
        tts_reading_mode="multilingual",
    )
    assert context.mode == "standard"
    assert context.allow_language_tags is False


def test_language_learning_context_allows_tags_only_in_multilingual_tts() -> None:
    request = GeneratePackRequest(plan=_plan(language="ja", learning_language="en"), persist=False)
    assert GenerationContext.from_request(request, tts_reading_mode="llm").allow_language_tags is False
    assert GenerationContext.from_request(request, tts_reading_mode="multilingual").allow_language_tags is True


def test_new_manifest_persists_resolved_generation_mode() -> None:
    result = build_revision_commit(
        None,
        CommitPackRevisionInput(
            target=RevisionTarget(creatorId="creator", contentId="content"),
            operation="initial_generate", title="Context", language="ja", generationMode="language_learning",
            addedFiles=[AddedPackFile(name="doc.json", kind="document", logicalId="doc", content={"id": "doc", "title": "Doc"})],
        ),
    )
    assert result.manifest.generationMode == "language_learning"


def test_tag_normalizer_removes_only_redundant_switches() -> None:
    assert normalize_language_tag_structure("[ja-JP]ロサンゼルス[ja-JP]のホテル", "ja") == "ロサンゼルスのホテル"
    assert normalize_language_tag_structure("[en-US]I'm staying in Los Angeles.[ja-JP]という意味です。", "ja") == "[en-US]I'm staying in Los Angeles.[ja-JP]という意味です。"


def test_ambiguous_tag_boundary_is_reported_without_rewrite() -> None:
    content = {
        "type": "document", "language": "ja", "documents": [
            {"id": "doc-1", "tts": {"text": "[en-US]Wi-Fiの利用料金"}},
        ],
    }
    issues = tts_language_boundary_issues("doc.json", content)
    assert [issue.category for issue in issues] == ["tts_language_boundary"]
