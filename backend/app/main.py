import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, ingest, query
from app.core.config import settings
from app.core.warmup import warm_up_clients
from app.shared.session_store import purge_expired

logger = logging.getLogger(__name__)


async def _warm_up() -> None:
    try:
        await asyncio.to_thread(warm_up_clients)
    except Exception:  # noqa: BLE001 - warm-up is an optimisation only
        logger.warning("client warm-up failed", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Collect expired session stores, and debris from any failed delete."""
    try:
        removed = purge_expired()
        if removed:
            logger.info("swept %d expired session store(s)", len(removed))
    except Exception:  # noqa: BLE001 - a failed sweep must never stop the app
        logger.exception("session sweep failed")
    warm_up_task = asyncio.create_task(_warm_up())
    yield
    warm_up_task.cancel()


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
    app.include_router(query.router, prefix="/api")
    return app


app = create_app()
