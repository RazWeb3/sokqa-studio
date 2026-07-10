# Language Learning 責務分離計画（改訂版）

## 背景・前提の補正

元の計画書は「これから語学機能が入り始める」前提で組まれていたが、実態調査により
**語学機能は既に Planner / Prompt / Document / Quiz / TTS / Quality の各モジュールへ分散実装済み**であることが判明した。

したがって本計画は、以下の方針へ修正する。

1. **「追加」ではなく「既存実装の移設・集約」である**ことを明記する。
2. **Phase 0（責務棚卸し）を追加**し、影響範囲を把握してから着手する。
3. **Planner・Prompt・Document は一体として分離**する（目的関数は Planner → Prompt → Document の流れで決まるため、バラバラに分離すると不整合が生じる）。
4. 語学判定が**2系統**存在するため、それを1つの判定へ統合する設計を Phase 0 で決定する。

---

## 語学判定の2系統（統合対象）

| 系統 | 入口 | 意味 | 所在 |
|---|---|---|---|
| 系統A: 外国語学習 | `learningLanguage` あり かつ `learningLanguage != packLanguage` | pack 言語外の言語を学ぶ | `planner._is_language_learning_mode` / `prompts._compose_generation_purpose` |
| 系統B: 日本語学習 | `structurePolicy == "japanese_learning"` かつ `packLanguage != ja` | 日本語を学ぶ（JLPT等） | `prompts._is_japanese_learning_plan` / `japanese_learning` 構造ポリシー |

→ 両系統とも「Language Learning」とみなし、**1つの `is_language_learning(plan)` 判定へ統合**する。
分散したまま LanguageLearningStrategy へ移しても `if jlpt` 等が残存し、分離の意味が消失するため、統合は必須。

---

## Phase 0：責務棚卸し（実施済み対応表）

各処理を **共通 / Standard専用 / Language Learning専用** に分類。

| モジュール | 処理 | 分類 |
|---|---|---|
| pack_agent | `generate_pack` 本体（生成オーケストレーション） | 共通 |
| pack_agent | `revise_tts` | 共通 |
| Planner | `_is_language_learning_mode`（系統A） | **LL関連** → 統合判定へ |
| Planner | `infer_learning_language` | **LL関連**（LL専用か共通かは Phase 5 で判断） |
| Planner | `_language_learning_planner_objective` | **LL関連** |
| Planner | `_normal_planner_objective` 通常部分 | **Standard専用** |
| Prompt | `_is_japanese_learning_plan`（系統B） | **LL関連** → 統合判定へ |
| Prompt | `_japanese_learning_difficulty_block` | **LL関連** |
| Prompt | `japanese_learning` 構造ポリシー | **LL関連**（系統B） |
| Prompt | `_compose_generation_purpose` 通常分岐 | **Standard専用** |
| Prompt | `_compose_generation_purpose` 語学ガイダンス分岐 | **LL関連**（Phase 5 候補1 で `build_language_learning_purpose_lines` として移設済み） |
| Document | `learningLanguage` フィールド出力（normalize / mock / strict） | 共通（スキーマ処理・残置） |
| Quiz | `choiceLanguageMode` ルール | 共通（スキーマ処理・残置） |
| Quiz | `learningLanguage` フィールド出力 | 共通（スキーマ処理・残置） |
| TTS | `_effective_quiz_language_settings` | **LL関連**（Phase 7 で移設済み） |
| TTS | `build_document_language_settings`（document 用 LL 言語設定） | **LL関連**（Phase 5 候補3 で移設済み） |
| TTS | multilingual / 言語タグ処理 | 共通（Standard 側に残置・LL専用ではない） |
| Quality | `learningLanguage` / `choiceLanguageMode` 検証 | **LL関連**（Phase 6 で移設済み） |
| multilingual_detection | `learningLanguage` 参照（検出用） | 共通 |
| 全般 | JSON生成・Revision・Manifest・Storage・API・Import/Export・Recording・Pack管理・Version管理 | 共通 |

