# Sokqa Studio Specification

## パック生成仕様

Sokqa Studio の基本生成フローは二段階である。

1. `POST /plan-pack`
2. `POST /generate-pack`

`/plan-pack` は CoursePlan を返し、ユーザーが確認または編集した CoursePlan を `/generate-pack` に渡す。

## planner

入力:

- theme
- targetUser
- difficulty
- scale
- language
- customInstructions
- creatorId / creatorDisplayName
- contentId / slug
- includeTts / enableTtsOptimize
- ttsReadingMode
- ttsLanguageSettings
- structurePolicy
- generationUnit
- docCount / quizCount / questionCount
- materialMode
- documentCount / sectionsPerDocument
- quizPacks
- userTtsRules
- sourceText / sourceMode
- globalTags / description 関連設定
- answerPositionMode
- task model overrides

出力:

- CoursePlan
- documents
- quizPacks
- ttsRules
- proposedReadingPatterns
- selectedReadingPatternIds
- globalTags
- metadata

Planner は pack language に基づいて title、description、document titles、quiz labels を生成する。

## document generator

入力:

- CoursePlan
- PlanDocument
- sourceText
- customInstructions
- model

出力:

- SokqaDocumentPack

通常モードでは Gemini または mock generator が document JSON を生成する。

`materialMode=strict` かつ sourceText がある場合は、source material を LLM で書き換えず、段落を document item としてコピーする。1ファイルあたり最大 50 section、最大 50 document files に分割される。

## quiz generator

入力:

- CoursePlan
- PlanQuizPack
- 参照 document packs
- model

出力:

- SokqaQuizPack

quiz は document 本文を参照して生成される。選択肢は 4 件、answerIndex は 0-3。`answerPositionMode=balanced` の場合、正答位置を偏らないように並べ替える。

## パック構造

## metadata

CoursePlan 主なフィールド:

- id
- creatorId
- creatorDisplayName
- contentId
- slug
- shortTitle
- title
- description
- language
- targetUser
- difficulty
- scale
- author
- version
- structurePolicy
- generationUnit
- materialMode
- sourceMode
- globalTags

Manifest v2 主なフィールド:

- id
- type: `pack_manifest`
- schemaVersion: `1`
- contentId
- slug
- title
- description
- language
- author
- scale
- globalTags
- creator
- revision
- versionId
- sourceVersionId
- buildId
- generatedAt
- change
- items

## document

`SokqaDocumentPack`:

- id
- type: `document`
- schemaVersion
- title
- description
- language
- author
- assetBaseUrl
- globalTags
- documents

`documents[]`:

- id
- text
- tts
- tags

`tts`:

- text
- audioUrl
- audioPath
- textLanguage

## quiz

`SokqaQuizPack`:

- id
- type: `quiz`
- schemaVersion
- title
- description
- language
- author
- assetBaseUrl
- globalTags
- questions

`questions[]`:

- id
- question
- choices
- answerIndex
- explanation
- tags
- tts

`tts`:

- questionText
- choiceTexts
- answerText
- explanationText
- questionAudioUrl
- choiceAudioUrls
- explanationAudioUrl
- questionAudioPath
- choiceAudioPaths
- explanationAudioPath
- questionLanguage
- choicesLanguage
- answerLanguage
- explanationLanguage

## TTS仕様

TTS 最適化は display text を変更せず、TTS 専用フィールドに読み上げ用テキストを保存する。

## TTS Reading Mode

実装上の型は以下である。

- `none`
- `rule`
- `llm`
- `multilingual`

### rule

辞書ベースで TTS 用テキストを生成する。

辞書のマージ順:

```text
system dictionary -> user dictionary -> plan.ttsRules
```

後の辞書が前の辞書を上書きする。置換は長い `source` から適用する。

### llm

Gemini に読み上げ用テキスト生成を依頼し、その後に辞書補正とガード処理を行う。

`llm` では言語タグを出さない。タグが出た場合は除去される。

### multilingual

