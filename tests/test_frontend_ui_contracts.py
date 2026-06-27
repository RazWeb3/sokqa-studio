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
    record_units_html = html[html.index("function renderUnits() {"):html.index("function selectedUnits() {")]

    assert "RECORDING_CHUNK_SIZE" not in html
    assert "function chunkUnitIds" not in html
    assert "Object.assign(targetSnapshot, data.target)" not in html
    assert 'requestJson("/tts/record", {' in html
    assert "unitIds," in html
    assert "件をサーバーで録音しています" in html
    assert "--fixed-action-state-width: 320px;" in html
    assert 'id="recordViewRecordTab"' in html
    assert 'id="recordViewPlaybackTab"' in html
    assert 'id="recordViewDeleteTab"' in html
    assert '<label>録音するテキスト<select id="recordingTextSource"><option value="raw">生テキスト</option><option value="corrected">補正テキスト</option></select></label>' in html
    assert 'id="recordActionBadge"' in html
    assert 'id="recordActionTitle"' in html
    assert 'id="recordActionText"' in html
    assert 'function setRecordView(mode)' in html
    assert 'activeRecordView = ["record", "play", "delete"].includes(mode) ? mode : "record";' in html
    assert '$("recordRecordControls").classList.toggle("active", activeRecordView === "record");' in html
    assert '$("recordPlaybackControls").classList.toggle("active", activeRecordView === "play");' in html
    assert '$("recordDeleteButtons").classList.toggle("active", activeRecordView === "delete");' in html
    assert 'id="playbackRateSelect"' in html
    assert '<button class="danger-btn" id="resetSelectedRecordingBtn" type="button">選択した録音を削除</button>' in html
    assert '<button class="danger-btn" id="resetAllRecordingBtn" type="button">すべての録音を削除</button>' in html
    assert 'id="selectRecordedPlaybackBtn"' in html
    assert 'id="selectRecordedDeleteBtn"' in html
    assert '<button id="playSelectedBtn" type="button">連続再生</button>' in html
    assert '<button class="secondary" id="prevTrackBtn" type="button">前</button><button id="playSelectedBtn" type="button">連続再生</button><button class="secondary" id="nextTrackBtn" type="button">次</button>' in html
    assert 'id="playAllBtn"' not in html
    assert 'id="pauseTrackBtn"' not in html
    assert 'id="nowPlaying"' not in html
    assert ".record-action-bar {" in html
    assert "grid-template-columns: var(--fixed-action-state-width) minmax(250px, 300px) minmax(0, 1fr);" in html
    assert ".record-action-controls.active { display: flex; align-items: center; min-width: 0; }" in html
    assert ".record-settings-grid { display: grid; grid-template-columns: repeat(2, minmax(118px, 1fr)); gap: 8px; width: 100%; }" in html
    assert ".record-playback-grid .player-controls { display: flex; align-items: center; gap: 8px; flex-wrap: nowrap; }" in html
    assert ".record-playback-grid audio { display: none; }" in html
    assert ".record-action-buttons { display: none; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; min-width: 0; }" in html
    assert ".record-delete-buttons { width: 100%; margin-left: auto; justify-content: flex-end; gap: 6px; flex-wrap: nowrap; }" in html
    assert ".unit-status-badge { padding: 6px 10px; font-size: 13px; font-weight: 700; }" in html
    assert 'const allowPlayButton = activeRecordView === "play";' in record_units_html
    assert 'const recorded = unit.isRecorded ? `<span class="badge success unit-status-badge">録音済み</span>` : `<span class="badge warn unit-status-badge">未録音</span>`;' in record_units_html
    assert '<small><span class="badge">${escapeHtml(sourceBadge(unit))}</span> ${Number(unit.charCount || 0).toLocaleString("ja-JP")}字</small>' in record_units_html
    assert '<span class="unit-actions">${recorded}${playButton}</span>' in record_units_html
    assert '<small>${escapeHtml(String(unit.text || ""))}</small>' in record_units_html
    assert 'target="_blank" rel="noreferrer"' not in record_units_html
    assert '$("playSelectedBtn").textContent = currentPlayingUnitId && !isPlaybackPaused ? "一時停止" : "連続再生";' in html
    assert 'playQueue(selectedUnits().length ? selectedUnits() : unitsForActiveRecordView());' in html
    assert 'playQueueItems = unitsForActiveRecordView();' in html
    assert "forceRecordSelectedBtn" not in html
    assert "loadPacksBtn" not in html
    assert "record-inline-player" not in html


