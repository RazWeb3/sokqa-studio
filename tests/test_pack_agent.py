from fastapi.testclient import TestClient

from app.config import get_settings
from app.schemas.pack_v2 import PackManifestV2
from app.schemas.request import GeneratePackRequest, PlanPackRequest
from app.schemas.sokqa import CoursePlan, GeneratePackResponse, ValidationResult
from app.services.pack_agent import generate_pack, plan_pack
from app.services.pack_metadata import build_pack_metadata
from main import app


client = TestClient(app)


def _kind_count(items: list[dict], kind: str) -> int:
    return sum(1 for item in items if item["kind"] == kind)


def _assert_partitioned_quiz_packs(plan: dict, expected_count: int, expected_questions: int) -> None:
    document_ids = [document["id"] for document in plan["documents"]]
    quiz_packs = plan["quizPacks"]

    assert len(quiz_packs) == expected_count
    assert [pack["questionCount"] for pack in quiz_packs] == [expected_questions] * expected_count
    if expected_count >= 3:
        range_packs = quiz_packs[:-1]
        integrated_pack = quiz_packs[-1]
        assert "総合確認" in integrated_pack["title"]
        assert integrated_pack["purpose"] == "integrated_review"
        assert integrated_pack["sourceDocumentIds"] == document_ids
    else:
        range_packs = quiz_packs

    range_ids = []
    for pack in range_packs:
        assert pack["sourceDocumentIds"]
        if expected_count >= 3:
            assert pack["sourceDocumentIds"] != document_ids
        range_ids.extend(pack["sourceDocumentIds"])
    assert range_ids == document_ids
    assert len(range_ids) == len(set(range_ids))


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_quick_plan_and_generate() -> None:
    plan_response = client.post(
        "/plan-pack",
        json={
            "theme": "ITパスポート試験対策",
            "targetUser": "IT初心者の社会人",
            "scale": "quick",
        },
    )
    assert plan_response.status_code == 200
    plan = plan_response.json()
    document_count = len(plan["documents"])
    quiz_count = len(plan["quizPacks"])
    plan_section_counts = [document["targetSectionCount"] for document in plan["documents"]]
    assert document_count == 3
    assert all(35 <= count <= 50 for count in plan_section_counts)
    assert len(set(plan_section_counts)) > 1
    assert plan["scale"] == "quick"
    assert "quality" not in plan
    assert len(plan["quizPacks"]) == 1
    assert plan["quizPacks"][0]["questionCount"] == 30

    generate_response = client.post(
        "/generate-pack",
        json={
            "plan": plan,
            "ttsReadingMode": "none",
            "persist": False,
        },
    )
    assert generate_response.status_code == 200
    generated = generate_response.json()
    assert generated["validation"]["valid"] is True
    generated_section_counts = [
        len(file["content"]["documents"])
        for file in generated["files"]
        if file["kind"] == "document"
    ]
    assert generated_section_counts == plan_section_counts
    assert len(set(generated_section_counts)) > 1
    assert generated["manifest"]["scale"] == "quick"
    assert "quality" not in generated["manifest"]
    assert len(generated["manifest"]["items"]) == document_count + quiz_count
    assert _kind_count(generated["manifest"]["items"], "document") == document_count
    assert _kind_count(generated["manifest"]["items"], "quiz") == quiz_count
    assert len(generated["files"]) == len(generated["manifest"]["items"]) + 1
    # Debug prompts: default disabled -> empty list
    assert generated["prompts"] == []


def test_generation_unit_document_only_generates_only_documents() -> None:
    plan = plan_pack(
        PlanPackRequest(
            theme="Document Only",
            targetUser="Learners",
            generationUnit="document",
            docCount=1,
            quizCount=3,
            ttsReadingMode="none",
        )
    )

    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))

    assert len(generated.manifest.items) == 1
    assert all(item.kind == "document" for item in generated.manifest.items)
    assert generated.plan.generationUnit == "document"


