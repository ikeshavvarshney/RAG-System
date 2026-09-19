import pytest
from pydantic import ValidationError

from app.core.config import Settings

def test_settings_construct_with_no_env(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    s= Settings(_env_file=None)
    assert s.GEMINI_API_KEYS ==""


def test_chunk_max_tokens_from_env():
    s= Settings(_env_file=None, CHUNK_MAX_TOKENS=900)
    assert s.CHUNK_MAX_TOKENS == 900
    assert  isinstance(s.CHUNK_MAX_TOKENS, int)


def test_chunk_min_tokens_from_env():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, CHUNK_MIN_TOKENS=1000, CHUNK_MAX_TOKENS=500)

@pytest.mark.parametrize(
    "field", ["CHROMA_PATH", "SESSION_STORE_ROOT", "EMBEDDING_CACHE_DIR", "VISION_CACHE_DIR"]
)
def test_relative_storage_paths_resolve_against_backend_not_cwd(field, tmp_path, monkeypatch):
    from app.core.config import _BACKEND_ROOT

    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None, **{field: "./data/somewhere"})

    assert getattr(s, field) == str((_BACKEND_ROOT / "data" / "somewhere").resolve())


def test_defaults_resolve_to_absolute_paths_under_backend(tmp_path, monkeypatch):
    from app.core.config import _BACKEND_ROOT

    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None)

    for field in ("CHROMA_PATH", "SESSION_STORE_ROOT", "EMBEDDING_CACHE_DIR", "VISION_CACHE_DIR"):
        assert getattr(s, field).startswith(str(_BACKEND_ROOT))


@pytest.mark.parametrize(
    "field", ["CHROMA_PATH", "SESSION_STORE_ROOT", "EMBEDDING_CACHE_DIR", "VISION_CACHE_DIR"]
)
def test_absolute_storage_paths_are_kept(field, tmp_path):
    s = Settings(_env_file=None, **{field: str(tmp_path / "store")})

    assert getattr(s, field) == str(tmp_path / "store")