def test_pack_modal_places_import_next_to_refresh() -> None:
    html = _html()

    start = html.index('<div class="modal" id="packModal"')
    end = html.index('<div class="modal" id="versionModal"', start)
    modal_html = html[start:end]

    assert '<button id="refreshPacksBtn" type="button">一覧更新</button><button class="secondary" id="importPackBtn" type="button">インポート</button>' in modal_html
    assert 'class="pack-import-fields" id="packImportFields"' in modal_html
    assert 'id="importFiles"' in modal_html
    assert 'id="importTitle"' in modal_html
    assert 'id="importContentId"' in modal_html
    assert 'id="importSlug"' in modal_html
    assert 'id="importPackSubmitBtn"' in modal_html
    assert 'id="importStatus"' in modal_html
    assert 'function togglePackImportFields(force = null)' in html
    assert '$("packImportFields").classList.toggle("active", next);' in html
    assert '$("importPackBtn").textContent = next ? "インポートを閉じる" : "インポート";' in html


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
    assert 'id="baseLanguageDisplay"' in html
    assert 'id="learningLanguage"' in html
    assert 'hidden aria-hidden="true"' in html
    assert "plan.ttsLanguageSettings = ttsLanguageSettingsFromControls();" in html
    assert "delete plan.ttsLanguageSettings;" in html
    assert '$("multilingualTtsOptions").hidden = !multilingual;' in html
    assert '$("multilingualTtsOptions").open = multilingual;' in html
    assert '$("ttsDictionaryPanel").hidden = multilingual;' in html
    document_select = html[html.index('<select id="documentTextLanguageMode"'):html.index("</select>", html.index('<select id="documentTextLanguageMode"'))]
    assert 'value="mixed" selected' in document_select
    assert 'value="select"' in document_select
    assert "基本言語" in html
    assert "学習言語" in html
    assert "テーマから学習言語を推測します" in html
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
    assert "function expectedQuizPackCount()" in html
    assert "function shouldShowQuizChoiceLanguageSettings()" in html
    assert "function renderQuizChoiceLanguageSettings()" in html
    assert "function quizChoiceLanguageModesPayload(count = expectedQuizPackCount())" in html
    assert 'if (ttsModeValue() !== "multilingual") return false;' in html
    assert '!["pack", "quiz"].includes(generationUnitValue())' in html
    assert "return expectedQuizPackCount() > 0;" in html
    assert "return { quick: 1, standard: 2, large: 3, auto: 4 }[scaleValue()] || 1;" in html
    assert 'if (unit === "quiz") return Number($("quizCount").value || 1);' in html
    assert "let quizChoiceLanguageModes = [];" in html
    assert 'id="quizChoiceLanguageSettings"' in html
    assert 'id="quizChoiceLanguageSettingsList"' in html
    assert "クイズ選択肢の表示言語" in html
    assert "Quiz ${quizIndex + 1}" in html
    assert "function normalizeQuizChoiceLanguageModes(plan)" in html
    assert 'return plan?.learningLanguage ? "learning" : "auto";' in html
    assert 'type="radio"' in html
    assert "select.disabled = !active;" in html
    assert 'select.title = active ? "" : "「言語を選ぶ」を選んだ時だけ有効です。";' in html
    assert "syncTtsLanguageControls();" in html
    assert '<button class="generate-primary" id="planBtn" type="button">プランを作成</button>' in html
    assert 'data-quiz-choice-language-mode' in html
    assert "パック言語" in html
    assert "学習言語" in html
    assert "おまかせ" in html
    render_start = html.index("function renderPlanPreview(plan)")
    render_end = html.index("function renderPlanPreviewFromJson()", render_start)
    render_html = html[render_start:render_end]
    assert "選択肢言語：" in render_html
    assert 'labelFor("choiceLanguageMode", quiz.choiceLanguageMode || defaultQuizChoiceLanguageMode(plan))' in render_html
    assert '<select data-quiz-choice-language-mode=' not in render_html


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
    assert 'quizChoiceLanguageModes: quizChoiceLanguageModesPayload(),' in html
    assert html.index('id="language"') < html.index('id="generationUnit"') < html.index('id="customInstructions"')
    assert "追加条件" in html
    assert 'customInstructions: $("customInstructions").value.trim()' in html
    assert 'const customInstructions = $("customInstructions").value.trim();' in html
    assert 'if (customInstructions && !String(plan.customInstructions || "").trim())' in html
    assert "plan.customInstructions = customInstructions;" in html


