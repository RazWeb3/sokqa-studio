from fastapi.testclient import TestClient

from app.schemas.sokqa import CoursePlan, PackManifest
from main import app


client = TestClient(app)


def _kind_count(items: list[dict], kind: str) -> int:
    return sum(1 for item in items if item["kind"] == kind)


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
    assert len(plan["quizPacks"]) == 1
    assert plan["quizPacks"][0]["questionCount"] == 10

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
    assert len(plan["quizPacks"]) == 3
    assert [pack["questionCount"] for pack in plan["quizPacks"]] == [30, 30, 30]


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
    assert len(plan["quizPacks"]) == 3
    assert [pack["questionCount"] for pack in plan["quizPacks"]] == [30, 30, 30]

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
