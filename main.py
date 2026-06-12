from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routes import debug, generate, health, jobs, packs, plan, quality, tts_recording
from app.startup import log_runtime_settings


app = FastAPI(
    title="Sokqa Course Pack Agent",
    version="0.1.0",
    description="Generate, validate, repair, optimize, store, and manifest Sokqa course packs.",
)


@app.on_event("startup")
def startup_event() -> None:
    log_runtime_settings()

app.include_router(health.router)
app.include_router(plan.router)
app.include_router(generate.router)
app.include_router(jobs.router)
app.include_router(packs.router)
app.include_router(tts_recording.router)
app.include_router(quality.router)
app.include_router(debug.router)

BASE_DIR = Path(__file__).resolve().parent
generated_dir = BASE_DIR / "generated"
generated_dir.mkdir(exist_ok=True)
app.mount("/generated", StaticFiles(directory=generated_dir), name="generated")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(BASE_DIR / "web" / "index.html")