def test_plan_condition_suggestion_ui_is_available_and_appends_only_selected_text() -> None:
    html = _html()

    assert 'id="suggestConditionsStandaloneBtn"' in html
    assert ">AIに追加条件を提案してもらう<" in html
    assert 'button.textContent = "AI提案を生成中...";' in html
    assert 'button.textContent = "AIに追加条件を提案してもらう";' in html
    assert 'id="conditionSuggestionsPanel" hidden' in html
    assert 'class="condition-suggestions" id="conditionSuggestions"' in html
    assert 'requestJson("/api/plan-suggest-conditions"' in html
    assert "function displayLanguageValue()" in html
    assert 'displayLanguage: displayLanguageValue(),' in html
    assert 'const value = document.documentElement.lang?.trim();' in html
    assert 'hasSourceMaterial: Boolean($("sourceText").value.trim())' in html
    assert "function renderConditionSuggestions()" in html
    assert "function appendConditionSuggestion(suggestion)" in html
    assert "existingCustomInstructionLines().includes(text)" in html
    assert '`${current}\\n\\n----\\nAI提案:\\n${text}`' in html
    assert 'data-add-condition="${escapeHtml(suggestion.id || "")}"' in html
    assert '${added ? "追加済み" : "追加"}' in html
    assert "handleGenerationControlChange();" in html
    assert html.index('id="customInstructions"') < html.index('id="suggestConditionsStandaloneBtn"') < html.index('id="conditionSuggestionsPanel"')
    assert html.index('id="conditionSuggestionsPanel"') < html.index('id="planBtn"') < html.index('id="generateBtn"')


def test_material_mode_preview_reflects_source_presence() -> None:
    html = _html()

    assert "function materialModePreviewLabel(plan)" in html
    assert 'return "テーマから作成";' in html
    assert 'if (plan.materialMode === "strict") return "資料をそのまま使う";' in html
    assert 'if (plan.materialMode === "reference") return "資料を参考に作る";' in html
    assert "${escapeHtml(materialModePreviewLabel(plan))}" in html


def test_language_selection_does_not_override_tts_mode() -> None:
    html = _html()

    assert "function syncPackLanguageUi()" in html
    assert 'id="language"' in html
    assert "$(\"ttsReadingMode\").value = defaultTtsModeForLanguage" not in html
    assert "function defaultTtsModeForLanguage" not in html


def test_generation_form_metadata_controls_are_available() -> None:
    html = _html()

    assert "[hidden] { display: none !important; }" in html
    for label in ["小学生", "中学生", "高校生", "大学生", "資格学習者", "社会人", "実務担当者"]:
        assert f"<option>{label}</option>" in html or f"<option selected>{label}</option>" in html
    assert '<option selected>社会人</option>' in html
    assert "<option>初学者</option>" not in html
    assert "<option selected>初学者</option>" not in html
    assert "<option>上級者</option>" not in html
    assert "<option selected>上級者</option>" not in html
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


