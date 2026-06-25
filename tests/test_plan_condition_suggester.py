from fastapi.testclient import TestClient
import pytest

from main import app
from app.config import get_settings
from app.schemas.request import PlanSuggestConditionsRequest
from app.services import plan_condition_suggester as suggester
from app.services.gemini_client import GeminiClient


client = TestClient(app)


def test_mock_provider_does_not_return_fallback_suggestions(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "gemini_provider", "mock")

    with pytest.raises(RuntimeError, match="AI提案の取得に失敗しました"):
        suggester.suggest_conditions(
            PlanSuggestConditionsRequest(
                theme="ITパスポート",
                targetUser="社会人",
                difficulty="beginner",
                language="ja",
            )
        )


def test_gemini_response_is_used_without_fallback_completion(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "gemini_provider", "gemini")

    def fake_generate_json(self, *args, **kwargs):
        return {
            "suggestions": [
                {
                    "id": "plain_examples",
                    "title": "例を短く",
                    "text": "各説明のあとに短い例を入れてください。",
                    "reason": "社会人が理解しやすいため",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    suggestions = suggester.suggest_conditions(
        PlanSuggestConditionsRequest(theme="英語学習", targetUser="社会人")
    )

    assert [item.id for item in suggestions] == ["plain_examples"]


def test_invalid_json_shape_is_ignored_by_parser() -> None:
    suggestions = suggester._suggestions_from_response(
        {
            "suggestions": [
                {"id": "ok", "title": "OK", "text": "条件を追加してください。", "reason": "理由"},
                {"id": "duplicate", "title": "重複", "text": "条件を追加してください。", "reason": "理由"},
                {"id": "missing_text", "title": "欠落", "reason": "理由"},
                "broken",
            ]
        }
    )

    assert [item.id for item in suggestions] == ["ok"]


def test_tts_related_suggestions_are_filtered() -> None:
    suggestions = suggester._suggestions_from_response(
        {
            "suggestions": [
                {
                    "id": "abbreviation_reading",
                    "title": "略語読み補正",
                    "text": "IT、AI、APIなどの英略語は読み方も補足してください。",
                    "reason": "読み上げでつまずきやすいため",
                },
                {
                    "id": "practical_examples",
                    "title": "実務例追加",
                    "text": "各章に身近な実務シナリオを1つ以上入れてください。",
                    "reason": "抽象概念を利用場面と結びつけるため",
                },
            ]
        }
    )

    assert [item.id for item in suggestions] == ["practical_examples"]


def test_empty_gemini_response_raises_without_fallback(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "gemini_provider", "gemini")
    monkeypatch.setattr(GeminiClient, "generate_json", lambda self, *args, **kwargs: {"suggestions": []})

    with pytest.raises(RuntimeError, match="AI提案の取得に失敗しました"):
        suggester.suggest_conditions(
            PlanSuggestConditionsRequest(theme="歌詞の読解", targetUser="高校生")
        )


def test_prompt_excludes_tts_responsibilities() -> None:
    prompt = suggester._suggestion_prompt(
        PlanSuggestConditionsRequest(theme="ITパスポート", targetUser="社会人")
    )

    assert "教材内容・説明方法・出題方針のみ" in prompt
    assert "読みパターン提案機能の責務" in prompt
    for banned in ["TTS", "読み上げ", "発音", "読み方", "略語読み", "読み補正"]:
        assert banned in prompt


def test_prompt_requires_display_language_for_all_suggestion_fields() -> None:
    prompt = suggester._suggestion_prompt(
        PlanSuggestConditionsRequest(
            theme="日本語会話",
            targetUser="社会人",
            language="id",
            displayLanguage="ja",
        )
    )

    assert "- packLanguage: id" in prompt
    assert "- displayLanguage: ja" in prompt
    assert "提案タイトル・提案理由・提案内容は、必ず displayLanguage で出力してください。" in prompt
    assert "packLanguage や learningLanguage に引っ張られて出力言語を変えてはいけません。" in prompt


def test_plan_suggest_conditions_endpoint_returns_503_without_gemini(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "gemini_provider", "mock")

    response = client.post(
        "/api/plan-suggest-conditions",
        json={
            "theme": "ITパスポート",
            "targetUser": "社会人",
            "difficulty": "beginner",
            "language": "ja",
            "customInstructions": "",
        },
    )

    assert response.status_code == 503
    assert "AI提案の取得に失敗しました" in response.json()["detail"]


def test_plan_suggest_conditions_endpoint_returns_gemini_suggestions(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "gemini_provider", "gemini")

    def fake_generate_json(self, *args, **kwargs):
        return {
            "suggestions": [
                {
                    "id": "plain_terms",
                    "title": "専門用語を減らす",
                    "text": "社会人向けに専門用語を減らし、必要な場合は短く言い換えてください。",
                    "reason": "学習者が説明を追いやすくするため",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post(
        "/api/plan-suggest-conditions",
        json={
            "theme": "ITパスポート",
            "targetUser": "社会人",
            "difficulty": "beginner",
            "language": "ja",
            "customInstructions": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["suggestions"][0]["id"] == "plain_terms"


def test_source_material_injects_strict_japanese_notation_suggestion(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "gemini_provider", "gemini")

    def fake_generate_json(self, *args, **kwargs):
        return {
            "suggestions": [
                {
                    "id": "plain_terms",
                    "title": "専門用語を減らす",
                    "text": "社会人向けに専門用語を減らし、必要な場合は短く言い換えてください。",
                    "reason": "学習者が説明を追いやすくするため",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post(
        "/api/plan-suggest-conditions",
        json={
            "theme": "歌詞の読解",
            "targetUser": "高校生",
            "difficulty": "beginner",
            "language": "ja",
            "customInstructions": "",
            "hasSourceMaterial": True,
        },
    )

    assert response.status_code == 200
    suggestions = response.json()["suggestions"]
    assert suggestions[0]["id"] == "strict_japanese_notation_reading"
    assert any(item["id"] == "plain_terms" for item in suggestions)


def test_source_material_fixed_suggestion_uses_display_language_not_pack_language(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "gemini_provider", "gemini")

    def fake_generate_json(self, *args, **kwargs):
        return {
            "suggestions": [
                {
                    "id": "source_based_examples",
                    "title": "例文を増やす",
                    "text": "参考資料に沿った例文を増やしてください。",
                    "reason": "資料との対応が追いやすくなるため",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    suggestions = suggester.suggest_conditions(
        PlanSuggestConditionsRequest(
            theme="歌詞の読解",
            targetUser="高校生",
            difficulty="beginner",
            language="id",
            displayLanguage="ja",
            hasSourceMaterial=True,
        )
    )

    assert suggestions[0].id == "strict_japanese_notation_reading"
    assert suggestions[0].title == "日本語表記・読みの厳密確認"
    assert suggestions[0].reason == "推測による表記揺れ・誤ローマ字を防ぎ、歌詞など固有表現の誤りを減らすため"
