# Sokqa Studio Specification

## 品質状態と操作権限

`qualityStatus`（`valid` / `warning` / `blocked`）は生成物の品質診断を表示するための情報であり、保存、録音、URL・QR表示、配布、公開を自動的に禁止しない。品質チェックは問題を通知し、最終判断は利用者が行う。

公開状態を扱う場合は、品質とは独立した`publicationStatus`（`draft` / `published`）で管理する。

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
`targetUser` は読者属性を表す。`difficulty` は教材レベルを表す。両者を同義に扱わない。

## difficulty の責務（深さの共通定義と Strategy ごとの具体化）

`difficulty` は「問題の深さ・認知負荷」を表し、教材ドメイン（語学・技術・資格等）を問わず共通の責務である。

共通層（quiz_generation_prompt）の定義:
- beginner: 基礎理解
- standard: 応用理解
- advanced: 深い判断

共通層は「深さ」のみを定義し、各ドメインでの具体的中身は書かない。

各 Strategy は共通層の「深さ」を当該ドメインの設問設計へ具体化する。Language Learning Strategy は
difficulty を具体化し、実装上は language_learning 側の difficulty block で提供する。
- beginner: 意味理解・基本対応（基本フレーズの意味、場面と表現の対応）
- standard: 場面適切性・使い分け（類似表現の選択、文脈に応じた表現選択）
- advanced: ニュアンス差・誤用修正・状況に応じた自然判断

`difficulty` から `choiceLanguageMode` への自動変換は行わない。`choiceLanguageMode` は計画側の設定のまま。

## テーマ別追加条件提案

入力:

- theme
- targetUser
- difficulty
- language
- displayLanguage
- customInstructions
- sourceText の有無

出力:

- suggestions[].title
- suggestions[].reason
- suggestions[].text

AI提案は管理画面の表示言語で生成する。`title`、`reason`、`text` はすべて `displayLanguage` に合わせる。
`language` は教材のパック言語であり、提案の出力言語を決める値ではない。`learningLanguage` も提案言語には影響しない。
固定提案と Gemini 生成提案の両方に同じルールを適用する。

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

## 第2層: _speech_text の言語境界ガード

`_speech_text` は rule 置換・かな化・プレースホルダ沈黙などを適用する低層関数である。ここに「言語タグ境界による処理切り替え」のガード層（＝第2層）を設ける。

仕様:

- `[xx-YY]` タグで区切られたテキストは、タグ境界ごとに「デフォルト言語スパン」と「非デフォルト言語スパン」に分割する。
- デフォルト言語スパン（タグなし、または `[ja-JP]` 等のパック言語スパン）には、従来通り rule 置換・かな化・プレースホルダ沈黙を適用する。
- 非デフォルト言語スパン（`[en-US]` 等の非デフォルトタグ区間）には、rule 置換・かな化を一切適用せず、原文をそのまま保持する。プレースホルダ沈黙等の日本語依存補正もスキップする。
- タグが存在しない入力は全体をデフォルト言語スパンとして扱い、既存の rule 挙動を維持する。

目的:

- 多言語スパンへ rule 辞書や読み補正ロジックが及ぶ回帰を防ぐ。
- この切り替えは `_rules_for_mode(multilingual)` がルールを空配列にする仕組み（偶然の非適用）とは独立した防御層である。どちらの仕組みも単独で存在し得るため、第2層は multilingual 以外のモードから rule が渡されてもタグ内を保護する。

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

## choicesLanguage

`choicesLanguage` は `tts` フィールドの一つで、選択肢全体の読み上げ言語を表す。

### 付与ルール

- `choices` の言語が **パック言語と異なる場合** は `choicesLanguage` を付与する。
- `choices` の言語が **パック言語と同じ場合** は `choicesLanguage` を付与しない。

判定基準は「**選択肢の言語 ≠ パック言語**」であり、`learning` モードや `pack` モードかどうかではない。

### ロケールコード

`choicesLanguage` には既存の Default Speech Language のロケールコードを使用する。

例:

```json
"choicesLanguage": "en-US"
```

```json
"choicesLanguage": "ja-JP"
```

### choiceTexts との関係

`choicesLanguage` は、`choiceTexts` による読み上げ・`choices` へのフォールバックのいずれにも適用される。

フォールバックは `choiceTexts` の省略だけでなく、各要素が `null`、空文字、空白のみの場合にも適用される。

つまり、`choicesLanguage` は `choiceTexts` の有無とは独立した **選択肢全体の読み上げ言語** を表すフィールドである。

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

