from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health

# TODO: replace with settings.FRONTEND_ORIGIN once config.py is implemented
FRONTEND_ORIGIN = "http://localhost:3000"


def create_app() -> FastAPI:
    app = FastAPI(title="Multimodal RAG", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    return app


app = create_app()