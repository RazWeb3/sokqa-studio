from fastapi.testclient import TestClient

from app.config import get_settings
from app.schemas.pack_v2 import PackManifestV2
from app.schemas.request import GeneratePackRequest, PlanPackRequest
from app.schemas.sokqa import CoursePlan
from app.services.pack_agent import generate_pack, plan_pack
from app.services.pack_metadata import build_pack_metadata
from main import app


client = TestClient(app)


def _kind_count(items: list[dict], kind: str) -> int:
    return sum(1 for item in items if item["kind"] == kind)


def _assert_partitioned_quiz_packs(plan: dict, expected_count: int, expected_questions: int) -> None:
    document_ids = [document["id"] for document in plan["documents"]]
    quiz_packs = plan["quizPacks"]
    range_packs = quiz_packs[:-1]
    integrated_pack = quiz_packs[-1]

    assert len(quiz_packs) == expected_count
    assert [pack["questionCount"] for pack in quiz_packs] == [expected_questions] * expected_count
    assert "総合確認" in integrated_pack["title"]
    assert integrated_pack["purpose"] == "integrated_review"
    assert integrated_pack["sourceDocumentIds"] == document_ids

    range_ids = []
    for pack in range_packs:
        assert pack["sourceDocumentIds"]
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
    assert 3 <= document_count <= 5
    assert all(30 <= document["targetSectionCount"] <= 50 for document in plan["documents"])
    assert plan["scale"] == "quick"
    assert "quality" not in plan
    _assert_partitioned_quiz_packs(plan, expected_count=3, expected_questions=20)

    generate_response = client.post(
        "/generate-pack",
        json={
            "plan": plan,
            "persist": False,
        },
    )
    assert generate_response.status_code == 200
    generated = generate_response.json()
    assert generated["validation"]["valid"] is True
    assert generated["manifest"]["scale"] == "quick"
    assert "quality" not in generated["manifest"]
    assert len(generated["manifest"]["items"]) == document_count + quiz_count
    assert _kind_count(generated["manifest"]["items"], "document") == document_count
    assert _kind_count(generated["manifest"]["items"], "quiz") == quiz_count
    assert len(generated["files"]) == len(generated["manifest"]["items"]) + 1


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
        )
    )

    first = generate_pack(GeneratePackRequest(plan=plan, persist=False))
    second = generate_pack(GeneratePackRequest(plan=plan, persist=False))

    assert first.manifest.contentId == second.manifest.contentId == "cnt_cache_check"
    assert first.manifest.slug == second.manifest.slug == "cache-check"
    assert first.manifest.versionId != second.manifest.versionId
    assert first.files[-1].url != second.files[-1].url


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
    assert 6 <= len(plan["documents"]) <= 10
    assert all(30 <= document["targetSectionCount"] <= 50 for document in plan["documents"])
    assert plan["scale"] == "standard"
    assert "quality" not in plan
    _assert_partitioned_quiz_packs(plan, expected_count=3, expected_questions=30)


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
    _assert_partitioned_quiz_packs(plan, expected_count=4, expected_questions=30)
    titles = [pack["title"] for pack in plan["quizPacks"]]
    assert titles[0].startswith("ITパスポート試験対策 理解チェック1（1〜3章")
    assert titles[1].startswith("ITパスポート試験対策 理解チェック2（4〜5章")
    assert titles[2].startswith("ITパスポート試験対策 理解チェック3（6〜7章")
    assert titles[3] == "ITパスポート試験対策 総合確認（1〜7章: 全範囲）"


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
