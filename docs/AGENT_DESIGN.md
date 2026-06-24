# Sokqa Studio Agent Design

## Planner Agent

## 役割

学習パック全体の CoursePlan を作成する。

Planner はパック全体の構成、document outline、quiz outline、metadata、TTS 読み方方針候補を決める。

## 入力

- theme
- targetUser
- difficulty
- scale
- language
- customInstructions
- sourceText / sourceMode
- structurePolicy
- generationUnit
- materialMode
- docCount / quizCount / questionCount
- documentCount / sectionsPerDocument
- quizPacks
- includeTts / enableTtsOptimize / ttsReadingMode
- userTtsRules
- globalTags / description 関連設定
- answerPositionMode

## 出力

- CoursePlan
- documents
- quizPacks
- ttsRules
- proposedReadingPatterns
- selectedReadingPatternIds
- globalTags

## Document Generator

## 役割

CoursePlan と PlanDocument から SokqaDocumentPack を生成する。

Gemini provider が有効な場合は Gemini で生成し、mock provider の場合は deterministic mock content を生成する。

`materialMode=strict` では元資料を LLM で書き換えず、段落単位で document item に格納する。

## 入力

- CoursePlan
- PlanDocument
- sourceText
- customInstructions
- document model

## 出力

- SokqaDocumentPack
- document items
- globalTags

## Quiz Generator

## 役割

CoursePlan、PlanQuizPack、生成済み document packs から SokqaQuizPack を生成する。

quiz は document 本文を根拠に作る。選択肢は4つで、`answerPositionMode=balanced` の場合は正答位置を分散する。

## 入力

- CoursePlan
- PlanQuizPack
- source document packs
- quiz model

## 出力

- SokqaQuizPack
- questions
- choices
- answerIndex
- explanation
- globalTags

## TTS Optimizer

## 役割

display text を変更せず、TTS 専用フィールドを追加または更新する。

TTS Optimizer は品質チェックを代替するものではない。生成時点で読み上げ品質問題を減らすための前処理であり、リリース前の品質保証は TTS Quality Checker と TTS Quality Fixer で別途行う。

TTS 品質保証は三段構えで扱う。

```text
TTS Optimizer
↓
TTS Quality Checker
↓
TTS Quality Fixer
```

## 入力

- GeneratedFile list
- ttsRules
- ttsReadingMode
- ttsLanguageSettings

## 出力

- TTS fields 付き GeneratedFile list
- TtsReport

## rule

辞書置換のみで TTS text を作る。

特徴:

- 高速
- LLM コストなし
- 変換範囲は辞書とローカルルールに限定される

## llm

Gemini で読み上げ用テキストを生成し、辞書補正とガードを通す。

特徴:

- 文脈に応じた自然な読みを生成しやすい
- 言語タグは許可しない
- 予期しない文字体系が混入した場合は fallback する

## multilingual

Gemini で多言語 TTS text を生成し、言語切替タグを許可する。

特徴:

- `[ja-JP]` などのタグを TTS field に付与する
- パック言語を default language として扱う
- display text は変更しない

## Text Quality Checker

## 役割

display text の品質問題を検出する。

対象:

- factual
- style
- leak

役割:

- 本文、問題文、選択肢、解説文の品質問題を検出する
- TTS 読み問題は扱わない

## TTS Quality Checker

## 役割

TTS text の品質問題を検出する。

対象:

- reading
- notation
- double_utterance
- tts_text_mismatch

役割:

- TTS text の読み、重複、表記、意味不一致を検出する
- audioPath / audioUrl の null は通常状態として無視する
- すでに tts field に反映済みの読み指摘は filter する

## Text Quality Fixer

## 役割

Text Quality Checker の指摘のうち、display text 変更を伴う修正候補を作る。

- factual、style、leak に対する修正候補を pendingFix として作る
- 自動では display text を変更しない
- 承認制である
- 承認後、本文変更と TTS reset を行う

入力:

- target
- quality issues
- maxFixes

出力:

- pendingFixes
- updatedJson
- apply response

## TTS Quality Fixer

役割:

- reading、double_utterance、notation、tts_text_mismatch に対して TTS field を修正する
- 自動適用可能な範囲だけを適用する
- display text は変更しない
- 該当 audio field を clear し、再録音対象にする

入力:

- target
- quality issues
- maxFixes

出力:

- appliedFixes
- unappliedFixes
- updatedJson
- reRecordNeededUnits

## Revision Committer

明示的な Agent ではないが、各 Agent の成果物を保存可能な revision に変換する中核サービスである。

役割:

- changed / added / removed files を manifest items に反映する
- revision、versionId、fileVersionId、contentHash を採番する
- latest.json を更新する
- recording / quality fix / tts fix の change log を manifest に残す

## 将来追加予定

## テーマ別追加条件提案

Planner がテーマ、対象ユーザー、sourceText から「追加すると良い条件」を提案する。提案はユーザー承認後のみ CoursePlan または customInstructions に適用する。

例:

- 試験対策なら出題範囲、用語粒度、過去問風クイズ
- 語学教材なら学習言語、母語、発音練習、例文量
- 社内資料なら禁止事項、固有名詞、機密情報チェック

## 多言語学習支援

pack language と `learningLanguage` を意識して、本文、問題、選択肢、解説、TTS タグの生成方針を支援する。

Planner はテーマから学習言語を推測し、ユーザー指定があればそれを優先する。Quiz Generator は quiz pack ごとの
`choiceLanguageMode`（pack / learning / auto）に従い、auto でも1問内の4択は同じ言語に統一する。

## JLPT支援

日本語学習教材向けに、難易度に応じた表記制御を支援する Agent。

検討項目:

- 漢字制御
- ひらがな優先
- ふりがな

## Planner支援

ユーザーが CoursePlan を確認する前に、章構成、問題数、TTS mode、source material mode の妥当性をレビューする Agent。

## 補正エージェント強化

- display text と TTS text の差分説明
- 修正前後の録音影響見積もり
- multilingual tag の自動検証
- source material 逸脱チェック
- quiz の根拠 document 紐付け強化