def test_generate_flow_exposes_auto_quality_fix_opt_in_and_defaults_off() -> None:
    html = _html()

    assert 'id="autoQualityFixAfterGenerate" type="checkbox"' in html
    assert "生成後に自動品質チェック・修正提案を行う" in html
    assert "時間がかかります。修正は選択後に適用します。" in html
    assert html.index('id="customInstructions"') < html.index('id="suggestConditionsStandaloneBtn"') < html.index('id="planBtn"')
    assert html.index('id="planBtn"') < html.index('id="autoQualityFixAfterGenerate"') < html.index('id="generateBtn"')
    assert html.index('id="ttsReadingMode"') < html.index('id="autoQualityFixAfterGenerate"')
    assert 'id="generateBtn" type="button" hidden disabled' in html
    assert '$("generateBtn").hidden = !hasPlan;' in html
    assert 'id="generateQualityOption" hidden' in html
    assert '$("generateQualityOption").hidden = !hasPlan;' in html
    assert 'id="autoTextQualityResult"' in html
    assert "自動品質チェックはOFFです。" in html
    assert 'if (isAutoQualityFixEnabled()) {' in html
    assert "await runBatchQualityWorkflow(generated);" in html
    assert 'checked id="autoQualityFixAfterGenerate"' not in html


def test_generate_screen_uses_settings_review_result_tabs_and_stateful_action_bar() -> None:
    html = _html()

    assert 'id="generationSettingsTab"' in html
    assert 'id="generationReviewTab"' in html
    assert 'id="generationResultTab"' in html
    assert 'data-generation-view-panel="settings"' in html
    assert 'data-generation-view-panel="review" hidden' in html
    assert 'data-generation-view-panel="result" hidden' in html
    assert "function setGenerationView(view)" in html
    assert '["settings", "review", "result"].includes(view)' in html
    assert 'setGenerationView("review");' in html
    assert 'renderGeneratedResult(generated);\n          setGenerationView("result");' in html
    assert 'class="generate-action-bar"' in html
    action_bar_css = html[html.index(".generate-action-bar {"):html.index(".generate-action-bar .actions")]
    assert "position: fixed;" in action_bar_css
    assert "left: max(14px, calc((100vw - 1480px) / 2 + 28px));" in action_bar_css
    assert "grid-template-columns: var(--fixed-action-state-width) 220px 320px 188px;" in action_bar_css
    assert "justify-content: space-between;" in action_bar_css
    assert 'id="progressToast"' not in html
    layout_css = html[html.index(".app {"):html.index(".workflow-label")]
    assert "body {" in html
    assert "height: 100%;" in html[html.index("body {"):html.index("button, input, select, textarea {")]
    assert "overflow: hidden;" in html[html.index("body {"):html.index("button, input, select, textarea {")]
    assert "display: grid;" in layout_css
    assert "grid-template-rows: auto minmax(0, 1fr);" in layout_css
    workflow_css = html[html.index(".workflow-shell {"):html.index(".workflow-label")]
    assert "position: static;" in workflow_css
    assert "height: 100%;" in workflow_css
    assert "min-height: 0;" in workflow_css
    assert "overflow-x: hidden;" in workflow_css
    assert "overflow-y: auto;" in workflow_css
    main_css = html[html.index("main {"):html.index(".tab-panel[hidden]")]
    assert "height: 100%;" in main_css
    assert "overflow-y: auto;" in main_css
    assert "scrollbar-gutter: stable;" in main_css
    responsive_css = html[html.index("@media (max-width: 1060px) {"):html.index("@media (max-width: 760px) {")]
    assert "body { overflow-y: auto; }" in responsive_css
    assert ".app { height: auto; min-height: 100vh; padding-bottom: 48px; }" in responsive_css
    assert ".studio-layout, main { overflow: visible; }" in responsive_css
    assert "main { height: auto; padding-right: 0; }" in responsive_css
    assert ".workflow-shell { position: static; height: auto; min-height: 0; overflow-y: visible; }" in html
    assert 'id="generateActionBadge"' in html
    assert 'id="generateActionLoader" hidden' in html
    assert 'class="actions generate-plan-slot"' in html
    assert 'class="actions generate-run-slot"' in html
    option_css = html[html.index(".generate-action-option {"):html.index(".generate-action-option .hint { justify-self: start; max-width: 38ch; }")]
    assert "width: min(100%, 280px);" in option_css
    assert "justify-self: end;" in option_css
    assert "text-align: left;" in option_css
    assert "justify-content: flex-start;" in html[html.index(".generate-action-option .checkline {"):html.index(".generate-action-option .hint { justify-self: start; max-width: 38ch; }")]
    assert 'generationActionState = "planning";' in html
    assert 'generationActionState = "generating";' in html
    assert 'generationActionState = "quality";' in html
    assert 'generationActionState = "complete";' in html
    assert '$("planBtn").textContent = hasPlan ? "プランを再作成" : "プランを作成";' in html
    assert '$("planBtn").classList.toggle("secondary", hasPlan);' in html
    assert '$("planBtn").classList.toggle("generate-primary", !hasPlan);' in html


