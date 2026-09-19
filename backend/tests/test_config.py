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

def test_relative_chroma_path_resolves_against_backend_not_cwd(tmp_path, monkeypatch):
    from app.core.config import _BACKEND_ROOT

    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None, CHROMA_PATH="./data/chroma")

    assert s.CHROMA_PATH == str((_BACKEND_ROOT / "data" / "chroma").resolve())


def test_absolute_chroma_path_is_kept(tmp_path):
    s = Settings(_env_file=None, CHROMA_PATH=str(tmp_path / "store"))

    assert s.CHROMA_PATH == str(tmp_path / "store")
