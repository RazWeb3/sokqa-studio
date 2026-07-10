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

## 完了条件

- 通常教材: 既存テスト全件合格（語学含まないケースは完全一致）。
- 系統A/B は単一の Language Learning 判定へ統合されている。
- LL関連と判断された責務は `generation/language_learning` に集約されている。
- 共通責務・境界処理は Standard 側に残置する理由が本設計書に記録されている。
- Phase 5 の設計判断候補について、すべて責務分類（LL関連・共通・境界処理）が確定している。
- LL関連と判断された分岐のみが `generation/language_learning` 配下へ集約済み（共通スキーマ処理に見える `if learningLanguage:` 等は集約対象外）。