def test_quality_screen_uses_text_and_reading_correction_tabs() -> None:
    html = _html()

    assert 'id="qualityTextTab"' in html
    assert 'id="qualityTtsTab"' in html
    assert 'data-quality-view="text"' in html
    assert 'data-quality-view="tts"' in html
    assert "テキスト品質" in html
    assert "読み補正品質" in html
    assert 'id="qualityPanelTitle"' in html
    assert 'id="qualityPanelHint"' in html
    assert 'id="qualityPanelBadge" hidden' in html
    assert 'class="quality-action-bar"' in html
    assert 'id="qualityActionBadge"' in html
    assert 'id="qualityActionTitle"' in html
    assert 'id="qualityActionText"' in html
    assert "grid-template-columns: var(--fixed-action-state-width) minmax(0, 1fr);" in html[html.index(".quality-action-bar {"):html.index(".quality-action-state {")]
    assert 'id="qualityTextButtons"' in html
    assert 'id="qualityTtsButtons"' in html
    assert 'id="qualityTtsLockedButtons"' in html
    assert '<button id="skipTextWorkflowBtn" type="button">テキスト工程をスキップ</button>' in html
    assert "function setQualityResultPane(mode)" in html
    assert 'activeQualityView = mode === "tts" ? "tts" : "text";' in html
    assert 'button.setAttribute("aria-selected", String(button.dataset.qualityView === activeQualityView));' in html
    assert '$("qualityPanelTitle").textContent = activeQualityView === "text" ? "テキスト品質チェック・修正" : "読み補正品質チェック・修正";' in html
    assert '$("qualityPanelBadge").hidden = activeQualityView !== "tts";' in html
    assert "function updateQualityActionBar()" in html
    assert 'setActionButtonTone("textCheckBtn",' in html
    assert 'setActionButtonTone("textFixBtn",' in html
    assert 'setActionButtonTone("applyTextFixBtn",' in html
    assert 'setActionButtonTone("saveTextFixBtn",' in html
    assert 'setActionButtonTone("skipTextWorkflowBtn", true);' in html
    assert 'setActionButtonTone("ttsCheckBtn",' in html
    assert 'setActionButtonTone("ttsFixBtn",' in html
    assert 'setActionButtonTone("saveTtsFixBtn",' in html
    assert 'setQualityResultPane("text");' in html
    assert 'setQualityResultPane("tts");' in html
    assert '$$("[data-quality-view]").forEach((tab) => tab.addEventListener("click", () => setQualityResultPane(tab.dataset.qualityView)));' in html
    assert "読み補正の品質チェックは未実行です。" in html
    assert "補正実行" in html
    assert "保存" in html
    mobile_css = html[html.index("@media (max-width: 760px) {"):html.index(".pack-option {", html.index("@media (max-width: 760px) {"))]
    assert ".quality-view-tab { min-width: 0; width: calc(100% / 2); }" in mobile_css
    assert ".quality-action-bar {" in mobile_css


def test_generate_flow_runs_batch_quality_after_success_when_enabled() -> None:
    html = _html()

    assert "品質チェック実行中..." in html
    assert "finishProgress(\"生成\", \"生成済みパックを対象に設定しました。\")" in html
    assert html.index("finishProgress(\"生成\", \"生成済みパックを対象に設定しました。\")") < html.index("await runBatchQualityWorkflow(generated);")