※ `CoursePlan.learningLanguage` / `PlanQuizPack.choiceLanguageMode` / `SokqaDocumentPack.learningLanguage` は
共通スキーマに残る語学依存フィールド。Phase 2 以降の入力として渡す形を維持する。

---

## Phase 1：Strategy 受け皿のみ（移設なし）【実施済み】

```
app/services/generation/
    __init__.py
    strategy.py            # GenerationStrategy ABC + resolve_generation_strategy
    strategies/
        __init__.py
        standard.py        # StandardStrategy: 既存 generate_pack を委譲
```

- Phase 1 時点では `resolve_generation_strategy` は常に `StandardStrategy` を返していたが、Phase 2 で統合判定（`is_language_learning_request`）を実装し、Language Learning と判定された場合のみ `LanguageLearningStrategy` を返すよう拡張済み。
- ルート `generate` は戦略解決経由へ切り替え（`app/routes/generate.py`）。
- 既存 `generate_pack` の本体・シグネチャは変更なし（既存テストは直接呼び出しのまま通る）。

---

## Phase 2：LanguageLearningStrategy 受け皿追加（Standard と同じ処理）

- `strategies/language_learning.py` を追加し、Phase 1 時点では StandardStrategy と同じ処理を呼ぶ。
- この段階で `resolve_generation_strategy` に「統合判定」を実装し、Language Learning と判定された場合のみ
  `LanguageLearningStrategy` を返すよう切り替える。
- 生成結果は変わらない（中身は同じ委譲）。

---

## Phase 3：Planner / Prompt の「明確に LL 関連と判断できた部分」移設【実施済み】

- Planner / Prompt のうち、明確に語学固有と判断できた LL 関連ユーティリティ群（言語学習判定・目的関数・日本語学習構造ポリシー等）を `generation/language_learning/{planner,prompt}.py` へ移設。
  - 具体名：`_is_language_learning_mode` / `infer_learning_language` / `_language_learning_planner_objective` / `_is_japanese_learning_plan` / `_japanese_learning_difficulty_block` / `japanese_learning` 構造・ruby ポリシー
- 元モジュールは互換委譲ラッパーとして残置（既存テスト互換のため）。
- 統合判定 `is_language_learning_request` / `is_language_learning_plan` を策定（系統A/B を1つに集約）。
- 注意：Prompt 内の `_compose_generation_purpose` など、境界領域にまたがるロジックは Phase 3 では移設せず、Phase 5 の設計判断候補として扱った。その後 Phase 5 候補1 により `build_language_learning_purpose_lines` として移設済み（純粋関数・内部ブロック委譲）。

---

## Phase 4：共通処理の再分類（Document / Quiz は移設対象なしと確定）

実装中の調査（grep および実装確認）では、Document / Quiz の `learningLanguage` / `choiceLanguageMode` は
「読んでコピーするだけ」の共通スキーマ処理（境界処理に近い）であり、現時点の調査範囲では明確な LL 専用責務は確認されなかった。

- Document Generator: `learningLanguage` 出力は共通扱いとし、Standard 側に残置。
- Quiz Generator: `choiceLanguageMode` / `learningLanguage` 出力は共通扱いとし、Standard 側に残置。
- したがって Document / Quiz の「LL 専用移設」は行わない。Phase 0 対応表の分類を「共通」へ修正済み。

---

## Phase 5：設計判断候補3箇所の最終分類と分離

実装・検証の結果、grep 単体では LL 専用か共通かの最終判断ができない「追加設計レビューが必要な候補」が
3箇所残っている。これらは現段階では**設計判断対象**であり、移設確定ではない。

### 入力
- 設計判断候補3箇所:
  1. `prompts.py` の `_compose_generation_purpose` 内 語学分岐・埋め込み（prompt生成専用ならLL／他で共用なら共通）
  2. `tts_optimizer.py` の `optimize_document_pack` 内 LL 専用 TTS 最適化（語学教材のみの最適化パスか／言語設定一般か）
  3. `planner.py` の 目的文以外での `infer_learning_language` 直接埋め込み（prompt生成専用ならLL／planner全体共通設定なら共通）

