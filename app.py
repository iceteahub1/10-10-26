"""Phase 1 infrastructure dashboard only; no ordering or AI inference."""
import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from bistro.config import load_settings
from bistro.paths import ROOT
settings = load_settings()
app = FastAPI(title="Perpetual Bistro — Phase 1", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
@app.get("/")
def dashboard():
    return FileResponse(ROOT / "templates/index.html")
@app.get("/health")
def health():
    return {"project": "perpetual-bistro", "phase": 1, "status": "infrastructure-ready", "launch_token": os.getenv("BISTRO_LAUNCH_TOKEN", ""),
            "ai_available": os.getenv('BISTRO_AI_AVAILABLE') == '1', "ai_model": settings.model,
            "ai_reason": os.getenv('BISTRO_AI_REASON', 'AI availability has not been checked.'), "browser_ai_chat": False}