def test_generation_unit_quiz_only_generates_only_quizzes() -> None:
    plan = plan_pack(
        PlanPackRequest(
            theme="Quiz Only",
            targetUser="Learners",
            generationUnit="quiz",
            quizCount=1,
            ttsReadingMode="none",
        )
    )

    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))

    assert len(generated.manifest.items) == 1
    assert all(item.kind == "quiz" for item in generated.manifest.items)
    assert generated.plan.generationUnit == "quiz"


def test_quiz_only_generation_uses_source_text_as_mock_context() -> None:
    source_text = "資料固有の論点として、二要素認証と長いパスワードの併用を扱います。"
    plan = plan_pack(
        PlanPackRequest(
            theme="Quiz Source",
            targetUser="Learners",
            generationUnit="quiz",
            quizCount=1,
            sourceText=source_text,
            materialMode="strict",
            ttsReadingMode="none",
        )
    )

    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))
    quiz_file = next(file for file in generated.files if file.kind == "quiz")
    first_explanation = quiz_file.content["questions"][0]["explanation"]

    assert "二要素認証と長いパスワード" in first_explanation
    assert generated.plan.sourceMode == "document_only"
    assert generated.plan.materialMode == "source_only"


def test_strict_document_generation_copies_source_without_document_llm(monkeypatch) -> None:
    source_text = "\n\n".join([f"原文段落{i}" for i in range(1, 43)])
    plan = plan_pack(
        PlanPackRequest(
            theme="資料準拠",
            targetUser="読者",
            generationUnit="document",
            docCount=1,
            sectionsPerDocument=42,
            sourceText=source_text,
            materialMode="strict",
            ttsReadingMode="none",
        )
    )

    def fail_document_generation(*_args, **_kwargs):
        raise AssertionError("strict source copy must not call document LLM generation")

    monkeypatch.setattr("app.services.pack_agent.generate_document_pack", fail_document_generation)
    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))
    document_file = next(file for file in generated.files if file.kind == "document")
    texts = [item["text"] for item in document_file.content["documents"]]

    assert generated.plan.materialMode == "strict"
    assert generated.plan.sourceMode == "document_only"
    assert texts == [f"原文段落{i}" for i in range(1, 43)]


def test_strict_document_generation_accepts_short_source_and_preserves_paragraph_text() -> None:
    source_text = "  第一段落の本文です。\n改行は本文内に残します。  \n\n第二段落の本文です。"
    plan = plan_pack(
        PlanPackRequest(
            theme="短い資料",
            targetUser="読者",
            generationUnit="document",
            docCount=1,
            sectionsPerDocument=42,
            sourceText=source_text,
            materialMode="strict",
            ttsReadingMode="none",
        )
    )

    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))
    document_file = next(file for file in generated.files if file.kind == "document")
    texts = [item["text"] for item in document_file.content["documents"]]

    assert generated.validation.valid is True
    assert len(texts) == 2
    assert texts == ["第一段落の本文です。\n改行は本文内に残します。", "第二段落の本文です。"]


def test_strict_large_source_splits_into_multiple_document_files_in_one_manifest() -> None:
    source_paragraphs = [f"原文段落{i:04d}" for i in range(1, 121)]
    source_text = "\n\n".join(source_paragraphs)
    plan = plan_pack(
        PlanPackRequest(
            theme="大きな資料",
            targetUser="読者",
            generationUnit="document",
            sourceText=source_text,
            materialMode="strict",
            ttsReadingMode="none",
        )
    )

    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))
    document_files = [file for file in generated.files if file.kind == "document"]
    actual_texts = [
        item["text"]
        for file in document_files
        for item in file.content["documents"]
    ]

    assert generated.validation.valid is True
    assert generated.plan.strictSourceFileCount == 3
    assert generated.plan.strictSourceSectionCount == 120
    assert len(document_files) == 3
    assert len(generated.manifest.items) == 3
    assert [len(file.content["documents"]) for file in document_files] == [40, 40, 40]
    assert actual_texts == source_paragraphs