### 作業
- 各候補について「LL専用」「共通」「境界処理」のいずれかへ分類する。
- LL専用と判断されたもののみを `generation/language_learning/` 配下への移設対象とする。
- 共通・境界処理と判断されたものは Standard 側に残置し、その理由を本計画書へ記録する。

### 完了条件
- 設計判断候補3箇所について「LL専用」「共通」「境界処理」のいずれかへの分類が確定している。
- LL専用と判断されたもののみを `language_learning` 配下への移設対象とする。
- 共通・境界処理と判断されたものは、Standard 側に残置する理由が本計画書へ記録されている。

### 判定結果

#### 候補1：`prompts.py` の `_compose_generation_purpose` 内語学分岐【LL関連・移設済み】
- 責務：learningLanguage / packLanguage / structurePolicy から語学教材専用の生成方針を組み立てる。共通教材では不要。
- 実装：案A（内部ブロックのみ委譲）。`language_learning/prompt.build_language_learning_purpose_lines(plan) -> list[str]` を追加（副作用なし・純粋関数）。`prompts.py` は `.extend(...)` で結合。
- 既存テスト回帰なし、個別テスト3件追加済み。

#### 候補2：`planner.py` の目的文以外での `infer_learning_language` 直接埋め込み【移設なし】
| 行 | 判定 | 理由 |
|---|---|---|
| 473 | 共通（Planner入力構築） | `infer_learning_language()` を利用しているだけで責務は「Plannerプロンプトを完成させること」。LLロジックを実装しているわけではなく、呼び出し側の責務（プロンプト組み立て）は共通。 |
| 562 | 境界処理 | DTO（`choiceLanguageMode`）の既定値決定。Plan への値設定であり移設不要。 |
| 993 | 境界処理 | `CoursePlan.learningLanguage` の設定。スキーマ整形・DTO設定の責務であり移設不要。 |

- 依存ユーティリティが LL 関連でも、呼び出し側の責務（Planner 入力構築）まで LL になるとは限らない。3箇所とも Standard 側に残置。

#### 候補3：`tts_optimizer.py` の `optimize_document_pack` 内 LL 専用 TTS 最適化【一部LL関連・移設済み】
| 箇所 | 判定 | 対応 |
|---|---|---|
| `if pack.learningLanguage:` で `TtsLanguageSettings` を組み立てる部分（document） | **LL関連** | `language_learning/tts.build_document_language_settings(pack) -> TtsLanguageSettings | None` へ純粋関数として切り出し、`optimize_document_pack` は委譲（戻り値が None なら共通 language_settings を維持）。 |
| `_effective_quiz_language_settings(...)` 呼び出し（quiz） | **LL関連（委譲済み）** | Phase 7 で関数実体を移設済み。呼び出し行は残置で変更不要。 |
| `_gemini_*` / `_rule_*` への引数受け渡し（quiz） | **共通（境界処理）** | 下流関数への値伝播のみ。残置。 |

- 実装条件：候補1と同一（副作用なし・純粋関数・内部ブロックのみ委譲）。
- 既存テスト回帰なし、個別テスト2件追加済み。

---

## Phase 6：Quality Checker の LL 専用処理移設【実施済み】

- `_deterministic_tts_issues` を `generation/language_learning/quality.py` へ移設。元は委譲。
- 依存方向は `quality_checker → language_learning`（逆転なし）。未使用関数・import は削除。

---

## Phase 7：TTS 補助ロジック移設【実施済み】

- `_effective_quiz_language_settings`（クイズ用 TTS 言語設定の補助関数）を `generation/language_learning/tts.py` へ移設。元は委譲。
- `build_document_language_settings`（ドキュメント用 TTS 言語設定の純粋関数）も同モジュールへ追加（Phase 5 候補3 の LL関連 判定に基づく移設）。
- `tts_optimizer.optimize_document_pack` は `build_document_language_settings` を委譲し、戻り値が None なら共通 language_settings を維持（副作用なし）。
- 依存方向は `tts_optimizer → language_learning`（逆転なし）。

---

## Phase 8：Language Learning 実動作検証（検証フェーズ・未着手）