Gemini に多言語対応の読み上げ用テキスト生成を依頼し、必要な箇所に `[ja-JP]` などの言語タグを付与する。

言語タグは TTS 専用フィールドだけに保存する。display text は変更しない。

## 多言語設定

対象:

- document
- question
- choices
- explanation

実装フィールド:

- documentTextLanguageMode
- documentTextLanguage
- questionLanguageMode
- questionLanguage
- choicesLanguageMode
- choicesLanguage
- explanationLanguageMode
- explanationLanguage

Mode:

- `auto`
- `mixed`
- `select`

Language:

- `pack`
- `ja`
- `en`
- `ko`
- `zh`
- `id`
- その他 BCP-47-like code

正式値:

- `pack`
- `ja`
- `en`
- `ko`
- `zh`
- `id`

その他 BCP-47-like code は実装上受け付け可能だが、UI の選択肢として正式に扱うかは TODO とする。

多言語 UI デフォルト値:

- document: mode=`mixed`, language=`pack`
- question: mode=`mixed`, language=`pack`
- explanation: mode=`mixed`, language=`pack`
- choices: mode=`select`, language=`pack`

対応済み default speech language:

- ja -> ja-JP
- en -> en-US
- zh -> zh-CN
- ko -> ko-KR
- es -> es-ES
- fr -> fr-FR
- de -> de-DE
- it -> it-IT
- pt -> pt-PT
- id -> id-ID

## パック言語

パック言語はパック全体の基準言語であり、CoursePlan、document pack、quiz pack、manifest の `language` に保存される。

TTS ではパック言語を default language として扱う。

正式ルール:

- パック言語はデフォルト言語
- パック言語以外はタグ必須
- パック言語へ戻る場合のみ復帰タグ

例:

```json
{
  "language": "id"
}
```

この場合、説明文、解説文、問題文などの基本言語はインドネシア語になる。

## 学習言語

学習言語は、教材が学ばせたい対象言語である。

例:

- 日本語学習教材なら学習言語は日本語
- 英語話者向け日本語教材では、パック言語が `en`、学習言語が `ja` になり得る

現在の実装では、独立した `learningLanguage` フィールドは存在しない。学習言語は `ttsLanguageSettings` の各 field language によって表現する。

TODO: 学習言語を metadata として明示的に保存するか検討する。

検討中の例:

```json
{
  "language": "id",
  "learningLanguage": "ja"
}
```

## TTSタグ仕様

言語タグは TTS 専用である。

- パック言語はデフォルト言語
- パック言語で始まる場合は先頭タグ不要
- パック言語以外で始まる場合はタグ必須
- パック言語へ戻る場合のみ復帰タグ付与
- タグは TTS 専用
- display text は変更しない
- XML tag と closing tag は使用しない
- テキスト項目の末尾でパック言語へ戻すだけの復帰タグは不要
- 次の item は再びパック言語から始まるものとして扱う

タグ形式:

```text
[ja-JP]
[en-US]
[id-ID]
```

具体例:

パック言語が `id` で、日本語から始まる場合:

表示:

```text
ありがとう berarti terima kasih.
```

TTS:

```text
[ja-JP]ありがとう[id-ID] berarti terima kasih.
```

パック言語が `id` の場合:

```json
{
  "text": "Kata おはようございます berarti selamat pagi.",
  "tts": {
    "text": "Kata [ja-JP]おはようございます[id-ID] berarti selamat pagi."
  }
}
```

パック言語が `en` で、日本語から始まる TTS:

```json
{
  "question": "What does すみません mean?",
  "tts": {
    "questionText": "What does [ja-JP]すみません[en-US] mean?"
  }
}
```

パック言語で始まるため先頭タグ不要:

```json
{
  "text": "This phrase is [ja-JP]ありがとう[en-US] in Japanese."
}
```

パック言語以外で始まるため先頭タグ必須:

```json
{
  "text": "[ja-JP]ありがとう[en-US] means thank you."
}
```

display text は元のまま:

