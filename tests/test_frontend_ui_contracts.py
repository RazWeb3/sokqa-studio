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
    assert "高精度（読み補正）" in select_html
    assert 'value="multilingual"' in select_html
    assert "多言語（読み分け）" in select_html
    assert 'value="auto"' not in select_html
    assert 'id="ttsSwitch"' not in html


def test_pack_language_ui_exposes_supported_languages_and_custom_code() -> None:
    html = _html()
    select_start = html.index('<select id="language">')
    select_end = html.index("</select>", select_start)
    select_html = html[select_start:select_end]

    for value in ["ja", "en", "zh", "ko", "es", "fr", "de", "it", "pt", "id", "custom"]:
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
    assert "多言語（読み分け）では使用しません" in html


def test_tts_payload_uses_single_mode_field() -> None:
    html = _html()

    assert 'function ttsModeValue()' in html
    assert 'return ttsModeValue() !== "none";' in html
    assert 'const payload = { ttsReadingMode: ttsModeValue() };' in html
    assert 'if (settings) payload.ttsLanguageSettings = settings;' in html
    assert 'id="multilingualTtsOptions"' in html


def test_recording_ui_sends_one_record_request_without_client_chunking() -> None:
    html = _html()

    assert "RECORDING_CHUNK_SIZE" not in html
    assert "function chunkUnitIds" not in html
    assert "Object.assign(targetSnapshot, data.target)" not in html
    assert 'requestJson("/tts/record", {' in html
    assert "unitIds," in html
    assert "件をサーバーで録音しています" in html


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
    assert '$("multilingualTtsOptions").hidden = !multilingual;' in html
    assert '$("ttsDictionaryPanel").hidden = multilingual;' in html
    document_select = html[html.index('<select id="documentTextLanguageMode"'):html.index("</select>", html.index('<select id="documentTextLanguageMode"'))]
    assert 'value="mixed" selected' in document_select
    assert 'value="select"' in document_select
    assert '※本文中に部分的に混在する学習対象言語や外国語を指定します。' in html
    assert 'インドネシア語で日本語を学ぶ' not in html
    assert '例: 英語で日本語を学ぶ、日本語で英語を学ぶなど' in html
    assert '「言語を選ぶ」を選んだ項目だけ、右側の言語指定が有効になります。' in html
    for status_text in ["混合判定", "自動判定", "未使用"]:
        assert status_text not in html
    assert ">有効<" not in html
    assert "tts-language-status" not in html
    assert '["pack", "パック言語"]' in html
    assert 'documentTextLanguageMode: "mixed"' in html
    assert 'questionLanguageMode: "mixed"' in html
    assert 'choicesLanguageMode: "select"' in html
    assert 'explanationLanguageMode: "mixed"' in html
    assert 'documentTextLanguage: "pack"' in html
    assert 'questionLanguage: "pack"' in html
    assert 'choicesLanguage: "pack"' in html
    assert 'explanationLanguage: "pack"' in html
    assert "function syncTtsLanguageControls()" in html
    assert "select.disabled = !active;" in html
    assert 'select.title = active ? "" : "「言語を選ぶ」を選んだ時だけ有効です。";' in html
    assert "syncTtsLanguageControls();" in html
    assert '<button id="planBtn" type="button">プランを作成</button>' in html