責務分離（Phase 0〜7）は完了済みだが、その時点では「実際に `learningLanguage` を含む入力で LL 経路が動くか」「第一報の各指摘が再現するか」は未検証。
本フェーズは**実装修正フェーズではなく検証フェーズ**として位置づけ、まず事実を確定する。

### 目的
- `learningLanguage` を持つ実入力で、Language Learning の設計どおりに Planner・Prompt・Document が動作することを確認する。
- 実生成物を用いて品質課題（第一報の指摘）を再現・切り分ける。

### 調査順序（再現先行）
1. **LanguageLearningStrategy の選択確認**
   - `resolve_generation_strategy()` が LL リクエストで本当に `LanguageLearningStrategy` を返すか（`strategy.py:69` 経路）。
   - 補足：`LanguageLearningStrategy` は Phase 2 設計どおり `generate_pack()` に委譲する「受け皿」であり、空であること自体は不具合ではない。
2. **Planner → Prompt の接続確認**
   - `_planner_objective`（`planner.py:423`）が LL 判定時 `_language_learning_planner_objective` を返すか。
   - `_compose_generation_purpose`（`prompts.py:238`）が `build_language_learning_purpose_lines` を `.extend` するか。
   - これらは共通経路（`create_course_plan` → `_planner_prompt` → ドキュメント生成）に乗っている。Strategy が委譲だけでも、入力に `learningLanguage` があれば LL ロジックは実行され得る。
3. **実際の Language Learning パック**
   - `learningLanguage` を持つ生成物で確認する。
   - `generated/it` 等の通常教材（`it` = Information Technology タグ、`language: ja`）は LL 判定対象外のため、根拠として使用しない。
   - `generated/` 全域では確認時点で `learningLanguage` フィールドを持つパックは存在しない（テストデータ・別ブランチ等の可能性あり）。「LL パックが存在しない」ことと「LL 機能が動かない」ことは同義ではない。
4. **その後に生成品質を見る**
   - 旅行英語パック等、実際に LL として生成された成果物で第一報内容を再検証する。
   - Step 4 の具体的確認項目（「何をもって語学教材と判断するか」の基準）:
     - document が「語学教材」として生成されているか（テーマ解説ではなく、学習言語フレーズが主役）。
     - 学習言語のフレーズが主役になっているか（pack 言語は補助説明のみか）。
     - planner の目的（場面ベースの表現習得）と整合しているか。
     - 第一報の各指摘（document 構成、placeholder、quiz 関連など）が再現するか。

### 完了条件
- `learningLanguage` を含む生成リクエストで Language Learning 経路を確認済み。
- Planner の目的関数と Prompt の Language Learning 指示が最終プロンプトに反映されていることを確認済み。
- 実生成物を用いて、第一報の各指摘（document 構成、placeholder、quiz 関連など）について「再現する」「再現しない」「未確認」のいずれかに分類済み。