```json
{
  "question": "Apa arti ありがとう?",
  "tts": {
    "questionText": "Apa arti [ja-JP]ありがとう?"
  }
}
```

## choiceTexts の保持ルール

表示テキストと TTS テキストが同一文字列に見えても、パック言語以外の発音が必要な場合は `choiceTexts` を省略してはならない。

タグ付き TTS は保持する。

理由:

表示テキストと TTS テキストが同じ文字列でも、発音言語が異なる場合があるため。

例:

```json
{
  "choices": ["ありがとう", "こんにちは", "さようなら", "すみません"],
  "tts": {
    "choiceTexts": [
      "[ja-JP]ありがとう",
      "[ja-JP]こんにちは",
      "[ja-JP]さようなら",
      "[ja-JP]すみません"
    ]
  }
}
```

## Quality Checker仕様

Quality Checker は保存済みパックまたは manifest target を読み込み、Gemini または mock で品質問題を返す。

## Text Quality Checker

対象カテゴリ:

- factual
- style
- leak

目的:

- 事実誤りの疑い
- 学習者向け本文として不自然な表現
- 内部メモ、TODO、プロンプト残り、リークの検出

## TTS Quality Checker

対象カテゴリ:

- reading
- notation
- double_utterance
- tts_text_mismatch

目的:

- 読み間違いリスク
- 表記・読みの揺れ
- 二重発話
- display text と tts text の意味的な不一致

TTS mode では audioPath / audioUrl の null は通常状態として扱い、品質問題にしない。

## Quality Fixer仕様

## Text Quality Fixer

`/quality/text-fix` は factual、style、leak を対象に pendingFixes を生成する。

本文変更は自動適用しない。`/quality/text-fix/apply` で承認された pendingFix のみ反映する。

本文が変わる場合、該当 unit の tts は reset される。

## TTS Quality Fixer

`/quality/tts-fix` は LLM を使わず、reading、double_utterance、notation、tts_text_mismatch のうち安全に適用できる修正だけを tts fields に適用する。

`/quality/tts-fix/llm` は LLM 版の TTS fix を生成する。

TTS 修正は display text を変更しない。修正された TTS field に対応する audio path / audio URL は clear され、再録音対象になる。

## Revision仕様

Revision は `PackManifestV2` で管理する。

## revision採番

- 初回生成: revision `1`
- 既存 manifest からの変更: `current_manifest.revision + 1`

## 最新版管理

各 pack root に `latest.json` を保存する。

```text
{base}/creators/{creatorId}/packs/{contentId}/latest.json
```

`latest.json` は `PackLatestV2` 形式で、最新 versionId、revision、manifestUrl、assetBaseUrl、items を持つ。

## latest.json

`/packs` は基本的に latest.json を参照し、壊れている場合は versions の manifest から復旧する実装がある。

## versions

Manifest は以下に保存される。

```text
versions/{versionId}/manifest.json
```

Version ID は JST タイムスタンプ由来の形式である。

```text
vYYYYMMDD_HHMMSS
```

File version ID:

```text
fv_YYYYMMDD_HHMMSS_{kind}_{logicalId}_{random8}
```

Audio version ID:

```text
av_YYYYMMDD_HHMMSS_{stem}_{random8}
```

## Export仕様

## JSON

document、quiz、manifest は JSON として保存される。

## ZIP

`POST /packs/export-json-zip` は manifest と document / quiz JSON を ZIP として返す。

ZIP 内:

```text
manifest.json
{document file name}.json
{quiz file name}.json
```

## QR

Web UI は manifest URL または file URL を QR code として表示する。

TODO: QR を読み込む Sokqa アプリ側の URL scheme と import contract を正式化する。

## 現行実装との注意点

- README には `auto` TTS mode の説明があるが、現在の型定義では `TtsReadingMode` は `none/rule/llm/multilingual` である。
- `normalize_tts_reading_mode("auto")` は `llm` に正規化する。
- README の古い manifest shape 例と、現行の `PackManifestV2` は異なる。
- `learningLanguage` という独立フィールドは現状存在しない。
