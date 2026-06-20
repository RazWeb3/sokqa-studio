from pathlib import Path


INDEX_HTML = Path("web/index.html")


def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_tts_mode_ui_exposes_four_modes_but_not_auto() -> None:
    html = _html()
    select_start = html.index('<select id="ttsReadingMode">')
    select_end = html.index("</select>", select_start)
    select_html = html[select_start:select_end]

    assert 'value="none"' in select_html
    assert ">なし<" in select_html
    assert 'value="rule"' in select_html
    assert "標準（ルールのみ）" in select_html
    assert 'value="llm"' in select_html
    assert "高精度（AI補正）" in select_html
    assert 'value="multilingual"' in select_html
    assert "多言語（AI補正）" in select_html
    assert 'value="auto"' not in select_html
    assert 'id="ttsSwitch"' not in html


def test_pack_language_ui_exposes_supported_languages_and_custom_code() -> None:
    html = _html()
    select_start = html.index('<select id="language">')
    select_end = html.index("</select>", select_start)
    select_html = html[select_start:select_end]

    for value in ["ja", "en", "zh", "ko", "es", "fr", "de", "it", "pt", "custom"]:
        assert f'value="{value}"' in select_html
    assert 'id="customLanguageCode"' in html
    assert "function languageValue()" in html


def test_tts_mode_ui_defaults_to_llm() -> None:
    html = _html()
    select_start = html.index('<select id="ttsReadingMode">')
    select_end = html.index("</select>", select_start)
    select_html = html[select_start:select_end]

    assert '<option value="llm" selected>' in select_html
    assert '<option value="rule" selected>' not in select_html


def test_reading_patterns_are_sent_only_for_high_precision_llm_mode() -> None:
    html = _html()

    assert "function usesReadingPatterns()" in html
    assert 'return ttsModeValue() === "llm";' in html
    assert "if (!usesReadingPatterns()) return new Set();" in html
    assert "const showReadingPatterns = usesReadingPatterns() && patterns.length;" in html
    assert 'plan.selectedReadingPatternIds = [];' in html
    assert "多言語（AI補正）では使用しません" in html


def test_tts_payload_uses_single_mode_field() -> None:
    html = _html()

    assert 'function ttsModeValue()' in html
    assert 'return ttsModeValue() !== "none";' in html
    assert 'const payload = { ttsReadingMode: ttsModeValue() };' in html
    assert 'if (settings) payload.ttsLanguageSettings = settings;' in html
    assert 'id="multilingualTtsOptions"' in html


def test_multilingual_language_settings_are_hidden_and_synced_by_mode() -> None:
    html = _html()

    for element_id in [
        "documentTextLanguageMode",
        "questionLanguageMode",
        "choicesLanguageMode",
        "explanationLanguageMode",
        "documentTextLanguage",
        "questionLanguage",
        "choicesLanguage",
        "explanationLanguage",
    ]:
        assert f'id="{element_id}"' in html
    assert 'if (ttsModeValue() === "multilingual") plan.ttsLanguageSettings = ttsLanguageSettingsFromControls();' in html
    assert 'else delete plan.ttsLanguageSettings;' in html
    assert '$("multilingualTtsOptions").hidden = ttsModeValue() !== "multilingual";' in html


def test_tts_rule_editors_expose_simple_inputs_and_json_imports() -> None:
    html = _html()

    for element_id in [
        "temporaryRuleRows",
        "addTemporaryRuleBtn",
        "promoteTemporaryRulesBtn",
        "applyTemporaryRulesJsonBtn",
        "postTtsReplacePanel",
        "openDictionaryRulesBtn",
        "ttsDictionaryCount",
        "dictionaryRulesModal",
        "closeDictionaryRules",
        "dictionaryRuleRows",
        "addDictionaryRuleBtn",
        "reloadDictionaryRulesBtn",
        "saveDictionaryRulesBtn",
        "replaceDictionaryRulesJsonBtn",
    ]:
        assert f'id="{element_id}"' in html
    assert "appendDictionaryRulesJsonBtn" not in html
    assert "JSONを追加" not in html
    assert "上級者向け: JSONで辞書を上書き" in html
    assert "生成後の読み置き換え" in html
    assert "読みを置き換えてTTS再最適化" in html
    assert "生成前に登録しておくと、標準/高精度の読み補正に使われます。" in html
    assert "辞書を管理" in html
    assert "function updateDictionarySummary()" in html
    assert "function openDictionaryRulesModal()" in html
    assert "function promoteTemporaryRulesToDictionary()" in html
    assert "function normalizeRule(rule)" in html
    assert "function parseRulesJson(text)" in html
    assert "right.rule.source.length - left.rule.source.length" in html
    assert "function loadDictionaryRules()" in html
    assert "function saveDictionaryRules()" in html
    assert 'requestGet("/debug/tts-rules")' in html
    assert 'requestPutJson("/debug/tts-rules", { rules: dictionaryTtsRules })' in html
    assert '"note"' not in html[html.index('<section class="panel" id="postTtsReplacePanel"'):html.index('id="dictionaryRulesModal"')]