### 検証記録（LLM 呼び出しなし・単体確認）
条件: `language=ja, learningLanguage=en, theme="海外旅行で使う英語", structurePolicy=listening` の最小リクエストで確認。
- [1] `resolve_generation_strategy()` は LL リクエストで `LanguageLearningStrategy` を返し、通常リクエストで `StandardStrategy` を返した。**OK**。
- [2] `_planner_objective()` は `_is_language_learning_mode=True` 時に `_language_learning_planner_objective` を選択（`_normal_planner_objective` とは不一致）。**OK**。
- [3] `_compose_generation_purpose()` は LL パックで `build_language_learning_purpose_lines` の出力（1行: 「学習対象言語そのものを本文の主役として…」）を最終プロンプトに `.extend` 反映。通常パックでは空配列で混入せず。**OK**。
- [4] 実生成品質を旅行英語 LL パック（`cnt_258a6fc05f` / `language=ja, learningLanguage=en, structurePolicy=listening`）で分類。**document・quiz 双方のスキーマに `learningLanguage:en` が正しく出力済み**。第一報各指摘の再現分類は以下：

  | 第一報指摘 | 分類 | 証拠 |
  |---|---|---|
  | ① document 導入が英語学習になっていない | **再現する** | doc_01/doc-1「皆さん、海外旅行へようこそ」が日本語メタ説明開始。第1フレーズ（doc-3）まで3段落消費。「短い導入→即フレーズ」に反する。 |
  | ② planner の goal が語学目的になっていない | **再現せず（逆に正常）** | `documents[].goal` は「〜英語表現を〜できるようになります」と場面ベース語学目的で正しく生成。目的関数は期待通り動作。 |
  | ③ 角括弧プレースホルダー禁止 | **再現する（quiz 本文）** | document 本文は具体名補完済みで角括弧無し。但し quiz の question/choices/explanation に `[国名]` `[氏名]` `[飲み物]` `[番号]` が残存（q-18〜q-22, q-29, range_02 q-1〜q-3）。 |
  | ④ 単語欠落（Wi-Fi 等） | **再現せず** | 本パック（空港・機内・入国・ホテル・レストラン・緊急）に Wi-Fi 文脈が存在せず欠落確認されず。継続課題自体は未否定。 |
  | ⑤ TTS タグ二重化・不整合 | **再現する（但し設計上の課題）** | document `tts.text` に `[en-US]...[ja-JP]` 混入（doc-3 等）。`_document_quality_rules_block` の「タグは本文に含めない」に違反。quiz tts にも混入。 |
  | ⑥ quiz choiceLanguageMode 不整合（再発） | **再現せず** | range_01(pack): question 日本語/choices 英語＝正常。range_02(learning): question 日本語/choices 英語＝正常。分離後も正しく動作。 |
  | ⑦ choiceTexts 省略未達（再発） | **再現せず** | multilingual タグ付きで読み上書きが必要なため choiceTexts 保持は正しい挙動。第一報の「省略できるのに省略していない」は別フィールド混同。 |

### 検証で判明した設計上の留意点（Phase 9 以降の入力）
- LL 経路は **Strategy が `generate_pack()` に委譲していても、共通経路内の planner/prompt 分岐経由で正常に動作する**。空の Strategy シェルは不具合ではない（Phase 2 設計どおり）。
- `build_language_learning_purpose_lines` は「学習言語を主役に」とだけ指示し、「短い導入→即・学習言語フレーズ→短い解説」の構成を**強制していない**。実生成で ① が再現したため、これは document 課題の**有力な原因候補**（確定ではない）。
- ⑤ の TTS タグ混入は、**本文生成プロンプトが「タグは後続TTS最適化の責務」と指示しているのに生成モデルが本文へ書き込む**という、purpose_lines 指示と実挙の乖離。quality ルール（`_document_quality_rules_block` / `_quiz_quality_rules_block`）の「タグを本文に含めない」も効いていない。Phase 9 で prompt 強化の候補。

### 分類時の注意（断定の禁止）
- LLM の生成品質はプロンプト全体（purpose_lines / planner_objective / 日本語学習ブロック / difficulty / structurePolicy / CoursePlan）で決まる。
- 従って単一関数（例: `build_language_learning_purpose_lines` が「学習言語を主役に」としか指示していない点）をもって、第一報の document 課題の「原因そのもの」と断定してはならない。あくまで**原因候補の一つ**として扱う。
- 課題の修正（Phase 9 以降）は、上記分類で「再現する」と確定したもの（① document 構成・③ quiz 角括弧プレースホルダー・⑤ TTS タグ混入）のみを対象とし、再現せず・未確認のものは修正しない。

---

## Phase 9：実生成物に基づく教材品質改善（再現確定分のみ・過剰実装禁止）

Phase 8 で分類した「再現する」指摘（① document 構成・③ quiz 角括弧プレースホルダー・⑤ TTS タグ混入）を
実生成物 `cnt_258a6fc05f`（`language=ja, learningLanguage=en, structurePolicy=listening`）で再確認し、
修正対象を絞り込んだ。

### 開始前調査（修正前に実施）

