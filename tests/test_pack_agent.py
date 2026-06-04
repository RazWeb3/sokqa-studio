from fastapi.testclient import TestClient

from app.schemas.sokqa import CoursePlan, PackManifest
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
    assert integrated_pack["title"] == "総合・応用クイズ"
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


def test_legacy_plan_and_manifest_with_or_without_quality_remain_valid() -> None:
    plan = CoursePlan.model_validate(
        {
            "id": "legacy_pack",
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

    manifest = PackManifest.model_validate(
        {
            "id": "legacy_manifest",
            "title": "Legacy Pack",
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
    assert [pack["title"] for pack in plan["quizPacks"]] == ["前半の理解チェック", "後半の理解チェック", "総合・応用クイズ"]


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
    assert [pack["title"] for pack in plan["quizPacks"]] == ["前半の理解チェック", "中盤の理解チェック", "後半の理解チェック", "総合・応用クイズ"]


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
    assert [pack["title"] for pack in plan["quizPacks"]] == [
        "序盤の理解チェック",
        "前半の理解チェック",
        "後半の理解チェック",
        "終盤の理解チェック",
        "総合・応用クイズ",
    ]
