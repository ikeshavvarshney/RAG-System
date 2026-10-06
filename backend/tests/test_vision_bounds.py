"""A stalled vision call fails fast and is bounded; clients are reused; calls are logged; --max-units stops cleanly.

The stall tests aim at non-routable addresses (10.255.255.x). Nothing answers there, so a connection attempt
hangs exactly like the stalls seen during the full ingest, and no real service is ever reached.
"""

import importlib.util
import io
import json
import os
import socket
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from PIL import Image

from app.core import gemini_client as gc
from app.core.config import settings
from app.core.gemini_client import (
    TRANSIENT_RETRY_DELAYS_SEC,
    VISION_CALL_TIMEOUT_SEC,
    VISION_CONNECT_TIMEOUT_SEC,
    VISION_SLOW_CALL_WARN_SEC,
    CallDeadlineExceeded,
    GeminiClient,
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


@pytest.fixture
def one_key(monkeypatch):
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a"))


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _call(client: GeminiClient):
    return client.generate_vision("vision", "model-x", "describe", b"img", "image/png")


def test_the_documented_limits_and_the_worst_case_per_page():
    assert VISION_CONNECT_TIMEOUT_SEC == 10.0 and VISION_CALL_TIMEOUT_SEC == 60.0 and VISION_SLOW_CALL_WARN_SEC == 30.0
    # Four attempts (the first plus three retries), each cut off at 60 s, plus the 2 + 5 + 10 s waits between them.
    assert 4 * VISION_CALL_TIMEOUT_SEC + sum(TRANSIENT_RETRY_DELAYS_SEC) == 257.0


# --------------------------------------------------------------------------- #
# A stalled call fails within the limits, not after ~175 s
# --------------------------------------------------------------------------- #
def test_a_connection_that_never_answers_fails_within_the_connect_limit(one_key, monkeypatch):
    monkeypatch.setattr(gc, "VISION_CONNECT_TIMEOUT_SEC", 1.0)
    monkeypatch.setattr(gc, "VISION_CALL_TIMEOUT_SEC", 5.0)
    client = GeminiClient(tracker=UsageTracker(), transient_delays=())
    client.vision_base_url = "http://10.255.255.1"
    client._vision_client_for("key-a")  # building the SDK client costs seconds; time only the stalled call

    started = time.perf_counter()
    with pytest.raises(httpx.TimeoutException):
        _call(client)
    elapsed = time.perf_counter() - started

    # Unbounded, one unanswered address holds the OS connect attempt for about 21 s.
    assert elapsed < 4.0, f"took {elapsed:.1f}s"


def test_a_name_with_several_dead_addresses_is_cut_off_by_the_call_deadline(one_key, monkeypatch):
    real = socket.getaddrinfo

    def several_dead_addresses(host, *args, **kwargs):
        if host == "stall.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"10.255.255.{n}", 80)) for n in (1, 2, 3)]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", several_dead_addresses)
    monkeypatch.setattr(gc, "VISION_CONNECT_TIMEOUT_SEC", 1.0)  # three addresses would add up to 3 s
    monkeypatch.setattr(gc, "VISION_CALL_TIMEOUT_SEC", 1.5)
    client = GeminiClient(tracker=UsageTracker(), transient_delays=())
    client.vision_base_url = "http://stall.test"
    client._vision_client_for("key-a")  # building the SDK client costs seconds; time only the stalled call

    started = time.perf_counter()
    with pytest.raises(CallDeadlineExceeded):
        _call(client)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.6, f"took {elapsed:.1f}s"
    assert client._vision_clients == {}  # the stalled client was discarded


def test_a_call_that_hangs_is_abandoned_at_the_deadline_and_retried(one_key, monkeypatch):
    monkeypatch.setattr(gc, "VISION_CALL_TIMEOUT_SEC", 0.2)
    client = GeminiClient(tracker=UsageTracker(), transient_delays=(0.0,))
    outcomes = [lambda: time.sleep(1.0), lambda: _response()]

    with patch("app.core.gemini_client.genai.Client") as client_cls:
        client_cls.return_value.models.generate_content.side_effect = lambda **kw: outcomes.pop(0)()
        started = time.perf_counter()
        text = _call(client)

    assert text == CHART_REPLY and time.perf_counter() - started < 0.9
    assert client.transient_retries == {"CallDeadlineExceeded": 1}


# --------------------------------------------------------------------------- #
# One SDK client per key
# --------------------------------------------------------------------------- #
def test_the_sdk_client_is_reused_across_vision_calls_with_the_same_key(one_key):
    client = GeminiClient(tracker=UsageTracker())

    with patch("app.core.gemini_client.genai.Client") as client_cls:
        client_cls.return_value.models.generate_content.return_value = _response()
        for _ in range(3):
            _call(client)

    assert client_cls.call_count == 1 and client_cls.return_value.models.generate_content.call_count == 3


def test_each_key_gets_its_own_client_and_rotation_still_works(monkeypatch):
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a,key-b"))
    client = GeminiClient(tracker=UsageTracker())

    with patch("app.core.gemini_client.genai.Client") as client_cls:
        client_cls.return_value.models.generate_content.return_value = _response()
        for _ in range(4):
            _call(client)

    assert [c.kwargs["api_key"] for c in client_cls.call_args_list] == ["key-a", "key-b"]