#### 調査①：⑤ TTS タグ混入の原因段階
- 実生成物で `[en-US]...[ja-JP]` を grep。全文書・quiz 合計 478 件。
- 全件が `documents[].tts.text` / `question.tts.*`（`questionText` / `choiceTexts` / `explanationText`）のみに存在。
- document/quiz の**学習者向け本文**（text / question / choices / explanation）には 1 件も混入していない。
- 原因: `language_learning/tts.build_document_language_settings` が `documentTextLanguageMode="mixed"` を返す
  → `optimize_document_pack` が multilingual モード（`allow_language_tags=True`）で実行 → Gemini が正常にタグ付与。
- 判定: tts フィールドのタグは multilingual 読み上げの**正常な出力**。本文への混入は**未発生**。
  Phase 8 の「本文に混入」は tts フィールドと本文の混同の疑い。
- **結論（実装前時点）: ⑤ は「再現せず（誤認）」へ再分類。Phase 9 の修正対象外。**

#### 調査①の訂正（実装後データ cnt_17bde2e928 で判明）
- 実装後の生成データ `cnt_17bde2e928` を確認した結果、**document 本文(text)へのタグ混入が 249 件 / 6 ファイル全てで発生**（doc_02/doc_04/doc_06 等）。
- `optimize_document_pack`（`tts_optimizer.py:1700-1727`）は `item.text` を変更せず `item.tts` のみ書き換える。→ 本文(text)の混入は **document_generator の生成段階** で発生。TTS 最適化は無関係。
- 旧データ cnt_258a6fc05f（実装前）は本文無混入だった。両者の唯一の差分は **Phase 9 Task 1 の追記（purpose_lines 行2）**。
- 行1は元から「言語タグ([en-US]等)は本文に含めない」と禁止。行2が「学習言語のフレーズを**主役として提示**」と強調した結果、モデルが「主役＝言語タグ付きで強調」と解釈し、禁止指示に反して混入したと推測。
- 不安定さ（doc_01/03/05 は無混入、doc_02/04/06 は混入）はモデルの非決定性と競合指示の相互作用。
- `language_settings`（mixed モード）は tts_optimizer のみに渡り document_generator には渡らないため、mixed 設定の漏れではない。
- **結論（訂正）: ⑤ 本文混入は Phase 9 Task 1 の追記が誘発した副作用。タスク分割へ追加。**

#### 調査②：① document 構成の原因（プロンプト不足か）
- planner `_language_learning_planner_objective`（`language_learning/planner.py`）は正常。
  「学習場面ベースの表現習得」「章タイトルは利用場面」「goal は表現を使える形」を指示。② 再現せずの根拠と一致。
- `build_language_learning_purpose_lines`（`language_learning/prompt.py`）は「学習言語を主役に」とだけ指示し、
  各 `documents[]` セクションの**構成（短い導入→即フレーズ→短い解説）を強制していない**。
- 実生成物: 各 doc が日本語メタ説明から始まり、第1フレーズまで2〜3段落消費（例: `cnt_258a6fc05f_doc_01` doc-1〜doc-3）。
- **結論: ① は planner の問題ではなく、purpose_lines の「構成強制不足」が原因。planner 微修正は不要。**

#### 調査③：③ placeholder の実態（Phase 8 記録との乖離）
- Phase 8 は「document 本文は角括弧無し」と記録していたが、実態は `cnt_258a6fc05f_doc_03` 本文に
  `[国名]` `[都市名]` `[数量]` `[品物]` が残存（doc-3「[国名]」、doc-9「[都市名]」、doc-24/25「[数量]/[品物]」）。
- quiz 本文にも `[国名]` `[氏名]` `[飲み物]` `[番号]` が残存（range_01/range_02）。
- **結論: 同種課題のため ③ の修正対象を「quiz 本文のみ」から「document 本文 + quiz 本文 の両方」へ拡張。**

