from fastapi.testclient import TestClient

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
    assert 1 <= document_count <= 4
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
    assert 6 <= len(plan["documents"]) <= 12
    assert len(plan["quizPacks"]) == 3
    assert [pack["questionCount"] for pack in plan["quizPacks"]] == [30, 30, 30]
