import io

import pytest
from PIL import Image, ImageDraw

from app.core.config import settings
from app.ingestion.extractors import vision
from app.ingestion.extractors.image import extract


def _make_text_image(text: str) -> bytes:
    img = Image.new("RGB", (400, 100), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 30), text, fill="black")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _make_blank_image() -> bytes:
    img = Image.new("RGB", (200, 100), color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _isolate_vision(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", str(tmp_path / "vision"))


def test_vision_success_yields_vision_chunk(monkeypatch):
    monkeypatch.setattr(
        vision._client,
        "generate_vision",
        lambda **kw: "CONTENT_TYPE: figure\nA hand-drawn organisational chart.",
    )

    result = extract(_make_text_image("ORG"), "sample.png")

    assert len(result) == 1
    assert result[0]["extraction_method"] == "vision"
    assert result[0]["chunk_type"] == "image_caption"
    assert result[0]["page"] is None
    assert "organisational chart" in result[0]["text"]


def test_vision_failure_falls_back_to_ocr(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("vision backend down")

    monkeypatch.setattr(vision._client, "generate_vision", _boom)

    result = extract(_make_text_image("HELLO"), "sample.png")

    assert len(result) == 1
    assert result[0]["extraction_method"] == "ocr"
    assert result[0]["chunk_type"] == "text"
    assert result[0]["page"] is None
    assert len(result[0]["text"]) > 0


def test_blank_image_yields_zero_chunks(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("vision backend down")

    monkeypatch.setattr(vision._client, "generate_vision", _boom)

    assert extract(_make_blank_image(), "blank.png") == []
