import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, ingest
from app.core.config import settings
from app.shared.session_store import purge_expired

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Collect expired session stores, and debris from any failed delete.

    Session uploads are ephemeral by design, but nothing expires them on its
    own. A sweep at startup is enough for a locally run service; a long-lived
    deployment would want this on a timer too.
    """
    try:
        removed = purge_expired()
        if removed:
            logger.info("swept %d expired session store(s)", len(removed))
    except Exception:  # noqa: BLE001 - a failed sweep must never stop the app
        logger.exception("session sweep failed")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Multimodal RAG", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(ingest.router, prefix="/api")
    return app


app = create_app()
