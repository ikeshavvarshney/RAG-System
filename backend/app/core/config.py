from pathlib import Path

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


#---OCR engine ---
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
    def check_token_bounds(self) -> "Settings":
        if self.CHUNK_MIN_TOKENS > self.CHUNK_MAX_TOKENS:
            raise ValueError(
                f"CHUNK_MIN_TOKENS ({self.CHUNK_MIN_TOKENS}) cannot exceed "
                f"CHUNK_MAX_TOKENS ({self.CHUNK_MAX_TOKENS})"
            )
        return self

settings =Settings()