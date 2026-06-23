# Sokqa Studio Project Overview

## プロジェクト概要

Sokqa Studio は、Sokqa 向け学習パックを企画、生成、検証、修正、録音、共有するための制作支援ツールである。

現状の README では「Sokqa Studio の前段となる Sokqa 学習パック生成 Agent の MVP」と説明されているが、現在の実装では Web UI、パック一覧、品質チェック、品質修正、TTS 録音、Revision 管理、JSON ZIP 出力まで含む Studio ワークフローへ拡張されている。

現在の Studio は、パック生成、品質チェック、品質修正、TTS 最適化、録音、Revision 管理を統合した制作パイプラインとして運用されている。

## 解決する課題

- 学習パックを単体 JSON として手作業で作る負担を減らす。
- document と quiz の構成、生成、検証、修復、TTS 最適化、保存を一つの制作ラインとして扱う。
- 読み上げ品質、本文品質、録音状態、共有 URL をリリース前に確認できるようにする。
- パック更新時に revision と version を残し、Sokqa アプリ側が最新パックを参照しやすくする。

## 想定ユーザー

- Sokqa の教材制作者
- 学習パックを生成、レビュー、修正、録音する運用担当者
- Sokqa Studio を保守する開発者
- Codex などの開発支援エージェント

## 主要機能

### パック生成

`POST /plan-pack` で CoursePlan を作成し、`POST /generate-pack` で document JSON、quiz JSON、manifest を生成する。

生成は Gemini 利用時と mock 利用時の両方に対応している。Gemini 利用時は planner、document、quiz でタスク別モデルを指定できる。

### 品質チェック

`/quality/text-check` と `/quality/tts-check` が実装されている。

- Text Quality Checker: factual、style、leak
- TTS Quality Checker: reading、notation、double_utterance、tts_text_mismatch

### 品質修正

`/quality/text-fix`、`/quality/text-fix/apply`、`/quality/tts-fix`、`/quality/tts-fix/llm`、`/quality/save-version` が実装されている。

品質保証は以下の4エージェントで構成される。

- Text Quality Checker
- TTS Quality Checker
- Text Quality Fixer
- TTS Quality Fixer

Text Quality Fixer は承認制で本文修正候補を扱う。TTS Quality Fixer は自動適用可能な範囲を tts fields に限定し、display text は変更しない。

### TTS 最適化

生成時に `ttsReadingMode` に応じて TTS 用テキストを付与する。

実装済みモード:

- `none`
- `rule`
- `llm`
- `multilingual`

辞書は system dictionary、user dictionary、plan rules をマージして使う。

### 録音

`/tts/recording-estimate`、`/tts/record`、`/tts/recording-reset` が実装されている。

Cloud TTS を使って document / quiz の単位ごとに MP3 を生成し、audio path / audio URL を各 JSON に反映する。

### QR 共有

Web UI 上で manifest URL または選択ファイル URL の QR を表示する機能がある。

Sokqa アプリで manifest URL を読み込む前提の共有導線が実装されている。

### バージョン管理

`PackManifestV2`、`PackLatestV2`、`revision`、`versionId`、`fileVersionId`、`latest.json`、`versions/{versionId}/manifest.json` が実装されている。

初回生成、import、text fix、tts fix、recording、recording reset などが revision operation として記録される。

## システム構成

### Frontend

- `web/index.html`
- 単一 HTML / CSS / JavaScript の Studio UI
- 生成、品質チェック、品質修正、録音、QR、ZIP export などの操作画面を持つ

### Backend

- FastAPI
- エントリポイント: `main.py`
- 主要 route:
  - `/plan-pack`
  - `/generate-pack`
  - `/packs`
  - `/packs/import`
  - `/packs/delete`
  - `/packs/revise-tts`
  - `/packs/export-json-zip`
  - `/quality/*`
  - `/tts/*`
  - `/debug/*`

### Storage

Storage backend は local と GCS に対応している。

Local では `generated/` 配下に保存する。GCS では `GCS_BUCKET`、`GCS_PREFIX`、`PUBLIC_BASE_URL` に基づいて保存する。

v2 の保存構造:

```text
{base}/creators/{creatorId}/packs/{contentId}/
  latest.json
  versions/{versionId}/manifest.json
  objects/doc/{fileVersionId}.json
  objects/quiz/{fileVersionId}.json
  objects/audio/{audioVersionId}.mp3
```

### LLM 利用箇所

- Planner Agent: CoursePlan 生成
- Document Generator: document JSON 生成
- Quiz Generator: quiz JSON 生成
- TTS Optimizer: `llm` / `multilingual` mode の TTS 読み生成
- Text Quality Checker: factual / style / leak の品質指摘
- TTS Quality Checker: reading / notation / double_utterance / tts_text_mismatch の品質指摘
- Text Quality Fixer: 承認制の本文修正候補生成
- TTS Quality Fixer: TTS field の自動修正

Gemini provider が `mock` の場合、一部機能は deterministic mock / fallback を使う。

## 現在の状態

## 開発段階

MVP から Studio ワークフローへ拡張中の段階。

生成、保存、一覧、品質チェック、品質修正、録音、QR、ZIP export、Revision 管理は実装済み。ただし正式リリース仕様として未整理な項目が残っている。

## 実装済み機能

- CoursePlan 生成
- document / quiz 生成
- source material 対応
- strict source split
- TTS 辞書 rule 適用
- LLM TTS 最適化
- multilingual TTS タグ
- text / tts quality check
- text / tts quality fix
- TTS 録音、録音見積もり、録音リセット
- revision commit
- latest.json
- pack listing
- pack import
- pack delete
- JSON ZIP export
- QR 表示 UI
- Gemini task model override
- answer position balancing

## 未実装または未確定機能

- TODO: Sokqa アプリ側の manifest import 詳細仕様
- TODO: QR import URL の正式スキーム
- TODO: 管理者・権限・認証の正式仕様
- TODO: 本番運用時の GCS bucket / CDN / domain 制約
- TODO: UI の正式 IA とリリース対象画面
- TODO: 品質チェック結果の永続化方針
- TODO: バージョン番号とリリース履歴の正式対応

## ロードマップ

## 直近の予定

- SPEC を現行実装と同期し続ける。
- TTS multilingual と pack language の挙動をテストと仕様の両方で固定する。
- Text Quality Fixer と TTS Quality Fixer の安全条件を明文化し、本文変更と TTS 変更の承認フローを整理する。
- QR / import 導線の正式仕様を確定する。

## 将来予定

- テーマ別追加条件提案
- Planner 支援の強化
- 補正エージェント強化
- 録音品質レビュー支援
- UI からのパック構造編集
- 外部公開・権限・監査ログ