def test_a_connection_error_discards_the_client_so_the_retry_opens_a_fresh_one(one_key):
    client = GeminiClient(tracker=UsageTracker(), transient_delays=(0.0,))

    with patch("app.core.gemini_client.genai.Client") as client_cls:
        client_cls.return_value.models.generate_content.side_effect = [
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
            _response(),
        ]
        _call(client)

    assert client_cls.call_count == 2


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def test_every_vision_call_is_logged_before_and_after(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(vision._client, "generate_vision", lambda **kw: CHART_REPLY)

    with caplog.at_level("INFO", logger=vision.logger.name):
        vision.extract_image(b"x" * 1234, page=3, mime_type="image/png", source_doc="report.pdf")

    messages = [r.getMessage() for r in caplog.records]
    assert messages[0] == "vision call start: report.pdf page 3, payload 1234 bytes"
    assert messages[1].startswith("vision call done: report.pdf page 3 in ") and messages[1].endswith("0 retries, ok")
    assert not [m for m in messages if "slow vision call" in m]


def test_a_failed_call_is_logged_with_its_error_type_and_a_slow_call_with_a_warning(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(vision, "VISION_SLOW_CALL_WARN_SEC", -1.0)  # every call counts as slow
    monkeypatch.setattr(vision, "run_ocr", lambda image: "ocr text")

    def boom(**kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(vision._client, "generate_vision", boom)

    with caplog.at_level("INFO", logger=vision.logger.name):
        vision.extract_image(_png_bytes(), location="embedded_image_2", source_doc="a.docx")

    messages = [r.getMessage() for r in caplog.records]
    assert f"vision call start: a.docx embedded_image_2, payload {len(_png_bytes())} bytes" in messages
    assert any(m.endswith("failed (RuntimeError)") and "a.docx embedded_image_2" in m for m in messages)
    assert any(r.levelname == "WARNING" and r.getMessage().startswith("slow vision call: a.docx embedded_image_2") for r in caplog.records)


def test_the_ingest_passes_the_document_name_into_the_vision_log(monkeypatch, tmp_path, caplog):
    from app.ingestion.pipeline import ingest_files

    monkeypatch.setattr(settings, "VISION_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(vision._client, "generate_vision", lambda **kw: CHART_REPLY)
    buffer = io.BytesIO()
    Image.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3)).save(buffer, format="PNG")

    with caplog.at_level("INFO", logger=vision.logger.name):
        ingest_files([("figure.png", buffer.getvalue())], corpus_scope="persistent")

    assert any(r.getMessage().startswith("vision call start: figure.png image, payload") for r in caplog.records)


# --------------------------------------------------------------------------- #
# --max-units
# --------------------------------------------------------------------------- #
def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "ingest_corpus.py"
    spec = importlib.util.spec_from_file_location("ingest_corpus_units_script", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _images(folder: Path, names: list[str]) -> dict[str, bytes]:
    folder.mkdir()
    made = {}
    for name in names:
        buffer = io.BytesIO()
        Image.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3)).save(buffer, format="PNG")
        made[name] = buffer.getvalue()
        (folder / name).write_bytes(made[name])
    return made


def _run(script, monkeypatch, folder, tmp_path, *flags):
    summary = tmp_path / "summary.json"
    monkeypatch.setattr(sys, "argv", ["ingest_corpus.py", "--corpus-dir", str(folder), "--summary-json", str(summary), *flags])
    return script.main(), json.loads(summary.read_text(encoding="utf-8"))


def test_max_units_stops_cleanly_after_n_uncached_units_and_cached_ones_do_not_count(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "files"
    images = _images(folder, ["a.png", "b.png", "c.png", "d.png"])
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", str(tmp_path / "vision-cache"))
    vision._cache_put(vision._cache_key(images["a.png"], settings.VISION_MODEL), CHART_REPLY)  # a.png is already paid for
    calls = []
    monkeypatch.setattr(vision._client, "generate_vision", lambda **kw: calls.append(1) or CHART_REPLY)

    code, summary = _run(_load_script(), monkeypatch, folder, tmp_path, "--max-units", "2")

    assert code == 0 and len(calls) == 2  # b.png and c.png; a.png was free and d.png was never started
    assert summary["stopped"].startswith("max-units") and summary["vision"]["api_calls"] == 2
    assert summary["store_after"]["chroma_count"] == 0  # a canary indexes nothing
    assert "Stopped cleanly after 2 uncached vision units" in capsys.readouterr().err


def test_without_max_units_the_run_does_everything(tmp_path, monkeypatch):
    folder = tmp_path / "files"
    _images(folder, ["a.png", "b.png", "c.png"])
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", str(tmp_path / "vision-cache"))
    monkeypatch.setattr(vision._client, "generate_vision", lambda **kw: CHART_REPLY)

    code, summary = _run(_load_script(), monkeypatch, folder, tmp_path)

    assert code == 0 and "stopped" not in summary and summary["store_after"]["chroma_count"] == 3


def test_a_max_units_larger_than_the_work_just_finishes(tmp_path, monkeypatch):
    folder = tmp_path / "files"
    _images(folder, ["a.png", "b.png"])
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", str(tmp_path / "vision-cache"))
    monkeypatch.setattr(vision._client, "generate_vision", lambda **kw: CHART_REPLY)

    code, summary = _run(_load_script(), monkeypatch, folder, tmp_path, "--max-units", "10")

    assert code == 0 and "stopped" not in summary and summary["vision"]["api_calls"] == 2
