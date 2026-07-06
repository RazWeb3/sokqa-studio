import json

import pytest

from app.services.gemini_client import (
    DebugPromptRecord,
    parse_json_response,
    pop_debug_prompts,
    record_debug_prompt,
)
from app.services.llm_json import LlmJsonParseContext, LlmJsonParseError, parse_llm_json_or_raise


def test_parse_plain_json_response() -> None:
    assert parse_json_response('{"ok": true}') == {"ok": True}


def test_parse_fenced_json_response() -> None:
    assert parse_json_response('```json\n{"ok": true}\n```') == {"ok": True}


def test_parse_json_with_surrounding_text() -> None:
    parsed, method = parse_llm_json_or_raise('Here is the result:\n{"ok": true}\nThanks.')

    assert parsed == {"ok": True}
    assert method == "object_extract"


def test_parse_repairs_trailing_comma() -> None:
    parsed, method = parse_llm_json_or_raise('```json\n{"items": [1, 2,],}\n```')

    assert parsed == {"items": [1, 2]}
    assert method == "repaired"


def test_unrepairable_json_saves_raw_response(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    context = LlmJsonParseContext(
        generation_unit="doc",
        model="gemini-test",
        theme="歌詞教材",
        doc_id="doc_01",
        title="JSON破損確認",
        source_text="A" * 300,
        additional_instructions="短くする",
        tts_reading_mode="llm",
        language="ja",
        difficulty="beginner",
        scale="quick",
    )

    with pytest.raises(LlmJsonParseError) as exc_info:
        parse_llm_json_or_raise('{"documents": [{"text": "missing comma" "broken"}]}', context)

    output_dir = tmp_path / "tmp" / "failed_generations"
    raw_files = list(output_dir.glob("*_raw.txt"))
    error_files = list(output_dir.glob("*_error.txt"))
    meta_files = list(output_dir.glob("*_prompt_meta.json"))
    assert len(raw_files) == len(error_files) == len(meta_files) == 1
    assert raw_files[0].read_text(encoding="utf-8").startswith('{"documents"')
    assert "doc_01" in error_files[0].read_text(encoding="utf-8")
    assert "gemini-test" in error_files[0].read_text(encoding="utf-8")

    meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
    assert meta["sourceTextLength"] == 300
    assert meta["sourceTextPreview"] == "A" * 200
    assert "A" * 201 not in meta_files[0].read_text(encoding="utf-8")
    assert exc_info.value.saved_prefix


def test_record_debug_prompt_is_noop_when_debug_disabled(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "debug_prompts_enabled", False)
    pop_debug_prompts()  # clear any leftovers
    record_debug_prompt("planner", "planner", "model-x", "prompt body")
    assert pop_debug_prompts() == []


def test_record_debug_prompt_collects_records_when_enabled(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "debug_prompts_enabled", True)
    pop_debug_prompts()  # clear any leftovers
    record_debug_prompt(
        "quiz",
        "quiz_range_01",
        "gemini-2.5-pro",
        "Create one Sokqa quiz JSON...",
        quiz_title="前半の理解チェック",
    )
    records = pop_debug_prompts()
    assert len(records) == 1
    record = records[0]
    assert isinstance(record, DebugPromptRecord)
    assert record.prompt_type == "quiz"
    assert record.target == "quiz_range_01"
    assert record.model == "gemini-2.5-pro"
    assert record.quiz_title == "前半の理解チェック"
    assert record.doc_title == ""
    assert record.characters == len("Create one Sokqa quiz JSON...")
    assert record.generated_at  # ISO 8601


def test_pop_debug_prompts_clears_buffer(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "debug_prompts_enabled", True)
    pop_debug_prompts()
    record_debug_prompt("doc", "doc_01", "m", "p")
    assert len(pop_debug_prompts()) == 1
    assert pop_debug_prompts() == []
