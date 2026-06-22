from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_generation_logs_include_source() -> None:
    plan_response = client.post(
        "/plan-pack",
        json={
            "theme": "ITパスポート試験対策",
            "targetUser": "IT初心者の社会人",
            "scale": "quick",
        },
    )
    response = client.post(
        "/generate-pack",
        json={
            "plan": plan_response.json(),
            "persist": False,
        },
    )
    logs = response.json()["logs"]
    assert any("Document doc_01" in log for log in logs)
    assert any("Quiz quiz_single_01" in log for log in logs)
