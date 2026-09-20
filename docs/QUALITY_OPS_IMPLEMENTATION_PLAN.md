# Sokqa Studio 品質・運用・Jev 統合 実装計画書（マスタープラン）

作成: 2026-09-21 / 状態: Phase 0〜2 実装済み（ブランチ `feat/packops-cli-registry`・§10 参照）。Phase 3 以降は承認待ち。

本書は以下の合意に基づくマスター計画書である。
- Jev（Typesafe `jev-1.13.0`）の技術詳細・横置き設計・観測ログスキーマは **docs/typesafe-integration-plan.md が正本**。本書はそれを作り直さず、段階①〜③への参照のみとする。
- IP クリアランス（独自性・法的安全性）とルーブリック採点は本書の新規範囲。
- チャット（エージェント）からの生成・検証・配置は Gemini 消費ゼロの導線として正式化する。

## 0. 設計上不変の前提（確定事項）

1. **R2 が唯一の正典**。ローカルは読み取りスナップショットのみで、双方向同期を作らない（衝突源の排除）
2. **単一口径**: 生成経路はサイト（HTTP API）とチャット（CLI/スクリプト）の両方から、同じ `app/services`（validator・import・quality）を呼ぶ。裏口の検証素通り経路を作らない
3. **機械検証と品質判定と保存ゲートは別物**（typesafe 計画 §1 と同じ）: `validation.valid`（technical ゲート）／ `qualityStatus` ／ Jev・Gemini 判定 ／ 採点スコア を1つの合格スコアと解釈しない
4. 法的判定は自動断定しない。機械チェックは「公開可/要注意/差止推奨」の所見＋人間最終判断とする
5. 既存 Gemini 経路（生成5系統・品質3判定）は Jev 採用決定まで**一切削減しない**

## 1. 層構成と実行順序

| 層 | 内容 | Jev 導入との関係 |
| --- | --- | --- |
| L1 データ層 | 機械検証強化・出典台帳・採点スキーマ・スナップショット | 独立。即実行可 |
| L2 運用層 | チャット/CLI 生成・検証・配置ループ、registry スナップショット | 独立。即実行可 |
| L3 表層・判定層 | Jev 横置き・採点の実運用・UI 公開ゲート | typesafe 計画の段階①→②→③に同期 |

## 2. Phase 0: チャット運用ループの正規化（L2・Gemini 消費ゼロ）

現状 `tmp/import_pm_zeroichi_to_r2.py` のような都度スクリプトを定型化する。

- `packs/<slug>/` を**教材ドラフト源（入力専用ワークスペース）**として確定（doc/quiz/v1 manifest/sources.json/HANDOFF.md を同梱し Git 追跡。pm_zeroichi_v1 がすでにこの形。現状の正典は R2）。HANDOFF 内の「このディレクトリが正本」表記は配置後の実態とズレるため順次「ドラフト源」へ改める
- 共通 CLI を追加: `scripts/packops.py`（または `python -m app.cli`）。サブコマンド:
  - `validate <slug>`: `validate_files` を機械検証のみで実行（LLM なし・無料）
  - `pull <slug>`: R2 の latest 対応オブジェクトを packs/<slug>/ に還流する（**再 import 直前の必須手順**。サイト側の品質修正・録音がローカル旧ドラフトの再 import で overwrite される事故を防ぐ）
  - `import <slug> --creator <id> [--content <id>]`: packs/ の JSON を読み、v1 manifest を PackManifestV2 互換へ補完して `import_pack_files` を呼ぶ（現行 tmp スクリプトの一般化。destination new/existing 自動判定）。import 時に配置版の versionId を pack 側 manifest に記録し、`pull`/`import` で R2 latest と突き合わせて乖離時は pull 必須とする
  - `snapshot`: §4 の registry 同期を実行
  - `quality-check <slug> --mode text|tts --file <name>`: 既存 `/quality/*` サービス関数を直接呼ぶ。**明示実行時のみ Gemini を消費**（ファイル単位・任意実行でコスト抑制）
- チャットからの指示例: 「pm_zeroichi_v1 の quiz を検→配置」→ `validate` → `import`。サイト UI と同じ検証を素通りしない導線が同じ service 関数に収束する
- 検証: import は既存テスト（test_pack_importer 系）を踏襲。新規 CLI は薄いラッパーとし、`import` 経由の R2 配置が TestClient テストで再現可能であることを1本追加

## 3. Phase 1: 機械検証の公開ゲート化（L1・無料）

