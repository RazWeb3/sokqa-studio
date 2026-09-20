# PM学習パック 初級「プロジェクト管理ゼロイチ」作業引き継ぎ

作成: 2026-09-20 / 状態: テストパック生成済み・**本人レビュー（Factチェック）途中**・R2配置済み（Dev: `sokqa_hackathon_dev` / revision 2 / 実機QRインポート確認済み）
このディレクトリ（`packs/pm_zeroichi_v1/`）が**ドラフト源（入力ワークスペース）**です。会話履歴に依存せず、この文書と同梱ファイルだけで再開できます。なお**配置後の現状の正典は R2 側**（latest pointer / pack_manifest v2）で、直近配置状態は `packops.lock.json` に記録する。R2 の変更をローカルに還流する場合は `python scripts/packops.py pull pm_zeroichi_v1`、再配置は `validate` → `import`（順序必須。乖離検出時は import 拒否）。詳細は docs/QUALITY_OPS_IMPLEMENTATION_PLAN.md §2/§4。

## 1. このパックの目的

- Sokqa 公式サイト（convly-site `public/sokqa/data/`）に未存在だったプロジェクト管理パックを1本作る（2026-09-19 棚卸しで重複なしを確認済み）
- 中断中の pack-generator が届かなかった「公式配布品質」の**ゴールデンサンプル**を、手作業＋機械検証＋人間レビューで実証する
- 3層構想（初級→中級: P-SM 3級/CAPM 対応→上級: PMP 状況判断型）の第1弾。**初級1本に集中し、同時並行で広げない**

## 2. ファイル一覧と役割

| ファイル | 内容 |
|---|---|
| `doc_pm_zeroichi_v1.json` | ドキュメント1本・44セクション（章1=1-15 PM概念／章2=16-30 プロセスグループと計画道具／章3=31-44 アジャイル・Scrum） |
| `quiz_pm_zeroichi_v1.json` | クイズ30問（各章10問対応・全問解説付き） |
| `pack_pm_zeroichi_manifest_01.json` | pack_manifest（URL一括インポート用。**2026-09-20 実URL（R2 cdn.convly.jp）へ更新済み**。再インポートで fileVersionId が変わるので要再更新） |
| `validate_pack.py` | 機械検証スクリプト。実行: `python validate_pack.py <このディレクトリ>` |
| `packops.lock.json` | 直近の R2 配置状態（creator/content/versionId）。packops CLI が読み書き。手編集しない |

形式は Sokqa Import JSON Profile v1（Vol.07 §44: `schemaVersion` 数値1、4択、`answerIndex` 0-3、doc は `<documentId>--<sectionId>`）。

## 3. 確定している設計判断（崩さないこと）

- **規模**: doc 合計40〜50セクション／quiz 30問（Obsidian vault `02-facts/sokqa/学習パック規模基準.md` が正本）
- **doc は1本統合**: 1セクション2行程度（81〜121文字）、全体で聞き流し約10分。分割の実益が薄いとの本人判断（v2 まで3本、v3 で1本）
- **対象資格の明記必須**: 「特定の資格試験対策ではない入門。次のステップは P-SM 3級・CAPM（中級）、PMP（上級）」を title・description・manifest に記載済み
- **creator 運用**: 検証は `sokqa_hackathon_dev`、公開合格で `sokqa_official`。JSON の author は当面 `Sokqa Studio Dev`（公開時に `Sokqa Team` への変更を検討）
- **自動化に頼らない**: `sokqa-pack-generator` / `sokqa-learning-pack-factory`（workspace 直下）は出力品質未達で開発中断中。**これらが動く前提の計画は禁止**。本パックの手順（AI下書き→機械検証→人間Factチェック）が正規ルート

## 4. 検証結果（2026-09-20 機械検証 OK）

- doc 44セクション、全セクション 81〜121 文字、id 重複なし、文字列内改行なし
- quiz 30問、choices すべて4件、answerIndex 分布 {0:6, 1:8, 2:8, 3:8}、3連続なし、全問 explanation あり
- manifest: type/kind/絶対URL 形式 OK（到達性は未検証）

## 5. レビュー基準（合否ライン・人間側判断）

1. 音声で聞いて初耳の用語でも、選択肢と解説だけで意味が取れる
2. 誤答 choice が初学者の実際に間違えそうな紛らわしさ（明らかに冗談な選択肢を混ぜない）
3. 解説は1〜2文、出典を要さない基礎事実のみ
4. 機械検証（上記コマンド）が通る
5. 上級向け（シナリオ判断・記述系）を初級に混ぜていない

## 6. やること（順番どおり）

- [ ] **本人 Fact チェック**: doc 44セクションと quiz 30問を確認。修正は直接 JSON へ（編集後必ず `python validate_pack.py .` 再実行、`packops.py import` で再配置）。※品質チェックが「〇〇」プレースホルダーを2件検出（doc 19番目セクション／quiz 14問目）。テストなので当面許容。なお正式化時に登録すべき誤読実測（TTS）: 「担います→かついます」「後→あとではなくごと」→ システム tts_rules へ集約する運用（docs/QUALITY_OPS_IMPLEMENTATION_PLAN.md §3）
- [x] **R2 配置**（2026-09-20 完了）: `tmp/import_pm_zeroichi_to_r2.py` でインポート。配置先 `sokqa/creators/sokqa_hackathon_dev/packs/pm_zeroichi_v1/`（latest: revision 2 / `v20260920_232612`）。**GCS→R2 移行の変更に注意**: R2 バケットはプライベートで `/generated` はループバック専用プロキシ（LAN IP・トンネルは403）。cdn.convly.jp を R2 バケット `sokqa-studio` のカスタムドメインへ接続し、`.env` の `PUBLIC_BASE_URL=https://cdn.convly.jp` を設定して再コミットしたため、manifest 内 URL は公開到達可能（3点 HTTP 200 確認）
- [x] **manifest URL 更新**（2026-09-20 完了）: v1 manifest の item URL を実 R2 公開 URL へ差し替え、`validate_pack.py` 再通過済み
- [ ] **実機QRテスト**: QR 読み込み・インポート自体は 2026-09-20 成功済み（`https://convly.jp/import?url=<manifestUrl>`）。**残タスク: リスンスルーで聞き切り感・読み上げ（WBS 等の略語の発音）を確認**。creator は `sokqa_hackathon_dev`
- [ ] **公開判断**: 合格なら author/creator を `sokqa_official` 系に統一し、convly-site `public/sokqa/data/cert/`（または business/）へ配置。配置先カテゴリは未決着

## 7. 経緯・前提知識の所在

- 仕様正本: Obsidian vault `03-outputs/sokqa-studio/Sokqa-Studio-Specification/`（Vol.07 が Import JSON、Vol.03/05 が Blueprint/Template）
- 判断・基準・進捗: 同 vault `02-facts/sokqa/`（参照マップ・規模基準）、`05-tasks/sokqa-studio/2026-09-19PM学習パック初級作成.md`
- 手本データ: convly-site `public/sokqa/data/it-dev/docker/`（doc＋quiz＋manifest の完成形）、Obsidian vault `03-outputs/sokqa-studio/Sokqa-Studio-Specification/exports/it-passport_v1/`
- pack-generator 中断理由の経緯: vault `04-sources/sokqa-studio/2026-09-19pack-generator開発中断の理由.md`