`learningLanguage` は CoursePlan、document pack、quiz pack に optional metadata として保存する。
新規生成では `learningLanguage` を正規ルートとして扱い、旧 `ttsLanguageSettings` は後方互換用のフォールバックとして維持する。

## Language Learning 生成後検証（validator）

語学教材（learningLanguage あり）の生成物は、プロンプト指示の遵守を生成後の機械検証で担保する。
検出された場合は「品質チェックの減点」ではなく「使えない教材＝生成失敗」として扱う（error カテゴリ）。
以下の各 validator は生成パイプライン末尾の `validate_files` で実行され、検出結果はレスポンスの `validation` フィールドに含まれる。

### 1. 学習言語欠落検証（document）

目的: 学習言語が完全欠落したパック言語のみの教材（「説明教材」）を検出する。

背景: 生成目的文（`build_language_learning_purpose_lines`）が「学習言語を本文の主役として提示（短い導入→即・学習言語フレーズ→解説）」を指示するが、LLM が構造制約を逸脱しパック言語のみの「説明教材」を生成する再発がある。プロンプト指示の遵守を保証するため、生成後の validator で学習言語スクリプトの存在を機械検証する。

注意: 本検証は教材品質の完全保証ではなく、明らかな学習言語欠落の検出を目的とする。学習言語スクリプトが 1 文字でも混入していれば通過する（例: `ABC` という社名や `I am here.` のみの短文が含まれる場合も通過する）。「英語教材として成立しているか」の判定ではなく、「学習言語文字が混入しているか」の欠落検査である。

- 検出条件: `learningLanguage` と `language`（pack）が**異なるスクリプト**（例: pack=ja / learning=en）の場合、document の**各セクション `text`（documents[] 要素単位）** に学習言語スクリプト（latin / cjk / hangul 等）が 1 文字も含まれていなければ error とする。
- 判定単位: **セクション単位**。導入文のみのセクションも含め、各セクション独立に判定する（導入直後に学習言語フレーズを置く構成を前提とするため、導入セクションのみ日本語という構成は逸脱として検出対象）。
- 対象フィールド: `documents[].text`
- スキップ条件（3 分岐）:
  - `learningLanguage` 未指定 → スキップ（明快）
  - `learningLanguage == packLanguage`（同一言語）→ スキップ
  - `learningLanguage != packLanguage` だが**同一スクリプト**（例: pack=en / learning=fr、いずれも latin）→ スキップ
    - **初期対応範囲の制約**: 本検証は異なるスクリプト間のみを対象とする。同一スクリプト間の言語ペア（en↔fr 等）は検証対象外。「英語教材なのにフランス語になった」等の同一スクリプト間の言語品質判定は初期対応範囲外。将来ラテン文字同士のペアを追加する場合は、スクリプト差に依存しない判定（言語ベースの語彙照合等）への拡張が必要。

### 2. プレースホルダ残存検証（document / quiz）

- 検出条件: learner-facing テキストに未解決のプレースホルダ（〇〇 / △△ / 株式会社〇〇 等の伏せ字、[国名] / [氏名] 等の角括弧ラベル、ASCII プレースホルダトークン）が残存する場合は error とする。
- 対象フィールド:
  - document: `documents[].text`、`documents[].tts.text`
  - quiz: `questions[].question`、`questions[].choices[]`、`questions[].explanation`
- スキップ条件（非検出）: 連続 3 文字以上のアンダースコア（`___` または `＿＿＿`）のみからなる意図的穴埋め練習形式は非検出とする。それ以外の空白・伏せ字・角括弧ラベルは検出対象。
  - 注意: 穴埋めの許可判定はテキスト形状（連続 `_`/`＿` 3 文字以上）に基づく。データ構造（quiz の fill_blank 型指定等）での厳密な区分は現状未実装。通常説明文中の空白プレースホルダと意図的穴埋めの完全分離は validator 単体では不可能であり、形状ベースの判定で許容する。

### 失敗時挙動（共通）

- パイプラインは例外停止しない。`validation.valid=False` をレスポンスに含めて `status="completed"` で返却し、生成失敗としてマークする。
- `validate_files` 検出後、`repair_files` を 1 回試行するが、上記いずれの error も LLM なしでは修復不可能（英語フレーズの注入や伏せ字の具体名解決は別生成が必要）なため実質無効。自動再生成は走らない。
- `repair_files` の対象外 error（non-repairable）:
  - `LANGUAGE_PHRASE_MISSING`（学習言語欠落）
  - `PLACEHOLDER_REMAINED`（プレースホルダ残存）
  - 理由: 修復には新規生成または意味判断が必要なため。これらは `repair_files` を通しても解消せず、完了レスポンスの `validation.valid=False` に維持される。
