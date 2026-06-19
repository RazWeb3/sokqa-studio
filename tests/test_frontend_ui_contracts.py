from pathlib import Path


INDEX_HTML = Path("web/index.html")


def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_tts_mode_ui_exposes_rule_and_llm_but_not_auto() -> None:
    html = _html()
    select_start = html.index('<select id="ttsReadingMode">')
    select_end = html.index("</select>", select_start)
    select_html = html[select_start:select_end]

    assert 'value="rule"' in select_html
    assert "標準（ルールのみ）" in select_html
    assert 'value="llm"' in select_html
    assert "高精度（AI補正）" in select_html
    assert 'value="auto"' not in select_html


def test_tts_mode_ui_defaults_to_llm() -> None:
    html = _html()
    select_start = html.index('<select id="ttsReadingMode">')
    select_end = html.index("</select>", select_start)
    select_html = html[select_start:select_end]

    assert '<option value="llm" selected>' in select_html
    assert '<option value="rule" selected>' not in select_html


def test_reading_patterns_are_sent_only_for_llm_mode() -> None:
    html = _html()

    assert 'function isLlmReadingMode()' in html
    assert 'return isTtsEnabled() && $("ttsReadingMode").value === "llm";' in html
    assert 'if (!isLlmReadingMode()) return new Set();' in html
    assert 'const showReadingPatterns = isLlmReadingMode() && patterns.length;' in html
    assert 'plan.selectedReadingPatternIds = [];' in html