def test_generation_policy_unit_and_material_controls_are_available_and_sent() -> None:
    html = _html()

    for element_id in ["structurePolicy", "generationUnit", "docCount", "quizCount", "materialMode", "customInstructions"]:
        assert f'id="{element_id}"' in html
    assert 'value="standard"' in html
    assert 'value="listening"' in html
    assert 'value="sequential"' not in html
    assert 'data-generation-unit="document"' in html
    assert 'data-generation-unit="quiz"' in html
    assert 'data-generation-unit="pack"' in html
    assert "学習パック" in html
    assert "ドキュメント</button>" in html
    assert "クイズ</button>" in html
    assert 'data-scale="auto"' in html
    assert 'data-scale="quick"' in html
    assert 'data-scale="standard"' in html
    assert 'data-scale="large"' in html
    assert 'class="segmented scale-cards" id="scaleSegment"' in html
    assert html.index('data-scale="quick"') < html.index('data-scale="standard"') < html.index('data-scale="large"') < html.index('data-scale="auto"')
    assert "おまかせ<small>内容量を自動調整</small>" in html
    assert "小規模<small>ドキュメント3＋クイズ1（30問）</small>" in html
    assert "中規模<small>ドキュメント6＋クイズ2</small>" in html
    assert "大規模<small>ドキュメント9＋クイズ3（生成に時間がかかります）</small>" in html
    assert 'value="reference"' in html
    assert 'value="source_only"' in html
    assert 'value="strict"' in html
    assert 'id="sourceMaterialDetails"' in html
    assert 'id="countControls" hidden' in html
    assert '<label id="docCountLine">ドキュメント数<select id="docCount"><option selected>1</option>' in html
    assert '<option value="">おまかせ</option></select></label>' in html
    assert 'id="sectionCount"' in html
    assert 'id="questionCount"' in html
    assert '<option selected>10</option>' in html
    assert '<option>20</option>' in html
    assert '<label id="quizCountLine">クイズ数<select id="quizCount"><option selected>1</option>' in html
    assert '<option selected>30</option>' not in html
    assert 'id="answerPositionMode" type="hidden" value="balanced"' in html
    assert '$("countControls").hidden = unit === "pack";' in html
    assert "function generationControlsPayload()" in html
    assert "strictSourceFileCount" in html
    assert "strict推定" in html
    assert "strictMaxFiles" in html
    assert "ファイル以内に収まるよう資料を分割" in html
    assert "...generationControlsPayload()," in html
    assert "syncGenerationControlsToPlan(plan)" in html
    assert html.index('id="language"') < html.index('id="customInstructions"') < html.index('id="generationUnit"')
    assert "追加条件" in html
    assert 'customInstructions: $("customInstructions").value.trim()' in html
    assert 'const customInstructions = $("customInstructions").value.trim();' in html
    assert 'if (customInstructions && !String(plan.customInstructions || "").trim())' in html
    assert "plan.customInstructions = customInstructions;" in html


def test_language_selection_does_not_override_tts_mode() -> None:
    html = _html()

    assert "function syncPackLanguageUi()" in html
    assert 'id="language"' in html
    assert "$(\"ttsReadingMode\").value = defaultTtsModeForLanguage" not in html
    assert "function defaultTtsModeForLanguage" not in html


def test_generation_form_metadata_controls_are_available() -> None:
    html = _html()

    assert "[hidden] { display: none !important; }" in html
    for label in ["初学者", "小学生", "中学生", "高校生", "大学生", "資格学習者", "社会人", "実務担当者", "上級者"]:
        assert f"<option>{label}</option>" in html or f"<option selected>{label}</option>" in html
    for element_id in [
        "globalTagsMode",
        "manualGlobalTags",
        "descriptionMode",
        "manualDescription",
        "descriptionIncludeDate",
        "descriptionIncludeAiDisclaimer",
        "ttsDictionaryPanel",
        "multilingualTtsOptions",
    ]:
        assert f'id="{element_id}"' in html
    assert '$("ttsDictionaryPanel").hidden = multilingual;' in html
    assert '$("multilingualTtsOptions").hidden = !multilingual;' in html
    assert '$("descriptionDateLine").hidden = false;' in html
    assert '$("descriptionAiLine").hidden = false;' in html


def test_tts_rule_editors_expose_simple_inputs_and_json_imports() -> None:
    html = _html()

    for element_id in [
        "temporaryRuleRows",
        "addTemporaryRuleBtn",
        "promoteTemporaryRulesBtn",
        "applyTemporaryRulesJsonBtn",
        "postTtsReplacePanel",
        "exportJsonZipBtn",
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
    assert 'requestJson("/packs/revise-tts"' in html
    assert 'requestJson("/debug/revise-tts"' in html
    assert "対象パックが選択されている場合は、その最新マニフェスト全体に適用します" in html
    assert "currentJobId" in html
    assert "if (!selectedPack && !currentJobId) return;" in html
    assert "postTtsReplacePanel\" hidden" not in html
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


def test_json_zip_export_ui_is_available_for_selected_pack() -> None:
    html = _html()

    assert 'id="exportJsonZipBtn"' in html
    assert 'id="exportGeneratedJsonZipBtn"' in html
    assert "function exportJsonZip()" in html
    assert 'requestBlob("/packs/export-json-zip", recordingTarget(selectedPack))' in html
    assert "application/zip" not in html
