import os
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .../RAG-System/backend/app/core/config.py -> .../RAG-System
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = _REPO_ROOT / "backend"
_STORAGE_PATHS = ("CHROMA_PATH", "SESSION_STORE_ROOT", "EMBEDDING_CACHE_DIR", "VISION_CACHE_DIR", "CACHE_PATH")

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #---Logging ---
    LOG_LEVEL: str = "INFO"

#---API key pools (comma-separated strings) ---
    GEMINI_API_KEYS: str =""
    TAVILY_API_KEYS: str =""


#---Storage ----
    # The persistent corpus. Session uploads never land here.
    CHROMA_PATH: str = "./data/chroma"
    # One Chroma directory per session, so a session's uploads are dropped by removing a directory
    # and cannot contaminate the corpus that RQ1/RQ2 are measured against.
    SESSION_STORE_ROOT: str = "./data/sessions"
    SESSION_TTL_HOURS: float = 24.0
    SESSION_MAX_DOCUMENTS: int = 5

    #---Vision & Embedding (Week 3) ---
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    VISION_MODEL: str = "gemini-3.5-flash-lite"
    MAX_VISION_PAGES: int = 80
    EMBEDDING_CACHE_DIR: str = "./data/cache/embeddings"
    VISION_CACHE_DIR: str = "./data/cache/vision"


    #--- Query pipeline (Week 4) ---
    QUERY_MODEL: str = "gemini-3.5-flash-lite"
    GENERATION_MODEL: str = "gemini-3.5-flash"
    GENERATION_MAX_OUTPUT_TOKENS: int = Field(default=2048, ge=64)
    QUERY_MAX_CHARS: int = 2000
    # Total queries sent to dense search, the original included.
    EXPANSION_COUNT: int = Field(default=4, ge=3, le=5)
    RETRIEVAL_TOP_K: int = 20
    HISTORY_MAX_TURNS: int = 3
    HISTORY_MAX_SESSIONS: int = 1000
    HISTORY_IDLE_HOURS: float = 24.0
    CACHE_PATH: str = "./data/answer_cache"
    # A hit bypasses the whole pipeline, so err high: a miss only costs a retrieval.
    # Real pairs: paraphrases 0.976-0.989, same-topic-different-question 0.93, unrelated 0.81.
    CACHE_SIMILARITY_THRESHOLD: float = Field(default=0.96, ge=0.0, le=1.0)

    #--- Decomposition (Week 5) ---
    DECOMPOSITION_ENABLED: bool = True
    DECOMPOSITION_MAX_SUB_QUESTIONS: int = Field(default=4, ge=2, le=6)

    #--- Fusion and reranking (Week 5) ---
    # Dense share of the RRF score, the RQ1 ablation variable. 1.0 is dense-only, 0.0 sparse-only.
    FUSION_DENSE_WEIGHT: float = Field(default=0.5, ge=0.0, le=1.0)
    RRF_K: int = Field(default=60, ge=1)
    RERANK_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    # Measured on a 2-core CPU with 500-token chunks: 30 at 512 tokens took ~4s, 20 at 256 ~1.3s.
    RERANK_CANDIDATES: int = Field(default=20, ge=1)
    RERANK_MAX_LENGTH: int = Field(default=256, ge=32)
    RERANK_TOP_K: int = Field(default=8, ge=1)
    CONTEXT_TOKEN_BUDGET: int = Field(default=3000, ge=1)

    #--- Sufficiency (Week 5) ---
    # Gated on the best dense cosine similarity, since RRF scores carry no absolute relevance and BM25 is unbounded.
    # Calibrated on 24 real-corpus questions: in-corpus 0.847-0.904, out-of-corpus 0.758-0.888 (adjacent topics reach 0.89).
    # A false auto-pass skips web search on a miss, which costs more than an extra LLM call, so HIGH sits above every
    # out-of-corpus score seen; LOW stays under the weakest in-corpus hit. A grey zone with no LLM verdict leans insufficient.
    SUFFICIENCY_HIGH_THRESHOLD: float = Field(default=0.90, ge=0.0, le=1.0)
    SUFFICIENCY_LOW_THRESHOLD: float = Field(default=0.82, ge=0.0, le=1.0)
    SUFFICIENCY_LLM_ENABLED: bool = True
    SUFFICIENCY_LLM_MODEL: str = "gemini-3.5-flash-lite"
    SUFFICIENCY_LLM_TOP_K: int = Field(default=5, ge=1)
    SUFFICIENCY_LLM_PASSAGE_CHARS: int = Field(default=700, ge=50)
    SUFFICIENCY_LLM_MAX_OUTPUT_TOKENS: int = Field(default=200, ge=16)

    #--- Web search fallback (Week 5) ---
    WEB_SEARCH_MAX_RESULTS: int = Field(default=5, ge=1, le=20)
    WEB_SEARCH_TIMEOUT_SEC: float = Field(default=10.0, gt=0)
    WEB_SEARCH_DEPTH: Literal["basic", "advanced", "fast", "ultra-fast"] = "basic"
    WEB_SEARCH_QUERY_MAX_CHARS: int = Field(default=400, ge=20)
    WEB_SEARCH_MAX_RETRIES: int = Field(default=2, ge=0)
    WEB_SEARCH_BACKOFF_SEC: float = Field(default=0.5, ge=0)
    # A key over its plan limit stays out this long before being probed again.
    WEB_SEARCH_EXHAUSTED_BLOCK_SEC: int = Field(default=3600, ge=1)
    # Web results allowed into the rerank pool: fused[:RERANK_CANDIDATES - n_web] + web[:WEB_MAX_IN_POOL].
    WEB_MAX_IN_POOL: int = Field(default=5, ge=0)

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
        """Normalise TESSERACT_CMD into an absolute path to the binary."""
        path = Path(self.TESSERACT_CMD)

        if not path.is_absolute():
            path = _REPO_ROOT / path

        path = path.resolve()

        if path.is_dir():
            path = path / ("tesseract.exe" if os.name == "nt" else "tesseract")

        self.TESSERACT_CMD = str(path)
        return self

    @model_validator(mode="after")
    def resolve_storage_paths(self) -> "Settings":
        # Relative to backend/, not the cwd: a different cwd would open an empty store.
        for name in _STORAGE_PATHS:
            setattr(self, name, str((_BACKEND_ROOT / getattr(self, name)).resolve()))
        return self

    @model_validator(mode="after")
    def check_token_bounds(self) -> "Settings":
        if self.CHUNK_MIN_TOKENS > self.CHUNK_MAX_TOKENS:
            raise ValueError(
                f"CHUNK_MIN_TOKENS ({self.CHUNK_MIN_TOKENS}) cannot exceed "
                f"CHUNK_MAX_TOKENS ({self.CHUNK_MAX_TOKENS})"
            )
        return self

    @model_validator(mode="after")
    def check_sufficiency_thresholds(self) -> "Settings":
        if self.SUFFICIENCY_LOW_THRESHOLD > self.SUFFICIENCY_HIGH_THRESHOLD:
            raise ValueError(
                f"SUFFICIENCY_LOW_THRESHOLD ({self.SUFFICIENCY_LOW_THRESHOLD}) cannot exceed "
                f"SUFFICIENCY_HIGH_THRESHOLD ({self.SUFFICIENCY_HIGH_THRESHOLD})"
            )
        return self

settings =Settings()