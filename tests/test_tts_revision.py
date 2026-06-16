from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_tts_revision_creates_new_manifest_revision() -> None:
    plan_response = client.post(
        "/plan-pack",
        json={
            "theme": "ITパスポート試験対策",
            "targetUser": "IT初心者の社会人",
            "scale": "quick",
        },
    )
    generated_response = client.post(
        "/generate-pack",
        json={
            "plan": plan_response.json(),
            "persist": False,
        },
    )
    generated = generated_response.json()

    revised_response = client.post(
        "/debug/revise-tts",
        json={
            "jobId": generated["jobId"],
            "persist": False,
            "ttsRules": [
                {
                    "source": "日本橋",
                    "reading": "にほんばし",
                    "note": "地名として読む場合",
                }
            ],
        },
    )

    assert revised_response.status_code == 200
    revised = revised_response.json()
    assert revised["manifest"]["schemaVersion"] == 1
    assert revised["manifest"]["revision"] == generated["manifest"]["revision"] + 1
    assert revised["manifest"]["versionId"] != generated["manifest"]["versionId"]
    assert revised["manifest"]["sourceVersionId"] == generated["manifest"]["versionId"]
    assert revised["manifest"]["change"]["operation"] == "tts_fix"
    assert revised["validation"]["valid"] is True
