from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_tts_revision_bumps_manifest_version() -> None:
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
    assert revised["manifest"]["version"] == "1.0.1"
    assert revised["validation"]["valid"] is True
