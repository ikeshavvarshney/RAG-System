import os
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .../RAG-System/backend/app/core/config.py -> .../RAG-System
_REPO_ROOT = Path(__file__).resolve().parents[3]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

#---API key pools (comma-separated strings) ---
    GEMINI_API_KEYS: str =""
    TAVILY_API_KEYS: str =""


#---Storage ----
    CHROMA_PATH: str = "./data/chroma"

    #---Vision & Embedding (Week 3) ---
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    VISION_MODEL: str = "gemini-3.6-flash"
    MAX_VISION_PAGES: int = 80
    EMBEDDING_CACHE_DIR: str = "./data/cache/embeddings"
    VISION_CACHE_DIR: str = "./data/cache/vision"


#---OCR engine ---
    # "paddle"            plain PP-OCR recognition, ~3s/page (default)
    # "paddle-structure"  PP-StructureV3: layout + table structure recovery
    #                     as markdown, ~75s/page on CPU. This is D-27's
    #                     structure-aware OCR baseline for RQ2, not a
    #                     production ingestion setting: at that cost a full
    #                     corpus pass takes hours. Turn it on for the runs
    #                     that need recovered tables.
    # "tesseract"         skip paddle entirely
    # Each engine falls back to the next cheaper one at runtime when it is
    # unavailable, so this chooses what is attempted, not what is required.
    OCR_ENGINE: Literal["paddle-structure", "paddle", "tesseract"] = "paddle"
    OCR_LANG: str = "en"
    # Detections below this score are dropped. Paddle boxes page rules and
    # borders as text, which would otherwise reach the chunker as content.
    OCR_MIN_CONFIDENCE: float = 0.5
    # Rotated-line classification. Off by default: it costs a model per image
    # and the corpus is upright pages.
    OCR_TEXTLINE_ORIENTATION: bool = False
    # Absolute path to the Tesseract binary. Overridable per machine via .env
    # so the wrapper does not depend on the developer's system PATH.
    TESSERACT_CMD: str = str(_REPO_ROOT / "vendor" / "tesseract" / "tesseract.exe")


#---Frontend / CORS ---
    FRONTEND_ORIGIN: str ="http://localhost:3000"

    #--- Chunking contract ---
    CHUNK_MIN_TOKENS: int =200
    CHUNK_MAX_TOKENS: int =800
    CHUNK_TARGET_TOKENS: int =500

    @model_validator(mode="after")
    def resolve_tesseract_cmd(self) -> "Settings":
        """Normalise TESSERACT_CMD into an absolute path to the binary.

        A relative path is taken as relative to the repo root, not to the
        process working directory, so the same .env works under uvicorn (run
        from backend/) and under pytest. A path naming the directory that
        holds the binary is completed with the platform's binary name:
        Windows reports executing a directory as PermissionError WinError 5,
        "Access is denied", which reads like a permissions problem rather
        than the configuration mistake it is.
        """
        path = Path(self.TESSERACT_CMD)

        if not path.is_absolute():
            path = _REPO_ROOT / path

        path = path.resolve()

        if path.is_dir():
            path = path / ("tesseract.exe" if os.name == "nt" else "tesseract")

        self.TESSERACT_CMD = str(path)
        return self

    @model_validator(mode="after")
    def check_token_bounds(self) -> "Settings":
        if self.CHUNK_MIN_TOKENS > self.CHUNK_MAX_TOKENS:
            raise ValueError(
                f"CHUNK_MIN_TOKENS ({self.CHUNK_MIN_TOKENS}) cannot exceed "
                f"CHUNK_MAX_TOKENS ({self.CHUNK_MAX_TOKENS})"
            )
        return self

settings =Settings()