def test_strict_source_allows_exactly_50_document_files() -> None:
    source_text = "\n\n".join([f"段落{i:04d}" for i in range(1, 2501)])
    plan = plan_pack(
        PlanPackRequest(
            theme="50ファイル資料",
            targetUser="読者",
            generationUnit="document",
            sourceText=source_text,
            materialMode="strict",
            ttsReadingMode="none",
        )
    )

    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))
    document_files = [file for file in generated.files if file.kind == "document"]

    assert generated.validation.valid is True
    assert generated.plan.strictSourceFileCount == 50
    assert len(document_files) == 50
    assert len(generated.manifest.items) == 50
    assert all(len(file.content["documents"]) == 50 for file in document_files)


def test_strict_source_stops_when_document_file_count_exceeds_50() -> None:
    source_text = "\n\n".join([f"段落{i:04d}" for i in range(1, 2502)])
    plan = plan_pack(
        PlanPackRequest(
            theme="51ファイル資料",
            targetUser="読者",
            generationUnit="document",
            sourceText=source_text,
            materialMode="strict",
            ttsReadingMode="none",
        )
    )

    try:
        generate_pack(GeneratePackRequest(plan=plan, persist=False))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("strict source generation must stop when the file count exceeds 50")

    assert plan.strictSourceFileCount == 51
    assert plan.strictSourceLimitExceeded is True
    assert "資料が大きすぎます（推定51ファイル）" in message
    assert "50ファイル以内" in message


def test_generate_request_can_override_generation_counts() -> None:
    plan = plan_pack(
        PlanPackRequest(
            theme="Override Counts",
            targetUser="Learners",
            scale="quick",
            ttsReadingMode="none",
        )
    )

    generated = generate_pack(
        GeneratePackRequest(
            plan=plan,
            persist=False,
            generationUnit="pack",
            docCount=1,
            quizCount=1,
        )
    )

    assert _kind_count([item.model_dump() for item in generated.manifest.items], "document") == 1
    assert _kind_count([item.model_dump() for item in generated.manifest.items], "quiz") == 1


def test_creator_id_resolution_request_env_and_default(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "default_creator_id", "creator_env")

    request_plan = plan_pack(
        PlanPackRequest(
            theme="Creator Request",
            targetUser="Learners",
            creatorId="creator_request",
        )
    )
    assert request_plan.creatorId == "creator_request"

    env_plan = plan_pack(PlanPackRequest(theme="Creator Env", targetUser="Learners"))
    assert env_plan.creatorId == "creator_env"

    monkeypatch.setattr(settings, "default_creator_id", "")
    default_plan = plan_pack(PlanPackRequest(theme="Creator Default", targetUser="Learners"))
    assert default_plan.creatorId == "creator_default"


def test_manifest_identity_and_storage_path_for_generated_pack(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "public_base_url", "https://cdn.convly.jp")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")

    plan = plan_pack(
        PlanPackRequest(
            theme="IT Passport",
            targetUser="Learners",
            creatorId="creator_8f3a2c9d",
            creatorDisplayName="Demo Creator",
            contentId="cnt_8f3a2c9d7e",
            slug="it-passport-basic",
            scale="quick",
            ttsReadingMode="none",
        )
    )
    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))
    manifest = generated.manifest

    assert manifest.id == "cnt_8f3a2c9d7e_manifest_r1"
    assert manifest.schemaVersion == 1
    assert manifest.revision == 1
    assert manifest.change.operation == "initial_generate"
    assert manifest.contentId == "cnt_8f3a2c9d7e"
    assert manifest.slug == "it-passport-basic"
    assert manifest.creator
    assert manifest.creator.id == "creator_8f3a2c9d"
    assert manifest.creator.displayName == "Demo Creator"
    assert manifest.versionId
    assert manifest.versionId.startswith("v")
    assert manifest.buildId == manifest.versionId.replace("v", "build_", 1)
    assert manifest.generatedAt
    assert manifest.generatedAt.endswith("+09:00")

    expected_root = "https://cdn.convly.jp/sokqa/creators/creator_8f3a2c9d/packs/cnt_8f3a2c9d7e"
    manifest_file = next(file for file in generated.files if file.kind == "manifest")
    assert manifest_file.url == f"{expected_root}/versions/{manifest.versionId}/manifest.json"
    assert all(item.url.startswith(f"{expected_root}/objects/") for item in manifest.items)


