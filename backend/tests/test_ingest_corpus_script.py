"""The ingestion script's run monitor: fail-fast on API errors, call counts, and a clean uninstall. No network."""

import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import settings
from app.ingestion.extractors import vision

CHART_REPLY = "CONTENT_TYPE: chart\nChart type: bar chart. Title: Quarterly revenue. Data points: Q1 10, Q2 12."


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "ingest_corpus.py"
    spec = importlib.util.spec_from_file_location("ingest_corpus_monitor_script", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """One standalone image, an isolated vision cache, and a scripted vision model and OCR."""
    folder = tmp_path / "files"
    folder.mkdir()
    buffer = io.BytesIO()
    Image.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3)).save(buffer, format="PNG")
    (folder / "chart.png").write_bytes(buffer.getvalue())
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", str(tmp_path / "vision-cache"))
    state = {"vision_calls": 0, "ocr_calls": 0, "vision_error": None}

    def generate_vision(*args, **kwargs):
        state["vision_calls"] += 1
        if state["vision_error"]:
            raise RuntimeError(state["vision_error"])
        return CHART_REPLY

    def run_ocr(image):
        state["ocr_calls"] += 1
        return "text read by ocr from the image"

    monkeypatch.setattr(vision._client, "generate_vision", generate_vision)
    monkeypatch.setattr(vision, "run_ocr", run_ocr)
    return folder, state, tmp_path


def _run(script, monkeypatch, folder, tmp_path, *flags) -> tuple[int, dict]:
    summary = tmp_path / "summary.json"
    monkeypatch.setattr(sys, "argv", ["ingest_corpus.py", "--corpus-dir", str(folder), "--summary-json", str(summary), *flags])
    code = script.main()
    return code, json.loads(summary.read_text(encoding="utf-8"))


def test_the_run_stops_at_the_first_api_error_instead_of_falling_back_to_ocr(corpus, monkeypatch, capsys):
    folder, state, tmp_path = corpus
    state["vision_error"] = "429 RESOURCE_EXHAUSTED: quota exceeded"

    code, summary = _run(_load_script(), monkeypatch, folder, tmp_path)

    assert code == 3
    assert state["vision_calls"] == 1 and state["ocr_calls"] == 0  # the error was not turned into an OCR read
    assert "RESOURCE_EXHAUSTED" in summary["stopped"] and "RESOURCE_EXHAUSTED" in summary["api_error"]
    assert summary["store_after"]["chroma_count"] == 0  # nothing was indexed
    assert "first API error" in capsys.readouterr().err


def test_continue_on_api_error_keeps_the_old_fallback_and_records_the_reason(corpus, monkeypatch):
    folder, state, tmp_path = corpus
    state["vision_error"] = "503 service unavailable"

    code, summary = _run(_load_script(), monkeypatch, folder, tmp_path, "--continue-on-api-error")

    assert code == 0 and state["ocr_calls"] == 1
    assert summary["vision"]["fallback_to_ocr"] == 1 and summary["vision"]["fallback_reasons"] == {"vision call failed": 1}
    assert summary["store_after"]["by_extraction_method"] == {"ocr": 1}
    assert summary["ocr"]["calls"] == 1


def test_a_clean_run_reports_calls_and_what_the_store_holds(corpus, monkeypatch):
    folder, state, tmp_path = corpus

    code, summary = _run(_load_script(), monkeypatch, folder, tmp_path)

    assert code == 0 and summary["vision"]["api_calls"] == 1 and summary["vision"]["fallback_to_ocr"] == 0
    after = summary["store_after"]
    assert after["by_extraction_method"] == {"vision": 1} and after["by_chunk_type"] == {"chart": 1}
    assert after["chroma_count"] == after["bm25_count"] == 1
    assert summary["vision_units_planned"] == 1 and summary["vision_cap"] == settings.MAX_VISION_PAGES


def test_a_second_run_makes_no_vision_calls_and_changes_no_counts(corpus, monkeypatch):
    folder, state, tmp_path = corpus
    script = _load_script()

    _, first = _run(script, monkeypatch, folder, tmp_path)
    _, second = _run(script, monkeypatch, folder, tmp_path)

    assert first["vision"]["api_calls"] == 1 and second["vision"]["api_calls"] == 0  # served from the vision cache
    assert second["store_before"] == {"chroma_count": 1, "bm25_count": 1}
    assert second["store_after"]["chroma_count"] == first["store_after"]["chroma_count"] == 1
    assert second["store_after"]["bm25_count"] == 1


def test_the_monitor_removes_itself_after_the_run(corpus, monkeypatch):
    folder, state, tmp_path = corpus
    script = _load_script()
    original_ocr = vision.run_ocr
    original_generate = vision._client.generate_vision

    _run(script, monkeypatch, folder, tmp_path)

    assert vision.run_ocr is original_ocr and vision._client.generate_vision is original_generate
    assert not [h for h in vision.logger.handlers if h.__class__.__name__ == "_FallbackHandler"]
