"""
agent-base-api — Tur 4 monorepo birleşim entrypoint'i.

Tek FastAPI process iki yetkiyi birden taşıyor:
  1. Mevcut agent-base API (auth, social_data, social_media)
  2. rule-based-engine'in orchestration_api router'ı (LangGraph + structured
     rules + scheduling + customer interaction + campaign + auth/orgs +
     social credentials)

Eski rule-based-engine deployment'ı (port 8000 fake commerce + port 8001
orchestration) bu birleşik servera taşındı; tek port, tek lifespan.

Çevre değişkenleri:
  - INTERNAL_SERVICE_IN_PROCESS=1  (otomatik set ediliyor)
  - LANGGRAPH_CHECKPOINT_DB        (default: listener.db)
  - SOCIAL_PUBLISH_LIVE            (default: 0)
  - CHAT_USE_LLM, NL_PARSER_USE_LLM (default: 1)
"""
from contextlib import asynccontextmanager
import logging
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

# Tur 4: internal service guard — orchestration_api'nin self-HTTP loop
# detector'u bu process içinde aktif olsun.
os.environ.setdefault("INTERNAL_SERVICE_IN_PROCESS", "1")

# Tur 4: rule-based-engine'den kopyalanan flat Python modülleri
# agent-base-api/ root'unda (app/'in bir üstü). sys.path'e ekleyelim ki
# `import db`, `from langgraph_engine import runtime` vb. çalışsın.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Varsayılan olarak uvicorn access logları kapalı — API spam'i önler.
if os.getenv('APP_DISABLE_UVICORN_ACCESS_LOG', '1').strip().lower() in ('1', 'true', 'yes', 'on'):
    logging.getLogger('uvicorn.access').disabled = True

from app.api import auth, client_debug, social_data, social_media
from app.core.database import init_db
from app.core.settings import settings
from app.services.local_media_storage import get_media_root, use_local_media_storage


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 1) Mevcut agent-base DB
    init_db()

    # 2) Tur 4: rule-based-engine listener.db şemasını başlat (orchestration
    #    katmanı için 30+ tablo: structured_rules, rule_executions,
    #    orchestration_traces, campaigns, scheduled_entries, ...).
    try:
        import db as orchestration_db  # noqa
        orchestration_db.init_db()
    except Exception as exc:
        logging.warning(f"orchestration db init skipped: {exc}")

    yield


app = FastAPI(
    title=settings.app.name,
    version=settings.app.version,
    description=settings.app.description,
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors.allow_origins,
    allow_credentials=settings.cors.allow_credentials,
    allow_methods=settings.cors.allow_methods,
    allow_headers=settings.cors.allow_headers,
)


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "name": settings.app.name,
        "description": settings.app.description,
        "version": settings.app.version,
        "monorepo_tour": 4,
    }


@app.get("/")
def root():
    return {
        "status": "healthy",
        "name": settings.app.name,
        "description": settings.app.description,
        "version": settings.app.version,
        "endpoints": {
            "agent_base_api":  "/api/auth, /api/social-*, /api/social-data",
            "orchestration":   "/api/internal/* (LangGraph + structured rules)",
            "dashboard_html":  "/dashboard (legacy single-page UI)",
            "fake_commerce":   "/commerce-platform/internal/create-* (mock e-commerce)",
        },
    }


# -----------------------------------------------------------------------
# Mevcut agent-base API router'ları (Tur 1-3'te zaten vardı)
# -----------------------------------------------------------------------
app.include_router(auth.router)
app.include_router(client_debug.router)
app.include_router(social_data.router)
app.include_router(social_media.router)
app.include_router(social_media.legacy_router)


# -----------------------------------------------------------------------
# Tur 4: rule-based-engine orchestration_api router mount.
# 80+ endpoint: /api/internal/structured-rules*, /rule-executions*,
# /rule-templates*, /campaigns*, /scheduled-entries*, /credentials*,
# /chat*, /workflows*, /tasks*, /approvals*, ... vb.
# -----------------------------------------------------------------------
try:
    from orchestration_api import router as orchestration_router
    app.include_router(orchestration_router)
    logging.info("orchestration_api mounted at /api/internal/*")
except Exception as exc:
    logging.warning(f"orchestration_api could not be mounted: {exc}")


# -----------------------------------------------------------------------
# Tur 4: Fake commerce platform — rule-based-engine/main.py içeriği
# fake_commerce_app.py olarak kopyalandı. Listener bu app'in
# fake_ai_api.db.timeline tablosunu polling ediyor; mock event akışı
# bozulmasın diye sub-app olarak mount ediyoruz.
# -----------------------------------------------------------------------
try:
    from fake_commerce_app import app as _fake_commerce_app
    app.mount("/commerce-platform", _fake_commerce_app)
    logging.info("fake_commerce_app mounted at /commerce-platform")
except Exception as exc:
    logging.warning(f"fake_commerce_app could not be mounted: {exc}")


# -----------------------------------------------------------------------
# Legacy dashboard HTML — /api/internal/dashboard router'ında zaten var;
# ek konfor için /dashboard kısa yolu da var.
# -----------------------------------------------------------------------
_DASHBOARD_PATH = Path(__file__).resolve().parent.parent / "index.html"
if _DASHBOARD_PATH.exists():
    @app.get("/dashboard")
    def dashboard():
        return FileResponse(
            str(_DASHBOARD_PATH),
            headers={"Cache-Control": "no-store"},
        )


if use_local_media_storage():
    _mr = Path(get_media_root())
    _mr.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(_mr)), name="local_media")


if __name__ == "__main__":
    uvicorn.run(
        app=app,
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.server.reload,
    )