- 呼び出し側（フロントエンド / ジョブ管理）は `validation.valid=False` を合図として、該当ファイルを再生成または手動修正の対象とする。

quiz pack は `choiceLanguageMode` を持つ。

- `pack`: 4択をパック言語にする
- `learning`: 4択を学習言語にする
- `auto`: 問題単位で選ぶ。ただし1問内の4択は同じ言語に統一する

QuizPack ごとの選択肢言語 UI 表示条件:

- `ttsReadingMode=multilingual`
- `generationUnit` が `pack` または `quiz`
- QuizPack 数は plan 生成後の `quizPacks.length` ではなく、現在の生成設定から算出する

QuizPack 数の算出:

- `generationUnit=pack` は現在の scale と既定ルールから予定 QuizPack 数を出す
- `generationUnit=quiz` は `quizCount` を使う
- `generationUnit=document` は 0 件

配置:

- 多言語読み上げ設定エリア内で plan 作成前に編集する
- 構成レビューでは編集 UI を出さず、各 QuizPack の `choiceLanguageMode` を確認表示だけ行う

非表示:

- `generationUnit=document`

UI 初期値:

- `learningLanguage` がある新規 plan では各 `quizPack.choiceLanguageMode` の初期値を `learning` にする
- `learningLanguage` がない場合は `auto`
- 既存データの旧 `ttsLanguageSettings` は削除しない

Planner 入力:

- UI の事前選択値は `quizChoiceLanguageModes` として `/plan-pack` に渡し、生成された各 `PlanQuizPack.choiceLanguageMode` に反映する

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

`choiceTexts` は各選択肢ごとに判定する。

品質チェックは、生成時に解決済みの Strategy を受け取って dispatcher で分岐する。共通構造検証は全教材で実行し、内容品質チェックと修正候補は Standard または Language Learning のどちらか一方だけを実行する。保存済みファイルの `language` / `learningLanguage` から Language Learning を推測しない。

- 読み補正（かな等）が必要な選択肢は `choiceTexts` に保持する。
- 読み補正が不要な選択肢は `null` または省略相当（空文字等）としてよい。
- 発音言語のみ異なる場合は `choicesLanguage` により指定する。

表示テキストと TTS テキストが同じ文字列でも、発音言語は `choicesLanguage` により決定される。

例:
```json
{
  "choices": ["教室", "学校", "先生", "学生"],
  "tts": {
    "choicesLanguage": "ja-JP",
    "choiceTexts": [
      "きょうしつ",
      null,
      null,
      null
    ]
  }
}
```

### multilingual TTS モードでの choiceTexts 省略

`ttsReadingMode=multilingual` でも、`choiceLanguageMode=learning` かつ各選択肢が
`choicesLanguage` でそのまま読めて個別の読み補正（かな化等）が不要な場合は、choiceTexts を
省略（各要素 null または省略）してよい。

判定基準は「learningLanguage の有無」ではなく「各選択肢ごとの読み補正要否」である。
学習言語が仮名/ルビ補正を要するスクリプト（japanese/cjk/hangul: 漢字・ハングル読み等）の場合は
choiceTexts を保持する。ラテン・キリル・アラビア等は補正不要で省略可。

この省略判断は全教材共通の TTS optimizer の責務である。`choicesLanguage` があり、言語タグを除いた `choiceTexts` が `choices` と一致し、個別の読み補正がない場合だけ `choiceTexts` を省略する。1件でも個別補正または表示差があれば保持する。

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

## Language Learning 固有の構造チェック

語学教材固有の構造検証（学習言語含有・フレーズ接地・TTS網羅）は、共通 Quality Checker の
カテゴリ（factual/style/leak/reading/notation/double_utterance/tts_text_mismatch）には追加せず、
`generation/language_learning/quality.py` の `deterministic_tts_issues` 既存委譲経路へ新設する。

- ドキュメント: 学習言語含有（学習言語フレーズの存在）、TTS網羅（学習言語フレーズ含有ユニットは multilingual で TTS 必須）。
- クイズ: 選択肢言語混在（既存）＋問題が学習対象（フレーズ・表現・語彙・文型）に関連していること。
  章説明や教材メタ情報のみを問う問題（学習言語フレーズに全く接地していない問題）を検出する。
  応用・統合問題を弾かないよう、「フレーズが問題文にそのまま存在する」ではなく「学習対象に関連する問題であるか」で判定する。

新設カテゴリは `ll_structure`（Literal 型に追加）。共通カテゴリとは区別し、text モードの構造検証として実行する。

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
- `learningLanguage` がない既存パックでは、従来の `ttsLanguageSettings` を利用する。