- プレースホルダー検出（現 warning/quality）に**公開向けerror 化フラグ**を追加: 保存は現状どおり許可し、`snapshot`/レポート生成時に「公開不可」表示へ変換する（保存ゲート仕様は変更しない＝ §0-3 の区別を守る）
- tts_rules 登録フロー: 実測された誤読（「担います→かついます」「後→ごと」等）をパック単位でなく**システムルールへ集約**する手順を HANDOFF 系文書に追記（コード変更不要・運用ルール化）
- 出典台帳 `packs/<slug>/sources.json` スキーマ v0（Phase 4 の前提）:

```json
{
  "schemaVersion": 1,
  "sources": [
    {"name": "Scrum Guide 2020", "url": "...", "license": "CC-BY-SA-4.0",
     "usage": "facts_paraphrase", "verbatimAllowed": false, "attributionRequired": true}
  ],
  "generatedBy": {"llm": "gemini-2.5-flash", "draftedBy": "human|agent", "date": "2026-09-20"}
}
```

- 検証: スキーマは Pydantic で `app/schemas/pack_sources.py`。validate 時に同梱を warning として促す（存在必須にはしない。旧パック互換）

## 4. Phase 2: R2 状況把握スナップショット（L2・片方向 pull）

「完全同期」ではなく **manifest/latest の JSON のみを Git 追跡スナップショット**化する。音声・object 本体は対象外（体積の大半が MP3 のため）。

- `packs/registry/<creator>/<contentId>.json`: latest 1件＋manifest items（url・fileVersionId・qualityStatus・publicationStatus・revision 要約）
- `python scripts/packops.py snapshot` が全 creator を走査して書換。GitHub Actions workflow_dispatch または schedule で定期実行可
- 副次効果: Git 履歴が**監査ログ**（いつ誰が再生成・再配置したか）になり、「運営だけが全体を把握」要件と、公開判断（draft/published）のレビュー材料を同時に満たす
- 本番公開モデル: 公開自体は従来どおり `publicationStatus` と URL 差し替えで制御。アプリ側が qualityStatus を尊重する仕様かは convly-site 側確認済みとする（未確認なら確認タスクを立てる。Studio 単側でゲートを作らない）
- 衝突の根本原因は「編集入口が2つあること」であり、ローカル追跡の有無ではない。回避策は方向性の固定: **packs/<slug>/ はドラフト源（入力）、R2 が現状の正典（latest）**。data flow は draft→import→R2 と、import 直前の `draft←pull←R2` のみ。registry スナップショットは読み取り専用で絶対に送り戻さない
- 検証: snapshot の決定論性（同一 R2 内容→同一出力）、registry の git diff レポート

## 5. Phase 3: Jev 横置き導入（L3・typesafe 計画そのもの）

詳細は docs/typesafe-integration-plan.md に従い、段階①→②→③の順。本書での追記合意は次のとおり。

- 段階①（off/fake adapter・観測ログ）は**キー発行前に着手可**。実装ファイル一覧は typesafe 計画 §7 のスタブ表どおり
- shadow 有効化の前提条件（キー発行・データ送信許可・予算確定）は §9 チェックリストを尊重する
- **採点機構は段階②の成果物として接続する**: Jev の Score は較正前の分布期待値であり「正解率」ではない。スコア表示には必ず `score_schema_version` と信頼帯（機械検証＝確度・Jev 採点＝未較正・人間レビュー＝最終）を併記する
- 段階③（生成中レビューへの展開）は typesafe 計画 §6-③ の完了条件を満たすまで着手しない

## 6. Phase 4: 学習者就绪度スコア（readiness score v0）

「学習前にクオリティが分からない」問題への回答。100点満点は**3系統の入力を重み付き合成した相対指標**であり、Jev 単独・LLM 単独にしない。

| 項目 | 配点 | 入力 | 確度 |
| --- | --- | --- | --- |
| 機械検証（構造・placeholder・重复・answerIndex 分布・網羅） | 30 | validate_files / validate_pack.py | 確定 |
| メタデータ・包装（title/description/対象資格明記/tags/sources.json 同梱） | 10 | 新規 checklist | 確定 |
| 音声適性（難読み語のルール未登録数・文長・TTS 網羅） | 15 | TtsReport + tts_rules 突合 | 確定〜疑似 |
| 教育設計（伝聞調・leak・style・正答整合） | 25 | Gemini 品質 issues（既存）→ 段階②で Jev 併記 | 未較正 |
| 独自性・法的安全性 | 20 | Phase 5 の IP チェック結果 | 所見 |