### Phase 9 対象（確定）
| 項目 | 対象 | 根拠 |
|---|---|---|
| ① document 構成 | 〇 | 各 doc が日本語メタ説明から始まり、第1フレーズまで2〜3段落。構成強制不足。 |
| ② planner goal | × | 再現せず（正常）。Phase 8 通り対象外。 |
| ③ 角括弧プレースホルダー | 〇（document + quiz 両本文） | doc_03 本文 `[国名][都市名][数量][品物]`、quiz 本文 `[国名][氏名][飲み物][番号]` が残存。 |
| ④ Wi-Fi 欠落 | × | 再現せず。Phase 8 通り対象外。 |
| ⑤ TTS タグ混入 | 〇（本文混入のみ） | 実装後の cnt_17bde2e928 で document 本文(text)に 249 件混入。tts フィールドのタグは正常出力だが、本文への混入は Task 1 追記が誘発した副作用（調査①訂正）。 |
| ⑥ choiceLanguageMode | × | 再現せず。Phase 8 通り対象外。 |
| ⑦ choiceTexts | × | 再現せず。Phase 8 通り対象外。 |

### タスク分割
- **Task 1（① document 構成）**: `build_language_learning_purpose_lines` へ「各 documents[] セクションは
  （短い導入 → 即・学習言語フレーズを主役として提示 → 短い解説）の構成を強制」を追記。planner 側は変更しない。
- **Task 2（③ placeholder）**: `_document_quality_rules_block` と `_quiz_quality_rules_block` のプレースホルダー禁止に
  「角括弧で囲んだ汎用ラベル（[国名][都市名][数量][品物][氏名][飲み物][番号] 等）も禁止」を明記し、具体名必須化。
  （既存ルールの「square-bracket placeholders / generic name labels」を LL 教材向けに補強。新規関数は作らない。）
- **Task 3（⑤ TTS タグ）**: 実装なし。対象外判定を本欄に記録するのみ。
- **Task 4（⑤ 本文混入・Task 1 誘発副作用）**: `build_language_learning_purpose_lines` 行2の「主役として提示」を
  「**タグ無しの素のテキストで**、かつ言語タグ([en-US]等)を付けずに学習言語フレーズを提示」と再明記し、
  行1の「タグを本文に含めない」をより強調する。構成強制（短い導入→即フレーズ→短い解説）は維持。
  新規関数は作らない（既存 2 行の書き直しのみ）。
- **Task 5（document 本文の直引用符 `"` 囲み）**: `cnt_17bde2e928_doc_05` 等で英語フレーズが `"Hi, how can I help you?"` のように
  直引用符 `"` で囲まれている。プロンプトに `"` 囲みの指示は無く、モデルが英語フレーズを強調するための自成挙動。
  `quality_checker._UNSPOKEN_READING_SYMBOLS` に `"` は含まれないため、TTS 読み上げ時に「ダブルクオーテーション」と発話される実害あり。
  `build_language_learning_purpose_lines` に「英語フレーズは引用符（`"` や「」）で囲まず、そのままの形で書く」を追記。
  新規関数は作らない（既存 2 行の書き直しのみ）。

### 完了条件
- `build_language_learning_purpose_lines` が「短い導入→即フレーズ→短い解説」の構成を含むこと（単体テストで確認）。
- document/quiz の両 quality ブロックが角括弧プレースホルダー禁止を含むこと（単体テストで確認）。
- `build_language_learning_purpose_lines` が「言語タグ([en-US]等)を**本文に含めない**」を重複なく明記し、かつ
  「タグ無しの素のテキスト」で学習言語フレーズを提示する指示を含むこと（単体テストで確認）。
- `build_language_learning_purpose_lines` が「英語フレーズは引用符（`"` や「」）で囲まず、そのままの形で書く」を含むこと（単体テストで確認）。
- 既存テスト全件合格。通常教材（語学含まない）は挙動変化なし。

---

## 完了条件

- 通常教材: 既存テスト全件合格（語学含まないケースは完全一致）。
- 系統A/B は単一の Language Learning 判定へ統合されている。
- LL関連と判断された責務は `generation/language_learning` に集約されている。
- 共通責務・境界処理は Standard 側に残置する理由が本設計書に記録されている。
- Phase 5 の設計判断候補について、すべて責務分類（LL関連・共通・境界処理）が確定している。
- LL関連と判断された分岐のみが `generation/language_learning` 配下へ集約済み（共通スキーマ処理に見える `if learningLanguage:` 等は集約対象外）。
