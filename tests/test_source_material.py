from fastapi.testclient import TestClient

from app.config import get_settings
from main import app


client = TestClient(app)


def _generate_with_source(source_text: str | None, source_mode: str | None = None) -> dict:
    payload = {
        "theme": "社内手順",
        "targetUser": "新入社員",
        "scale": "quick",
        "documentCount": 1,
        "sectionsPerDocument": 1,
        "includeTts": False,
    }
    if source_text is not None:
        payload["sourceText"] = source_text
    if source_mode is not None:
        payload["sourceMode"] = source_mode

    plan_response = client.post("/plan-pack", json=payload)
    assert plan_response.status_code == 200
    plan = plan_response.json()
    generate_response = client.post(
        "/generate-pack",
        json={
            "plan": plan,
            "persist": False,
        },
    )
    assert generate_response.status_code == 200
    return generate_response.json()


def test_source_document_only_uses_reference_without_mock_supplement(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    generated = _generate_with_source(
        "資料固有語アルファは申請前に確認する社内用語です",
        "document_only",
    )

    document_text = generated["files"][0]["content"]["documents"][0]["text"]
    assert "資料固有語アルファ" in document_text
    assert "補足して整理します" not in document_text


def test_source_document_reference_adds_supplement_while_preserving_reference(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    generated = _generate_with_source(
        "資料固有語ベータはオンボーディングで最初に扱う用語です",
        "document_reference",
    )

    document_text = generated["files"][0]["content"]["documents"][0]["text"]
    assert "資料固有語ベータ" in document_text
    assert "補足して整理します" in document_text


def test_source_mode_defaults_to_document_reference_when_source_text_exists(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan_response = client.post(
        "/plan-pack",
        json={
            "theme": "社内手順",
            "targetUser": "新入社員",
            "scale": "quick",
            "documentCount": 1,
            "sectionsPerDocument": 1,
            "includeTts": False,
            "sourceText": "資料固有語ガンマを確認します",
        },
    )

    assert plan_response.status_code == 200
    plan = plan_response.json()
    assert plan["sourceMode"] == "document_reference"


def test_blank_source_text_falls_back_to_theme_generation(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    without_source = _generate_with_source(None)
    with_blank_source = _generate_with_source("   ")

    assert with_blank_source["plan"]["sourceText"] is None
    assert with_blank_source["plan"]["sourceMode"] is None
    assert with_blank_source["files"][0]["content"] == without_source["files"][0]["content"]


def test_source_fields_do_not_leak_into_sokqa_outputs(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    generated = _generate_with_source(
        "資料固有語デルタを教材化します",
        "document_reference",
    )

    for file in generated["files"]:
        content = file["content"]
        assert "sourceText" not in content
        assert "sourceMode" not in content
    assert "sourceText" not in generated["manifest"]
    assert "sourceMode" not in generated["manifest"]


def test_generate_pack_request_source_text_can_override_plan_source(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan_response = client.post(
        "/plan-pack",
        json={
            "theme": "社内手順",
            "targetUser": "新入社員",
            "scale": "quick",
            "documentCount": 1,
            "sectionsPerDocument": 1,
            "includeTts": False,
        },
    )
    assert plan_response.status_code == 200

    generate_response = client.post(
        "/generate-pack",
        json={
            "plan": plan_response.json(),
            "persist": False,
            "sourceText": "資料固有語イプシロンを追加資料として扱います",
            "sourceMode": "document_only",
        },
    )

    assert generate_response.status_code == 200
    generated = generate_response.json()
    document_text = generated["files"][0]["content"]["documents"][0]["text"]
    assert "資料固有語イプシロン" in document_text
    assert "補足して整理します" not in document_text