def test_old_gcs_prefix_is_normalized_to_production_storage_path(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa/packs")

    plan = CoursePlan.model_validate(
        {
            "id": "pack",
            "creatorId": "creator_demo",
            "contentId": "cnt_demo",
            "slug": "demo-pack",
            "title": "Demo",
            "description": "Demo",
            "targetUser": "Learners",
            "difficulty": "beginner",
            "documents": [],
            "quizPacks": [],
        }
    )
    metadata = build_pack_metadata(plan)

    assert metadata.storage_prefix == f"sokqa/creators/creator_demo/packs/cnt_demo/versions/{metadata.version_id}"


def test_same_content_id_generates_distinct_version_paths(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "public_base_url", "https://cdn.convly.jp")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")

    plan = plan_pack(
        PlanPackRequest(
            theme="Cache Check",
            targetUser="Learners",
            creatorId="creator_demo",
            contentId="cnt_cache_check",
            slug="cache-check",
            scale="quick",
            ttsReadingMode="none",
        )
    )

    first = generate_pack(GeneratePackRequest(plan=plan, persist=False))
    second = generate_pack(GeneratePackRequest(plan=plan, persist=False))

    assert first.manifest.contentId == second.manifest.contentId == "cnt_cache_check"
    assert first.manifest.slug == second.manifest.slug == "cache-check"
    assert first.manifest.versionId != second.manifest.versionId
    assert first.files[-1].url != second.files[-1].url


def test_same_theme_generates_unique_pack_root_ids_with_manifest_integrity() -> None:
    first_plan = plan_pack(
        PlanPackRequest(
            theme="Duplicate Theme",
            targetUser="Learners",
            generationUnit="pack",
            docCount=1,
            quizCount=1,
            ttsReadingMode="none",
        )
    )
    second_plan = plan_pack(
        PlanPackRequest(
            theme="Duplicate Theme",
            targetUser="Learners",
            generationUnit="pack",
            docCount=1,
            quizCount=1,
            ttsReadingMode="none",
        )
    )

    first = generate_pack(GeneratePackRequest(plan=first_plan, persist=False))
    second = generate_pack(GeneratePackRequest(plan=second_plan, persist=False))

    first_doc = next(file for file in first.files if file.kind == "document")
    first_quiz = next(file for file in first.files if file.kind == "quiz")
    second_doc = next(file for file in second.files if file.kind == "document")
    second_quiz = next(file for file in second.files if file.kind == "quiz")

    assert first_doc.content["id"].startswith(f"{first.manifest.contentId}_")
    assert first_quiz.content["id"].startswith(f"{first.manifest.contentId}_")
    assert second_doc.content["id"].startswith(f"{second.manifest.contentId}_")
    assert second_quiz.content["id"].startswith(f"{second.manifest.contentId}_")
    assert first_doc.content["id"] != second_doc.content["id"]
    assert first_quiz.content["id"] != second_quiz.content["id"]
    assert {item.name for item in first.manifest.items} == {file.name for file in first.files if file.kind in {"document", "quiz"}}
    assert {item.logicalId for item in first.manifest.items} == {
        first_doc.name.removesuffix(".json"),
        first_quiz.name.removesuffix(".json"),
    }


def test_standard_plan_shape() -> None:
    response = client.post(
        "/plan-pack",
        json={
            "theme": "ITパスポート試験対策",
            "targetUser": "IT初心者の社会人",
            "scale": "standard",
        },
    )
    assert response.status_code == 200
    plan = response.json()
    assert len(plan["documents"]) == 6
    assert all(35 <= document["targetSectionCount"] <= 50 for document in plan["documents"])
    assert plan["scale"] == "standard"
    assert "quality" not in plan
    _assert_partitioned_quiz_packs(plan, expected_count=2, expected_questions=30)


def test_plan_and_v2_manifest_ignore_removed_quality_field() -> None:
    plan = CoursePlan.model_validate(
        {
            "id": "quality_removed_pack",
            "title": "Legacy Pack",
            "description": "Legacy course plan",
            "targetUser": "Legacy learners",
            "difficulty": "beginner",
            "quality": "coverage",
            "documents": [],
            "quizPacks": [],
        }
    )
    assert plan.scale is None
    assert not hasattr(plan, "quality")

    manifest = PackManifestV2.model_validate(
        {
            "id": "quality_removed_manifest",
            "title": "Legacy Pack",
            "contentId": "cnt_quality_removed",
            "creator": {"id": "creator_default", "displayName": None},
            "revision": 1,
            "versionId": "v20260613_120000",
            "buildId": "build_20260613_120000",
            "generatedAt": "2026-06-13T12:00:00+09:00",
            "change": {"operation": "initial_generate"},
            "quality": "coverage",
            "items": [],
        }
    )
    assert manifest.scale is None
    assert not hasattr(manifest, "quality")


def test_auto_scale_is_recorded_in_plan_and_manifest_without_quality() -> None:
    response = client.post(
        "/plan-pack",
        json={
            "theme": "ITパスポート試験対策",
            "targetUser": "IT初心者の社会人",
            "scale": "auto",
        },
    )
    assert response.status_code == 200
    plan = response.json()
    assert plan["scale"] == "auto"
    assert "quality" not in plan
    assert len(plan["documents"]) >= 1
    assert all(30 <= document["targetSectionCount"] <= 50 for document in plan["documents"])
    _assert_partitioned_quiz_packs(plan, expected_count=4, expected_questions=30)

    generate_response = client.post(
        "/generate-pack",
        json={
            "plan": plan,
            "ttsReadingMode": "none",
            "persist": False,
        },
    )
    assert generate_response.status_code == 200
    generated = generate_response.json()
    assert generated["plan"]["scale"] == "auto"
    assert "quality" not in generated["plan"]
    assert generated["manifest"]["scale"] == "auto"
    assert "quality" not in generated["manifest"]

    manifest_file = next(file for file in generated["files"] if file["kind"] == "manifest")
    assert manifest_file["content"]["scale"] == "auto"
    assert "quality" not in manifest_file["content"]


def test_auto_quiz_pack_count_for_six_or_fewer_documents() -> None:
    response = client.post(
        "/plan-pack",
        json={
            "theme": "ITパスポート試験対策",
            "targetUser": "IT初心者の社会人",
            "scale": "auto",
            "documentCount": 6,
        },
    )
    assert response.status_code == 200
    plan = response.json()
    _assert_partitioned_quiz_packs(plan, expected_count=3, expected_questions=30)
    titles = [pack["title"] for pack in plan["quizPacks"]]
    assert titles[0].startswith("ITパスポート試験対策 理解チェック1（1〜3章")
    assert titles[1].startswith("ITパスポート試験対策 理解チェック2（4〜6章")
    assert titles[2] == "ITパスポート試験対策 総合確認（1〜6章: 全範囲）"


def test_auto_quiz_pack_count_for_seven_to_ten_documents() -> None:
    response = client.post(
        "/plan-pack",
        json={
            "theme": "ITパスポート試験対策",
            "targetUser": "IT初心者の社会人",
            "scale": "auto",
            "documentCount": 7,
        },
    )
    assert response.status_code == 200
    plan = response.json()
    _assert_partitioned_quiz_packs(plan, expected_count=3, expected_questions=30)
    titles = [pack["title"] for pack in plan["quizPacks"]]
    assert titles[0].startswith("ITパスポート試験対策 理解チェック1（1〜4章")
    assert titles[1].startswith("ITパスポート試験対策 理解チェック2（5〜7章")
    assert titles[2] == "ITパスポート試験対策 総合確認（1〜7章: 全範囲）"


def test_auto_quiz_pack_count_for_eleven_or_more_documents() -> None:
    response = client.post(
        "/plan-pack",
        json={
            "theme": "ITパスポート試験対策",
            "targetUser": "IT初心者の社会人",
            "scale": "auto",
            "documentCount": 11,
        },
    )
    assert response.status_code == 200
    plan = response.json()
    _assert_partitioned_quiz_packs(plan, expected_count=5, expected_questions=30)
    titles = [pack["title"] for pack in plan["quizPacks"]]
    assert titles[0].startswith("ITパスポート試験対策 理解チェック1（1〜3章")
    assert titles[1].startswith("ITパスポート試験対策 理解チェック2（4〜6章")
    assert titles[2].startswith("ITパスポート試験対策 理解チェック3（7〜9章")
    assert titles[3].startswith("ITパスポート試験対策 理解チェック4（10〜11章")
    assert titles[4] == "ITパスポート試験対策 総合確認（1〜11章: 全範囲）"


def test_jobs_prompts_download_returns_zip_with_prompt_records() -> None:
    import io
    import zipfile

    """APIエンドポイントの smoke test: 生成結果から prompts が取り出せること"""
    from app.schemas.sokqa import DebugPromptRecordSchema
    from app.services.job_store import save_job

    record = DebugPromptRecordSchema(
        prompt_type="quiz",
        target="quiz_range_01",
        model="gemini-2.5-pro",
        prompt="Create one Sokqa quiz JSON.",
        generated_at="2026-07-06T01:23:45+00:00",
        characters=28,
        quiz_title="前半の理解チェック",
    )
    # generate_pack は prompts を未注入状態で返すため、静的に組み立てた job で API を検証する
    # PackManifestV2 のスキーマは複雑で再現コストが高いため、job_store -> API のみを単体テストする
    job_id = "job-debug-zip-01"
    # モック生成結果を直接構築する代わりに、既存 generate_pack の job を流用する
    request = GeneratePackRequest(
        plan=plan_pack(PlanPackRequest(theme="Debug Download", targetUser="Learner", scale="quick", ttsReadingMode="none")),
        persist=False,
    )
    generated = generate_pack(request)
    generated = generated.model_copy(update={"jobId": job_id, "prompts": [record]})
    save_job(generated)

    response = client.get(f"/jobs/{job_id}/prompts/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert f"{job_id}_prompts.zip" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert names == ["quiz_quiz_range_01_default_0_prompt.txt"]
    body = archive.read(names[0]).decode("utf-8")
    assert "Prompt Type : quiz" in body
    assert "Target      : quiz_range_01" in body
    assert "Model       : gemini-2.5-pro" in body
    assert "Quiz Title  : 前半の理解チェック" in body
    assert "Create one Sokqa quiz JSON." in body


def test_jobs_prompts_download_404_when_no_prompts() -> None:
    from app.services.job_store import save_job

    request = GeneratePackRequest(
        plan=plan_pack(PlanPackRequest(theme="Empty Prompts", targetUser="Learner", scale="quick", ttsReadingMode="none")),
        persist=False,
    )
    generated = generate_pack(request)
    generated = generated.model_copy(update={"jobId": "job-debug-empty"})
    save_job(generated)

    response = client.get("/jobs/job-debug-empty/prompts/download")
    assert response.status_code == 404


# ── prompt filename uniqueness with pack file_name ──────────────

def test_prompt_filename_different_packs_produce_unique_names() -> None:
    """異なる2つの document pack で file_name が異なるとファイル名も衝突しない。"""
    from app.routes.jobs import _prompt_filename
    from app.schemas.sokqa import DebugPromptRecordSchema

    pack1 = DebugPromptRecordSchema(
        prompt_type="tts_batch_doc",
        target="doc-1",
        model="dummy",
        prompt="dummy",
        phase="tts_optimizer",
        run_index=0,
        file_name="cnt_001_doc_01.json",
    )
    pack2 = DebugPromptRecordSchema(
        prompt_type="tts_batch_doc",
        target="doc-1",
        model="dummy",
        prompt="dummy",
        phase="tts_optimizer",
        run_index=0,
        file_name="cnt_002_doc_02.json",
    )
    name1 = _prompt_filename(pack1)
    name2 = _prompt_filename(pack2)
    assert name1 != name2
    assert "cnt_001_doc_01" in name1
    assert "cnt_002_doc_02" in name2
    assert "doc-1" in name1
    assert "doc-1" in name2


def test_prompt_filename_same_pack_chunks_differ_by_run_index() -> None:
    """同一 pack で chunk 分割時は run_index で区別されることを維持。"""
    from app.routes.jobs import _prompt_filename
    from app.schemas.sokqa import DebugPromptRecordSchema

    chunk0 = DebugPromptRecordSchema(
        prompt_type="tts_batch_doc",
        target="doc-1",
        model="dummy",
        prompt="dummy",
        phase="tts_optimizer",
        run_index=0,
        file_name="cnt_001_doc_01.json",
    )
    chunk1 = DebugPromptRecordSchema(
        prompt_type="tts_batch_doc",
        target="doc-2",
        model="dummy",
        prompt="dummy",
        phase="tts_optimizer",
        run_index=1,
        file_name="cnt_001_doc_01.json",
    )
    assert _prompt_filename(chunk0) != _prompt_filename(chunk1)
    assert _prompt_filename(chunk0).endswith("_0_prompt.txt")
    assert _prompt_filename(chunk1).endswith("_1_prompt.txt")


def test_prompt_filename_no_file_name_falls_back_to_legacy_format() -> None:
    """file_name が None の場合は従来のフォーマットにフォールバックする。"""
    from app.routes.jobs import _prompt_filename
    from app.schemas.sokqa import DebugPromptRecordSchema

    record = DebugPromptRecordSchema(
        prompt_type="quiz",
        target="quiz_range_01",
        model="dummy",
        prompt="dummy",
        phase="generator",
        run_index=0,
        file_name=None,
    )
    filename = _prompt_filename(record)
    assert filename == "quiz_quiz_range_01_generator_0_prompt.txt"


def test_prompt_filename_quiz_packs_produce_unique_names() -> None:
    """異なる quiz pack で file_name が異なるとファイル名が衝突しない。"""
    from app.routes.jobs import _prompt_filename
    from app.schemas.sokqa import DebugPromptRecordSchema

    q1 = DebugPromptRecordSchema(
        prompt_type="tts_batch_quiz",
        target="q-1",
        model="dummy",
        prompt="dummy",
        phase="tts_optimizer",
        run_index=0,
        file_name="cnt_001_quiz_01.json",
    )
    q2 = DebugPromptRecordSchema(
        prompt_type="tts_batch_quiz",
        target="q-1",
        model="dummy",
        prompt="dummy",
        phase="tts_optimizer",
        run_index=0,
        file_name="cnt_002_quiz_02.json",
    )
    assert _prompt_filename(q1) != _prompt_filename(q2)


def test_safe_filename_token_strips_special_chars() -> None:
    from app.routes.jobs import _safe_filename_token

    assert _safe_filename_token("cnt_001_doc_01.json") == "cnt_001_doc_01.json"
    assert _safe_filename_token("path/to/file.json") == "path_to_file.json"
    assert _safe_filename_token("file:name?.txt") == "file_name_.txt"
    assert _safe_filename_token("") == "unknown"
    assert _safe_filename_token("  ") == "unknown"
    assert _safe_filename_token("__valid__") == "valid"
