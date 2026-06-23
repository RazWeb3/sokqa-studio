# Sokqa Studio Changelog

この CHANGELOG は現行実装と git log から主要イベントを整理した開発履歴である。

正式なリリースタグが未確認のため、バージョン番号は TODO を含む。

## v0.1

- 初期生成機能
- `/plan-pack` と `/generate-pack`
- document / quiz / manifest 生成
- local storage 保存

TODO: 正式な対象 commit / 日付を確認する。

## v0.2

- ZIP 出力
- `/packs/export-json-zip`
- Web UI から JSON ZIP を download する導線

関連 commit:

- `a99a966 Add TTS revision and JSON zip export endpoints`

## v0.3

- TTS 最適化
- system / user / plan TTS rules
- `rule` / `llm` mode
- TTS dictionary modal / editable rules
- reading pattern selection

関連 commit:

- `67316b3 Add reading pattern selection to planning and TTS`
- `a2321a0 Add editable TTS dictionary rules`
- `0d921e6 Add TTS dictionary modal and rule promotion`

## v0.4

- Quality Checker
- text check と TTS check の分離
- factual / style / leak
- reading / double_utterance / notation / tts_text_mismatch

関連 commit:

- `a9bd149 Add quality check agent and UI`
- `668574a Split quality checks for text and TTS`

## v0.5

- Revision 管理
- `PackManifestV2`
- `latest.json`
- `/packs` が contentId ごとに最新 revision を返す
- recording / quality fix などの revision operation

関連 commit:

- `80e53b5 Return latest /packs version per contentId`
- `83ec75e Add latest pack pointer for manifest listing`

## v0.6

- 多言語対応
- インドネシア語対応
- quiz description の pack language 対応
- planner fallback metadata の localization

関連 commit:

- `bf94820 Add Indonesian support and TTS duplicate collapse`
- `7eb3d87 Localize planner fallback metadata`
- `abde88a Localize quiz descriptions by pack language`

## v0.7

- TTS 修正エージェント
- quality tts fix
- already-corrected reading issue filter
- unspoken symbol reading issue filter
- TTS 修正時の display text 保持と audio clear

関連 commit:

- `b9df48c Tighten TTS fix handling and quality review layout`
- `9773d91 Filter unspoken symbol reading issues from TTS checks`
- `3367d3e Filter already-corrected TTS reading issues`
- `1411b82 Remove ttsNeedsRefresh from quality workflows`

## v0.8

- パック言語対応
- multilingual TTS 改善
- pack language を default language としたタグ制御
- strict source file splitting limit

関連 commit:

- `57ad798 Add strict source file splitting limits`
- `bb9eee9 Handle pack language in multilingual TTS settings`

## v0.9 (Unreleased)

- pack language 対応強化
- multilingual TTS タグ仕様整理
- choiceTexts 保持ルール改善
- docs 整備
- Agent Design 再整理
- learningLanguage 検討開始

## TODO: 未分類の主要変更

- `42433c4 Refactor studio workflow and update UI components`
- `f33bfbb Refactor studio flows and tighten UI state handling`
- `7559cd1 Refactor studio flows and state management`
- `2761b0b Refine listening-first planning and document tagging`
- `8a0cdc1 Add generation controls for pack planning`
- `3ef93a8 Harden LLM JSON parsing and prompt safety`
- `bebc1b1 Randomize quiz answer positions and flag regular cycles`
- `a04c230 Improve plan UI and validation feedback`

TODO: 正式リリース単位が決まり次第、v0.x の境界をタグまたはリリース日で確定する。