def test_batch_quality_uses_existing_text_and_tts_check_fix_apis() -> None:
    html = _html()
    start = html.index("async function runBatchQualityWorkflow")
    end = html.index("async function applySelectedBatchQualityFixes", start)
    auto_check_html = html[start:end]

    assert "loadPackJson" not in auto_check_html
    assert "requestGet" not in auto_check_html
    assert "selectedFileJsonUrl" not in auto_check_html
    assert 'requestJson("/quality/text-check", { target: recordingTarget(pack) })' in auto_check_html
    assert 'requestJson("/quality/tts-check", { target: recordingTarget(pack) })' in auto_check_html
    assert 'requestJson("/quality/text-fix", { target: recordingTarget(pack), issues: textIssues })' in auto_check_html
    assert 'requestJson("/quality/tts-fix", { target: recordingTarget(pack), issues: ttsIssues })' in auto_check_html
    assert "console.error(\"auto quality check failed\"" in auto_check_html
    assert "品質チェックを実行できませんでした。" in auto_check_html
    assert 'setProgress("品質チェック", "失敗", "品質チェックを実行できませんでした。", 1);' in auto_check_html


def test_batch_quality_does_not_fetch_public_pack_json_for_gcs_or_localhost_urls() -> None:
    html = _html()
    start = html.index("function generatedQualityTargets")
    end = html.index("function renderQualityFixResult", start)
    batch_html = html[start:end]

    assert "async function loadPackJson" not in html
    assert "originalJson" not in batch_html
    assert "requestGet(url)" not in batch_html
    assert "selectedFileJsonUrl(pack)" not in batch_html
    assert 'fileUrl: pack?.url' in batch_html
    assert 'storagePrefix: pack?.storagePrefix' in batch_html


def test_batch_quality_displays_text_and_tts_fix_candidates() -> None:
    html = _html()
    start = html.index("function renderBatchQualityResult")
    end = html.index("function selectedBatchFixKeys", start)
    render_html = html[start:end]

    assert "修正候補 0件" in render_html
    assert "修正候補 ${total}件" in render_html
    assert "Text ${textCount}件 / TTS ${ttsCount}件" in render_html
    assert 'data-batch-fix' in render_html
    assert "選択した修正を適用" in render_html


def test_batch_quality_apply_button_updates_on_busy_change_and_is_safe_without_button() -> None:
    html = _html()
    start = html.index("function updateActionAvailability()")
    end = html.index("function scaleValue()", start)
    action_html = html[start:end]
    batch_start = html.index("function updateBatchQualityApplyAvailability()")
    batch_end = html.index("function findUnit(", batch_start)
    batch_html = html[batch_start:batch_end]

    assert "updateBatchQualityApplyAvailability();" in action_html
    assert 'const button = $("applyBatchQualityFixBtn");' in batch_html
    assert "if (button) button.disabled = isBusy || selectedBatchFixKeys().length === 0;" in batch_html


def test_auto_text_quality_targets_only_generated_document_and_quiz_files() -> None:
    html = _html()
    start = html.index("function generatedQualityTargets")
    end = html.index("function renderAutoTextQualityStatus", start)
    targets_html = html[start:end]

    assert "pack.contentId === contentId" in targets_html
    assert "pack.versionId === versionId" in targets_html
    assert '["document", "quiz"].includes(pack.kind)' in targets_html
    assert "pack.packName" in targets_html


def test_batch_quality_apply_orders_text_before_tts_and_saves_once() -> None:
    html = _html()
    start = html.index("async function applySelectedBatchQualityFixes")
    end = html.index("function renderQualityFixResult", start)
    apply_html = html[start:end]

    assert 'requestJson("/quality/text-fix/apply", {' in apply_html
    assert "textChangedUnits.add(unitKey(record.pack.packName, fix.location?.unitId))" in apply_html
    assert ".filter((fix) => !textChangedUnits.has(unitKey(record.pack.packName, fix.location?.unitId)))" in apply_html
    assert "applyTtsFixToJson(finalJson, fix)" in apply_html
    assert 'requestJson("/quality/tts-fix", {' in apply_html
    assert "issues: selectedTtsEntries.map((entry) => entry.issue)" in apply_html
    assert 'requestJson("/quality/save-version", {' in apply_html
    assert "console.error(\"auto quality fix apply failed\"" in apply_html
    assert "品質修正を適用しました" in apply_html
    assert "品質修正の適用に失敗しました。" in apply_html
    assert apply_html.index('requestJson("/quality/text-fix/apply", {') < apply_html.index('requestJson("/quality/save-version", {')


