# Sokqa Studio - AIエージェントで教材制作を支援するプラットフォーム

## Overview

### Project

AIが教材制作の各工程を支援し、人は内容の確認や修正に集中できる制作プラットフォームです。

企画から教材設計、生成、品質チェック、音声化までを段階的に支援します。

Sokqa Studio は、Sokqa 向けの学習パックを作るための Web スタジオです。AIチャットでも教材の一部は作れますが、教材制作全体を進めるには、設計、生成、確認、音声化、共有を手作業でつなぐ必要があります。

Sokqa Studio では、テーマを決めるだけで教材制作を開始できます。教材の構成案を作り、ドキュメントやクイズを生成し、品質チェックと修正、読み上げ用テキストの最適化、音声録音、Manifest による共有までをひとつの制作ラインとして扱います。

### Problem

教材制作は、本文やクイズを生成して終わる作業ではありません。教材設計、生成、品質確認、音声化、共有まで続く工程があり、それぞれの結果を確認しながら次の工程へ進める必要があります。

AIチャットだけでこのワークフロー全体を効率よく進めようとすると、プロンプト、出力管理、品質確認、音声化、共有準備が分断されます。そのため、作る人が工程ごとのつなぎ込みや確認作業に時間を使いやすくなります。

### Solution

Sokqa Studio は、テーマを決めるだけで教材制作を開始できるようにします。教材設計、生成、品質確認、音声化、Manifest、QRコード、URL共有までを一つの制作ワークフローとして扱います。

人は生成結果を確認し、必要な修正を判断し、学習者に届ける品質へ改善することに集中できます。

## Demo

スクリーンショットは後日追加予定です。

システム構成図は Architecture セクションに掲載しています。

現時点では、アプリケーション本体は `web/index.html` を FastAPI から配信し、生成、品質チェック、録音、URL/QR 共有までの操作画面を提供しています。

## Features

- **AIによる教材設計**: テーマ、対象ユーザー、難易度などから教材パックの構成案を作成します。
- **ドキュメント・クイズの一括生成**: 構成案をもとに Sokqa の document / quiz JSON と Manifest を生成します。
- **品質チェック**: 生成済みパックの本文品質と読み補正品質を確認します。
- **音声教材生成**: 読み上げ用テキストを補正し、Google Cloud Text-to-Speech で音声を生成します。
- **QRコード共有**: Manifest URL を QR コードとして表示し、Sokqa アプリへ渡す導線を提供します。
- **Learning Pack 出力**: Document、Quiz、Manifest、Audio をまとめて学習パックとして扱えます。

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

### System Architecture

![Sokqa Studio system architecture](docs/images/sokqa-studio-architecture.png)

Sokqa Studio は、HTML/JavaScript フロントエンド、FastAPI バックエンド、Google Cloud を組み合わせた制作支援システムです。

| Area | Role |
| --- | --- |
| Frontend | `web/index.html` が生成、品質チェック、録音、共有の操作画面を提供します。 |
| Backend | FastAPI が計画作成、生成、品質チェック、録音、パック管理の API を提供します。 |
| Storage | local と Google Cloud Storage に対応し、生成物と Manifest を保存します。 |
| LLM | Google GenAI / Vertex AI 経由で Gemini を利用できます。mock provider も用意されています。 |
| Cloud | GitHub Actions から Cloud Run へデプロイする構成です。 |

制作フローの中では、主に次の役割が連携します。

- **Planner**: 教材パックの構成案を作成します。
- **Generator**: ドキュメント、クイズ、Manifest を生成します。
- **Quality**: 本文と読み補正の品質問題を検出し、修正候補を作ります。
- **TTS**: 読み上げ用テキストの補正と音声生成を扱います。

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

## Related Project

Sokqa Studio は、学習アプリ **Sokqa** 向けの学習パック制作ツールです。

Studio で作成した学習パックは Sokqa アプリへインポートできます。Sokqa アプリはログイン不要で、端末内に保存した学習パックを使ってローカル完結・オフライン学習できます。

- Sokqa App: https://convly.jp/sokqa/

## Hackathon

Sokqa Studio は、教材制作を単発の生成ではなく、設計、生成、確認、音声化、共有まで続くワークフローとして扱うために作りました。

Cloud Run と GitHub Actions によるデプロイ構成を採用し、AIを使った制作体験と運用しやすい開発フローの両方を意識しています。DevOps × AI Agent Hackathon 提出作品です。
