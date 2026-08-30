from fastapi  import FastAPI 
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health
from app.core.config import settings


def create_app() -> FastAPI:
    app= FastAPI(title="Multimodal RAG", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    return app


app=create_app()