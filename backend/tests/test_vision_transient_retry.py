"""Transient network errors on a vision call are retried; quota and auth errors are not. No network."""

import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from google.genai import errors as genai_errors
from PIL import Image

from app.core import gemini_client as gc
from app.core.config import settings
from app.core.gemini_client import (
    TRANSIENT_MAX_RETRIES,
    TRANSIENT_RETRY_DELAYS_SEC,
    GeminiClient,
    is_transient_error,
)
from app.core.key_rotation import KeyRotator
from app.core.usage import UsageTracker
from app.ingestion.extractors import vision

CHART_REPLY = "CONTENT_TYPE: chart\nChart type: bar chart. Title: Quarterly revenue. Data points: Q1 10, Q2 12."


def _response(text: str = CHART_REPLY):
    response = MagicMock()
    response.text = text
    response.usage_metadata.prompt_token_count = 10
    response.usage_metadata.candidates_token_count = 5
    return response


def _api_error(cls, code: int, message: str = "boom"):
    return cls(code, {"error": {"code": code, "message": message}})


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a,key-b"))


def _vision_call(client: GeminiClient, **kwargs):
    return client.generate_vision("vision", "model-x", "describe", b"img", "image/png", **kwargs)


def test_the_retry_policy_is_three_retries_at_2_5_and_10_seconds():
    assert TRANSIENT_RETRY_DELAYS_SEC == (2.0, 5.0, 10.0) and TRANSIENT_MAX_RETRIES == 3


@pytest.mark.parametrize(
    "error",
    [
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.ReadError("connection reset"),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("no route"),
        httpx.ConnectTimeout("connect timed out"),
        _api_error(genai_errors.ServerError, 503, "unavailable"),
        _api_error(genai_errors.ServerError, 500, "internal"),
    ],
)
def test_a_transient_error_followed_by_success_succeeds(keys, caplog, error):
    client = GeminiClient(tracker=UsageTracker(), transient_delays=(0, 0, 0))

    with patch("app.core.gemini_client.genai.Client") as client_cls, caplog.at_level("WARNING"):
        client_cls.return_value.models.generate_content.side_effect = [error, _response()]
        text = _vision_call(client)

    assert text == CHART_REPLY and client_cls.return_value.models.generate_content.call_count == 2
    assert client.transient_retries == {type(error).__name__: 1}
    assert f"transient {type(error).__name__}" in caplog.text and "retry 1/3" in caplog.text


def test_retries_wait_2_then_5_then_10_seconds_and_give_up_after_three(keys, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(gc.time, "sleep", sleeps.append)
    client = GeminiClient(tracker=UsageTracker())

    with patch("app.core.gemini_client.genai.Client") as client_cls:
        generate = client_cls.return_value.models.generate_content
        generate.side_effect = httpx.RemoteProtocolError("Server disconnected without sending a response.")
        with pytest.raises(httpx.RemoteProtocolError):
            _vision_call(client)

    assert generate.call_count == 4  # the first attempt plus three retries
    assert sleeps == [2.0, 5.0, 10.0]
    assert client.transient_retries == {"RemoteProtocolError": 3}


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(keys, monkeypatch, status):
    sleeps: list[float] = []
    monkeypatch.setattr(gc.time, "sleep", sleeps.append)
    client = GeminiClient(tracker=UsageTracker())

    with patch("app.core.gemini_client.genai.Client") as client_cls:
        generate = client_cls.return_value.models.generate_content
        generate.side_effect = _api_error(genai_errors.ClientError, status)
        with pytest.raises(genai_errors.ClientError):
            _vision_call(client)

    assert generate.call_count == 1 and sleeps == [] and not client.transient_retries


def test_a_429_is_not_treated_as_transient_and_stops_at_once(keys, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(gc.time, "sleep", sleeps.append)
    client = GeminiClient(tracker=UsageTracker())

    with patch("app.core.gemini_client.genai.Client") as client_cls:
        generate = client_cls.return_value.models.generate_content
        generate.side_effect = _api_error(genai_errors.ClientError, 429, "Quota exceeded for metric ... PerDay")
        with pytest.raises(Exception):  # every key is out of daily quota
            _vision_call(client, max_retries=1)

    assert not is_transient_error(generate.side_effect)
    assert not client.transient_retries and not any(s in (2.0, 5.0, 10.0) for s in sleeps)
    assert generate.call_count == 2  # one try per key, then both are blocked; no transient retry on top


# --------------------------------------------------------------------------- #
# Through the ingestion script
# --------------------------------------------------------------------------- #
def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "ingest_corpus.py"
    spec = importlib.util.spec_from_file_location("ingest_corpus_retry_script", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run(tmp_path, monkeypatch, keys):
    folder = tmp_path / "files"
    folder.mkdir()
    buffer = io.BytesIO()
    Image.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3)).save(buffer, format="PNG")
    (folder / "chart.png").write_bytes(buffer.getvalue())
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", str(tmp_path / "vision-cache"))
    monkeypatch.setattr(vision._client, "transient_delays", (0, 0, 0))
    ocr_calls = []
    monkeypatch.setattr(vision, "run_ocr", lambda image: ocr_calls.append(1) or "ocr text")
    script = _load_script()

    def go(side_effect):
        summary_path = tmp_path / "summary.json"
        monkeypatch.setattr(sys, "argv", ["ingest_corpus.py", "--corpus-dir", str(folder), "--summary-json", str(summary_path)])
        with patch("app.core.gemini_client.genai.Client") as client_cls:
            client_cls.return_value.models.generate_content.side_effect = side_effect
            code = script.main()
            calls = client_cls.return_value.models.generate_content.call_count
        return code, json.loads(summary_path.read_text(encoding="utf-8")), calls, ocr_calls

    return go


def test_the_run_summary_counts_transient_retries(run):
    code, summary, calls, _ = run([httpx.RemoteProtocolError("Server disconnected"), _response()])

    assert code == 0 and calls == 2
    assert summary["vision"]["transient_retries"] == {"total": 1, "by_type": {"RemoteProtocolError": 1}}
    assert summary["vision"]["api_calls"] == 1  # the guard sees one call: the retry happened inside it


def test_three_retries_that_all_fail_stop_the_run_with_a_clear_message(run, capsys):
    code, summary, calls, ocr_calls = run(httpx.RemoteProtocolError("Server disconnected without sending a response."))

    assert code == 3 and calls == 4 and ocr_calls == []  # no quiet fall back to OCR
    assert summary["vision"]["transient_retries"]["total"] == 3
    assert "transient network error; gave up after 3 retries" in summary["api_error"]
    assert "STOPPED at the first API error" in capsys.readouterr().err


def test_a_429_stops_the_run_at_once_with_no_transient_retry(run):
    code, summary, calls, ocr_calls = run(_api_error(genai_errors.ClientError, 429, "Quota exceeded ... PerDay"))

    assert code == 3 and ocr_calls == []
    assert summary["vision"]["transient_retries"] == {"total": 0, "by_type": {}}
    assert "transient" not in summary["api_error"]