def test_manual_text_quality_check_still_uses_existing_flow() -> None:
    html = _html()
    start = html.index("async function runTextCheck")
    end = html.index("async function runTextFix", start)
    manual_html = html[start:end]

    assert 'lastTextCheckResult = await requestJson("/quality/text-check", { target: recordingTarget(selectedPack) });' in manual_html
    assert 'renderQualityIssues("textQualityResult", lastTextCheckResult, "text");' in manual_html
    assert 'setBadge("qualityBadge", "チェック済み", "info");' in manual_html


def test_save_version_rebuilds_selected_pack_from_new_version_once() -> None:
    html = _html()

    assert "async function applySavedVersion(versionId, before = selectedPack)" in html
    assert 'await loadPacks({ keepSelection: false, forceRefresh: true });' in html
    assert "const refreshed = packForSavedVersion(before, versionId, fallbackRevision);" in html
    assert "const fallbackRevision = Number.isFinite(Number(before.revision))" in html
    assert "selectedPack = normalized;" in html
    assert "updateGlobalStatus();" in html
    assert 'await refreshVersionInfoIfOpen();' in html
    assert 'await refreshImportQrIfOpen();' in html
    assert html.count("await applySavedVersion(newVersionId, before);") == 2


def test_saved_version_fallback_updates_revision_and_manifest_url() -> None:
    html = _html()
    start = html.index("function packAtVersion(")
    end = html.index("async function selectPack", start)
    pack_html = html[start:end]

    assert "function packAtVersion(pack, versionId, revision = pack?.revision)" in pack_html
    assert "revision: revision ?? pack.revision," in pack_html
    assert "function packForSavedVersion(reference, versionId, revision = null)" in pack_html
    assert "exactMatchingPack({ ...reference, versionId })" in pack_html
    assert "revision ?? latest?.revision ?? reference?.revision" in pack_html


def test_import_qr_refresh_keeps_current_selected_version() -> None:
    html = _html()
    start = html.index("async function openImportQr")
    end = html.index("function groupPacks", start)
    qr_html = html[start:end]

    assert 'await loadPacks({ keepSelection: false, forceRefresh: true });' in qr_html
    assert "selectedPack = packForSavedVersion(before, before.versionId, before.revision);" in qr_html
    assert "latestMatchingPack(before)" not in qr_html
    assert 'await openImportQr({ refreshLatest: true });' in qr_html


def test_request_get_uses_no_store_cache_policy() -> None:
    html = _html()

    assert 'async function requestGet(url, { noStore = false } = {})' in html
    assert 'const response = await fetch(url, noStore ? { cache: "no-store" } : {});' in html


def test_generated_result_delegates_share_url_and_qr_to_version_modal() -> None:
    html = _html()
    start = html.index("function renderGeneratedResult")
    end = html.index("async function createPlan", start)
    result_html = html[start:end]

    assert "syncShareLinks" not in html
    assert "shareManifestUrl" not in html
    assert "shareImportUrl" not in html

    assert 'id="openShareInfoBtn"' in result_html
    assert 'id="openShareInfoHint"' in result_html
    assert 'openVersionInfo({ force: true })' in result_html

    assert 'id="shareUrl"' not in html
    assert 'id="qrcode"' not in html
    assert 'new QRCode($("qrcode")' not in html


def test_generated_result_share_button_is_enabled_by_global_status() -> None:
    html = _html()
    start = html.index("function updateGlobalStatus()")
    end = html.index("function updateStatusBadges()", start)
    status_html = html[start:end]

    assert "$(\"openShareInfoBtn\").disabled = true;" in status_html
    assert "$(\"openShareInfoBtn\").disabled = !selectedPack.manifestUrl;" in status_html
