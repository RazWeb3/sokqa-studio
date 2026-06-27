# Sokqa Studio - AIエージェントで教材制作を支援するプラットフォーム

## Overview

AIが教材制作の各工程を支援し、人は内容の確認や修正に集中できる制作プラットフォームです。

企画から教材設計、生成、品質チェック、音声化までを段階的に支援します。

Sokqa Studio は、Sokqa 向けの学習パックを作るための Web スタジオです。教材の構成案を作り、ドキュメントやクイズを生成し、品質チェックと修正、読み上げ用テキストの最適化、音声録音、Manifest による共有までをひとつの制作ラインとして扱います。

教材制作では、構成づくり、本文作成、クイズ作成、読み上げ品質の調整、録音、更新管理が分断されやすくなります。Sokqa Studio はこれらを同じ画面と同じバックエンドの流れにまとめ、作る人がレビューと判断に集中できる状態を目指しています。

## Demo

スクリーンショットとシステム構成図は後日追加予定です。

現時点では、アプリケーション本体は `web/index.html` を FastAPI から配信し、生成、品質チェック、録音、URL/QR 共有までの操作画面を提供しています。

## Features

- **Pack Planning**: テーマ、対象ユーザー、難易度などから教材パックの構成案を作成します。
- **Pack Generation**: 構成案をもとに Sokqa の document / quiz JSON と Manifest を生成します。
- **Quality Check**: 生成済みパックの本文品質と読み補正品質を確認します。
- **TTS Optimization**: 表示テキストを保ったまま、読み上げ用テキストを辞書または Gemini で補正します。
- **Recording**: Google Cloud Text-to-Speech を使い、未録音ユニットの音声を生成してパックに反映します。
- **Revision**: 修正や録音結果を revision / version として保存し、Manifest を更新します。
- **QR Sharing**: Manifest URL を QR コードとして表示し、Sokqa アプリへ渡す導線を提供します。
- **JSON Export**: 生成済みパックを JSON ZIP として出力できます。

## Workflow

企画 → 教材設計 → 生成 → 品質チェック → 音声化

Sokqa Studio の基本的な制作フローは次の通りです。

1. テーマや対象ユーザーを入力する
2. AI が教材構成案を作る
3. 人が構成案を確認し、必要に応じて調整する
4. ドキュメント、クイズ、Manifest を生成する
5. 本文品質と読み補正品質をチェックする
6. 修正候補を確認して保存する
7. 音声化し、録音状態を確認する
8. Manifest URL または QR コードで共有する

## Architecture

Sokqa Studio は、シンプルな HTML/JavaScript フロントエンドと FastAPI バックエンドで構成されています。

| Area | Role |
| --- | --- |
| Frontend | `web/index.html` が生成、品質チェック、録音、共有の操作画面を提供します。 |
| Backend | FastAPI が計画作成、生成、品質チェック、録音、パック管理の API を提供します。 |
| Storage | local と Google Cloud Storage に対応し、生成物と Manifest を保存します。 |
| LLM | Google GenAI / Vertex AI 経由で Gemini を利用できます。mock provider も用意されています。 |
| Cloud | GitHub Actions から Cloud Run へデプロイする構成です。 |

内部の処理は、役割ごとに小さなエージェント/サービスへ分けています。

- **Planner**: 教材パックの構成案を作成します。
- **Document / Quiz Generator**: Sokqa document / quiz JSON を生成します。
- **TTS Optimizer**: 読み上げ用テキストを補正します。
- **Quality Checker / Fixer**: 本文と読み補正の品質問題を検出し、修正候補を作ります。
- **Revision Committer**: 変更内容を revision / version として Manifest に反映します。

詳細な仕様は `docs/SPEC.md` と `docs/AGENT_DESIGN.md` を参照してください。

## Tech Stack

| Technology | Purpose |
| --- | --- |
| Python | バックエンド実装 |
| FastAPI | API と Web UI 配信 |
| HTML / JavaScript | フロントエンド |
| Google GenAI / Vertex AI | Gemini による計画、生成、品質チェック、補正 |
| Google Cloud Text-to-Speech | 音声生成 |
| Google Cloud Storage | 生成物と Manifest の保存 |
| Cloud Run | アプリケーション実行環境 |
| GitHub Actions | main ブランチへの push と手動実行による Cloud Run デプロイ |
| pytest | 自動テスト |

## Local Setup

### Install

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Environment Variables

設定値は `.env.example` と `app/config.py` で確認できます。主な環境変数は次の通りです。

```env
APP_ENV
SOKQA_AUTHOR
PUBLIC_BASE_URL
STORAGE_BACKEND
LOCAL_STORAGE_DIR
TTS_RULES_PATH
TTS_USER_RULES_PATH
TTS_READING_MODE
TTS_CREDIT_PER_CHAR
CLOUD_TTS_LANGUAGE_CODE
CLOUD_TTS_VOICE
CLOUD_TTS_SPEAKING_RATE
CLOUD_TTS_PITCH
CLOUD_TTS_MAX_CONCURRENCY
CLOUD_TTS_RECORDING_REQUEST_MAX_UNITS
GCS_BUCKET
GCS_PREFIX
DEFAULT_CREATOR_ID
ALLOWED_MANIFEST_DOMAINS
GEMINI_PROVIDER
GEMINI_MODEL
GEMINI_MODEL_DOC
GEMINI_MODEL_QUIZ
GEMINI_MODEL_PLANNER
GEMINI_MODEL_QUALITY
GEMINI_MODEL_FIX
GOOGLE_CLOUD_PROJECT
GOOGLE_CLOUD_LOCATION
GOOGLE_GENAI_USE_VERTEXAI
```

### Run

```bash
uvicorn main:app --reload
```

ローカルでは `http://127.0.0.1:8000/` で Studio UI、`http://127.0.0.1:8000/docs` で Swagger UI を開けます。

Dockerfile では Cloud Run 向けに `uvicorn main:app --host 0.0.0.0 --port 8080` で起動します。

### Test

```bash
python -m pytest tests/ --basetemp .pytest_tmp -ra
```

## Deployment

デプロイは GitHub Actions で定義されています。

- Workflow: `.github/workflows/deploy-cloud-run.yml`
- Trigger: `main` ブランチへの push、または手動実行
- Deploy target: Cloud Run
- Cloud Run サービス名: `sokqa-course-pack-agent`
- Region: `asia-northeast1`
- Auth: Workload Identity Provider と Service Account を GitHub Secrets から利用

README 上のプロジェクト名は `Sokqa Studio` ですが、Cloud Run サービス名は現在 `sokqa-course-pack-agent` として定義されています。

## Hackathon

この README は DevOps × AI Agent Hackathon 提出作品として、GitHub を訪れた審査員や開発者が短時間で全体像を理解できるように整備しています。
