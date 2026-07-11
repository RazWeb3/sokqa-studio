import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.schemas.request import GeneratePackRequest, PlanPackRequest
from app.schemas.sokqa import SokqaDocumentItem, SokqaDocumentPack
from app.services.document_generator import generate_document_pack
from app.services.gemini_client import GeminiClient
from app.services.llm_json import LlmJsonParseError, parse_llm_json_or_raise
from app.services.pack_agent import generate_pack, plan_pack
from app.services.quiz_generator import generate_quiz_pack
from main import app


client = TestClient(app)


def _force_gemini_failure(monkeypatch) -> None:
    def fail_generate_json(self, *args, **kwargs):
        raise RuntimeError("forced gemini failure")

    monkeypatch.setattr(GeminiClient, "generate_json", fail_generate_json)


def test_gemini_provider_generation_failure_does_not_save_mock_content(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")
    _force_gemini_failure(monkeypatch)

    plan = plan_pack(
        PlanPackRequest(
            theme="ITパスポート試験対策",
            targetUser="IT初心者の社会人",
            scale="quick",
            ttsReadingMode="rule",
        )
    )

    with pytest.raises(RuntimeError, match="保存していません"):
        generate_pack(GeneratePackRequest(plan=plan, persist=False))


def test_gemini_provider_quiz_generation_failure_does_not_save_mock_content(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")
    _force_gemini_failure(monkeypatch)

    plan = plan_pack(
        PlanPackRequest(
            theme="ITパスポート試験対策",
            targetUser="IT初心者の社会人",
            scale="quick",
            ttsReadingMode="rule",
        )
    )
    source_document = SokqaDocumentPack(
        id="doc_pack",
        title="基礎",
        language=plan.language,
        documents=[SokqaDocumentItem(id="doc-1", text="二要素認証を確認します。")],
    )

    with pytest.raises(RuntimeError, match="保存していません"):
        generate_quiz_pack(plan, plan.quizPacks[0], [source_document])


def test_generate_route_returns_clear_generation_failure_message(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")
    _force_gemini_failure(monkeypatch)

    plan = plan_pack(
        PlanPackRequest(
            theme="ITパスポート試験対策",
            targetUser="IT初心者の社会人",
            scale="quick",
            ttsReadingMode="rule",
        )
    )

    response = client.post("/generate-pack", json={"plan": plan.model_dump(mode="json"), "persist": False})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "ドキュメント生成に失敗しました" in detail["message"]
    assert "保存していません" in detail["message"]
    assert "少し時間を置いて再生成" in detail["hint"]


def test_document_parse_failure_saves_raw_and_does_not_persist_pack(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    plan = plan_pack(
        PlanPackRequest(
            theme="歌詞教材",
            targetUser="学習者",
            scale="quick",
            docCount=1,
            quizCount=0,
            customInstructions="引用は短くする",
            sourceText="la la\n" * 40,
            ttsReadingMode="none",
        )
    )

    def broken_generate_json(self, _prompt, model=None, temperature=None, parse_context=None):
        if parse_context is not None:
            parse_context.model = parse_context.model or model
        parsed, _method = parse_llm_json_or_raise(
            '{"documents": [{"text": "missing comma" "broken"}]}',
            parse_context,
        )
        return parsed

    monkeypatch.setattr(GeminiClient, "generate_json", broken_generate_json)
    monkeypatch.setattr("app.services.pack_agent._persist_initial_revision", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not persist failed generation")))

    with pytest.raises(RuntimeError, match="保存していません"):
        generate_pack(GeneratePackRequest(plan=plan, persist=True))

    failed_dir = tmp_path / "tmp" / "failed_generations"
    assert list(failed_dir.glob("*_raw.txt"))
    assert not (tmp_path / "generated").exists()


def test_document_generation_retries_after_parse_error_and_succeeds(monkeypatch) -> None:
    sleep_calls = []
    monkeypatch.setattr("app.services.document_generator.time.sleep", sleep_calls.append)

    plan = plan_pack(
        PlanPackRequest(
            theme="JSON再試行",
            targetUser="学習者",
            scale="quick",
            docCount=1,
            quizCount=0,
            ttsReadingMode="none",
        )
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")
    attempts = {"count": 0}

    def flaky_generate_json(self, _prompt, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise LlmJsonParseError("broken json", attempts=[{"method": "direct", "error": "broken json"}])
        return {"documents": [{"text": "再試行後に成功した本文です。"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", flaky_generate_json)

    pack = generate_document_pack(plan, plan.documents[0])

    assert attempts["count"] == 2
    assert sleep_calls == [0.5]
    assert pack.documents[0].text == "再試行後に成功した本文です。"


def test_document_generation_retries_twice_then_succeeds_on_third_attempt(monkeypatch) -> None:
    sleep_calls = []
    monkeypatch.setattr("app.services.document_generator.time.sleep", sleep_calls.append)
    plan = plan_pack(
        PlanPackRequest(theme="JSON再試行3回目", targetUser="学習者", scale="quick", docCount=1, quizCount=0, ttsReadingMode="none")
    )
    monkeypatch.setattr(get_settings(), "gemini_provider", "gemini")
    calls = {"count": 0}

    def generate_json(self, _prompt, **_kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise LlmJsonParseError("broken json", attempts=[{"method": "direct", "error": "broken json"}])
        return {"documents": [{"text": "3回目に成功した本文です。"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", generate_json)

    pack = generate_document_pack(plan, plan.documents[0])

    assert calls["count"] == 3
    assert sleep_calls == [0.5, 1.5]
    assert pack.documents[0].text == "3回目に成功した本文です。"


def test_document_generation_raises_after_retry_exhaustion(monkeypatch) -> None:
    sleep_calls = []
    monkeypatch.setattr("app.services.document_generator.time.sleep", sleep_calls.append)

    plan = plan_pack(
        PlanPackRequest(
            theme="JSON再試行失敗",
            targetUser="学習者",
            scale="quick",
            docCount=1,
            quizCount=0,
            ttsReadingMode="none",
        )
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")
    attempts = {"count": 0}

    def broken_generate_json(self, _prompt, **_kwargs):
        attempts["count"] += 1
        raise LlmJsonParseError("broken json", attempts=[{"method": "direct", "error": "broken json"}])

    monkeypatch.setattr(GeminiClient, "generate_json", broken_generate_json)

    with pytest.raises(RuntimeError, match="ドキュメント生成に失敗しました"):
        generate_document_pack(plan, plan.documents[0])

    assert attempts["count"] == 3
    assert sleep_calls == [0.5, 1.5]


def test_quiz_generation_retries_after_parse_error_and_succeeds(monkeypatch) -> None:
    sleep_calls = []
    monkeypatch.setattr("app.services.quiz_generator.time.sleep", sleep_calls.append)

    plan = plan_pack(
        PlanPackRequest(
            theme="JSON再試行",
            targetUser="学習者",
            scale="quick",
            docCount=1,
            quizCount=1,
            questionCount=1,
            ttsReadingMode="none",
        )
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")
    source_document = SokqaDocumentPack(
        id="doc_pack",
        title="基礎",
        language=plan.language,
        documents=[SokqaDocumentItem(id="doc-1", text="二要素認証を確認します。")],
    )
    attempts = {"count": 0}

    def flaky_generate_json(self, _prompt, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise LlmJsonParseError("broken json", attempts=[{"method": "direct", "error": "broken json"}])
        return {
            "questions": [
                {
                    "question": "最も適切な説明はどれですか？",
                    "choices": ["正答", "誤答1", "誤答2", "誤答3"],
                    "answerIndex": 0,
                    "explanation": "正答は本文の説明と一致します。",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", flaky_generate_json)

    pack = generate_quiz_pack(plan, plan.quizPacks[0], [source_document])

    assert attempts["count"] == 2
    assert sleep_calls == [0.5]
    assert pack.questions[0].question == "最も適切な説明はどれですか？"


def test_quiz_generation_raises_after_retry_exhaustion(monkeypatch) -> None:
    sleep_calls = []
    monkeypatch.setattr("app.services.quiz_generator.time.sleep", sleep_calls.append)

    plan = plan_pack(
        PlanPackRequest(
            theme="JSON再試行失敗",
            targetUser="学習者",
            scale="quick",
            docCount=1,
            quizCount=1,
            ttsReadingMode="none",
        )
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")
    source_document = SokqaDocumentPack(
        id="doc_pack",
        title="基礎",
        language=plan.language,
        documents=[SokqaDocumentItem(id="doc-1", text="二要素認証を確認します。")],
    )
    attempts = {"count": 0}

    def broken_generate_json(self, _prompt, **_kwargs):
        attempts["count"] += 1
        raise RuntimeError("temporary backend failure")

    monkeypatch.setattr(GeminiClient, "generate_json", broken_generate_json)

    with pytest.raises(RuntimeError, match="クイズ生成に失敗しました"):
        generate_quiz_pack(plan, plan.quizPacks[0], [source_document])

    assert attempts["count"] == 3
    assert sleep_calls == [0.5, 1.5]
