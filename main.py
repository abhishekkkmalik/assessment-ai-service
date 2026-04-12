"""
FastAPI application entry point for the assessment-ai-service.

Registers all routers (generate, modify, embed) and handles application
lifecycle: Qdrant collection initialisation on startup.

Endpoints:
  POST   /ai/generate                   — generate MCQ questions for a chapter/topic
  POST   /ai/modify                     — modify a single existing question
  POST   /ai/embed                      — ingest a PDF from GCS into Qdrant
  DELETE /ai/embed/{pdf_id}             — soft-delete PDF embeddings (is_active=False)
  PATCH  /ai/embed/{pdf_id}/reactivate  — restore soft-deleted embeddings
  DELETE /ai/embed/{pdf_id}/permanent   — permanently remove PDF embeddings
  GET    /health                        — liveness + Qdrant readiness probe
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from routers import generate, modify, embed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("ai_service.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Startup: creates the Qdrant collection and payload indexes if they don't
    exist yet.  A warning is logged (not an error) if Qdrant is unavailable at
    startup — the service can still handle requests once Qdrant comes up.

    Shutdown: no explicit cleanup required (Qdrant client is stateless).
    """
    try:
        from services.vector_store import ensure_collection
        ensure_collection()
    except Exception as exc:
        logger.warning(f"Qdrant init failed (service may not be ready yet): {exc}")
    yield
    # Shutdown: nothing to clean up


app = FastAPI(
    title="MCQ Generation Service",
    description="MCQ generation and modification via Gemini 2.5 Flash (Vertex AI)",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(generate.router)
app.include_router(modify.router)
app.include_router(embed.router)


@app.get("/health")
async def health():
    """
    Liveness and readiness probe.

    Returns {"status": "ok", "qdrant_collection_ready": bool}.
    qdrant_collection_ready is True only if the configured collection exists
    in Qdrant — useful for distinguishing a healthy service from one that is
    still waiting for Qdrant to initialise.
    """
    qdrant_ok = False
    try:
        from services.vector_store import _get_qdrant, COLLECTION_NAME
        collections = {c.name for c in _get_qdrant().get_collections().collections}
        qdrant_ok = COLLECTION_NAME in collections
    except Exception:
        pass
    return {"status": "ok", "qdrant_collection_ready": qdrant_ok}
