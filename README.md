# Sokqa Course Pack Agent

Sokqa Studioの前段となる、Sokqa学習パック生成AgentのMVPです。

このMVPは単体JSON生成ではなく、構成案から複数document/quiz JSONを生成し、検証、修復、TTS最適化、保存、manifest化までを1つの制作ラインとして扱います。

## Features

- `GET /health`
- `POST /plan-pack`
- `POST /generate-pack`
- `GET /jobs/{jobId}`
- `GET /jobs/{jobId}/manifest`
- Debug/Admin:
  - `POST /debug/validate-pack`
  - `POST /debug/repair-pack`
  - `POST /debug/optimize-tts`
  - `POST /debug/revise-tts`

## Run Locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000/docs`.

## Quick Flow

1. Call `POST /plan-pack` with `scale: "quick"`.
2. Review/edit the returned plan.
3. Call `POST /generate-pack` with the plan.
4. Read the manifest from `GET /jobs/{jobId}/manifest`.
5. If device testing finds a TTS issue later, call `POST /debug/revise-tts` with extra reading rules. The agent keeps display content fixed, regenerates TTS fields, and bumps the manifest patch version.

Generated local files are saved under `generated/{pack_id}` by default.

## Storage

Default storage is local. For Cloud Storage:

```env
STORAGE_BACKEND=gcs
GCS_BUCKET=your-bucket
GCS_PREFIX=sokqa/packs
PUBLIC_BASE_URL=https://cdn.convly.jp/sokqa/packs
```

The app should only import manifest URLs from Sokqa-managed domains.

## Gemini Hook

The MVP uses deterministic mock generators by default. When `GEMINI_PROVIDER=gemini`,
document and quiz generation try Gemini first, then fall back to the deterministic mock
generators if the call fails.

The Gemini boundary is in `app/services/gemini_client.py` and prompt builders live in
`app/services/prompts.py`.

Set these when wiring real generation:

```env
GEMINI_PROVIDER=gemini
GEMINI_MODEL=gemini-2.5-flash
```

For local Google auth, use Application Default Credentials:

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```