- 出力: `packs/registry/<creator>/<content>.score.json`（snapshot 時に再生成）。バンド表示は「要レビュー(<70) / 公開可(70-89) / 推奨(90+)」だが、**閾値は v0 の暫定値として version 管理**し、後付けで緩めない（typesafe 計画 §6 と同じ原則）
- factual（内容の真偽）はスコアに含めない。根拠資料 (`source_evidence`) を渡せるパックだけの探索項目として分離する（typesafe 計画 §4 の制約と同じ）
- 検証: 既知の良品/欠陥パック（pm_zeroichi_v1＝警告2件入り、mock 破綻サンプル）でスコア再現テスト。スコア計算は決定的関数とし LM 呼び出しを分数化しない

## 7. Phase 5: IP クリアランス（L1 機械部＋所見）

- 層4（表記）: 商標・帰属・非公式明記の regex/field チェック（PMBOK®/PMP® 帰属表記の存在、「公式試験対策ではありません」表記、過去問風表現パターン）。`classification='ip'` の QualityIssue として既存レポートに合流
- 層2（逐語重複）: Scrum Guide 公式 PDF を `.deps/`（gitignore）に保持し、文字 n-gram（5-8 shingle）照合。閾値超で `ip` warning。PMBOK は原文入手不可のため「参照宣言のみ」で代替
- 層3（regurgitation）: 怪しい箇所の Web 検索照合は将来拡張（本書では枠だけ確保）
- CC BY-SA 注意: Scrum Guide 2020 は Attribution + ShareAlike。**表現の複製が substantial と判定された場合、パック全体のライセンス継承義務が生じ得る**ため、層2 のヒットは即「公開不可」ではなく人間法務判断へのエスカレーションとする
- 検証: 既知複製文を注入した fixture で検出率、正常教材での誤検知0確認

## 8. やらないこと（否定記録）

- ~~ローカル↔R2 双方向同期~~ → 片方向 pull スナップショットに置換（§4）
- ~~「スコア化しない」~~ → 撤回。就绪度スコアは導入。ただし LLM/Jev 単独スコアにしない・法的断定をスコアにしない（§6-7）
- ~~チャット生成経路の禁止~~ → 撤回。Gemini 消費ゼロの検証済み導線を正規化（§2）
- 作らない: 第二の manifest 正典 / pack-generator 前提の計画（HANDOFF §3 遵守）/ Jev への保存判断委譲（段階②完了まで）/ UI ダッシュボード新設（registry + CLI レポートで足りる。L3 表層は公開直前まで据え置き）

## 9. 着手順と承認ポイント

1. Phase 0+1+2（実装1回・外部依存なし・無料）→ 動作確認
2. typesafe 計画 段階①（off/fake）実装承認 → §7 のキーなしスタブ
3. Phase 5 層4（表記チェックのみ先行）→ pm_zeroichi_v1 に適用してレポート確認
4. キー発行後: shadow 有効化（明示承認）→ 段階② → Phase 4 スコア v0 接続 → Phase 5 層2

各段の完了条件は本書と typesafe 計画 §6 のうち**後から緩めない方**を優先する。

## 10. 実装状況（2026-09-21）

**完了（Phase 0〜2・外部依存なし・Gemini 消費ゼロ）**
- サービス層 `app/services/packops.py`: `validate_pack_dir`（公開ゲート `publishReady` 併記）/ `import_pack_dir`（v1→v2 補完・乖離検出・lock 書込・v1 manifest URL 還流）/ `pull_pack_dir`（R2 latest→ドラフト還流）/ `quality_check_pack_dir`（明示時のみ Gemini）/ `snapshot_registry`（片方向・決定論的）
- CLI `scripts/packops.py`: `validate` / `pull` / `import` / `snapshot` / `quality-check` サブコマンド
- 出典台帳スキーマ v0 `app/schemas/pack_sources.py`（不在は warning 促し・error 化しない）
- 公開ゲートは §0-3 を遵守: **保存は止めず** `publishReady`（placeholder 検出反映）として結果と registry に載せる
- registry `packs/registry/<creator>/<content>.json` を Git 追跡化（29 パック・`publishReadyBasis` で判定根拠を明示）
- テスト `tests/test_packops.py` 5本（validate/import 乖離/snapshot 決定論性）。既存 944本含め全通過
- pm_zeroichi_v1 を実データで `validate`→`pull`→`snapshot` 動作確認済み（〇〇2件のため `publishReady=false`）

**未着手（承認待ち）**
- Phase 3（Jev 横置き）: typesafe 計画 段階①のスタブから。キー発行前は off/fake のみ
- Phase 4（就绪度スコア v0）/ Phase 5（IP クリアランス 層4表記チェック→層2逐語重複）
- pm_zeroichi_v1 の `sources.json` 実データ: 実際の参照元確定（Fact チェックと一体）後にのみ記入。捏造しない
