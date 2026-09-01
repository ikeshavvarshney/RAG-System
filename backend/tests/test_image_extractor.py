import io

from PIL import Image, ImageDraw

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


def test_image_with_text_yields_nonempty_ocr_chunk():
    image_bytes = _make_text_image("HELLO")

    result = extract(image_bytes, "sample.png")

    assert len(result) == 1
    assert result[0]["extraction_method"] == "ocr"
    assert result[0]["chunk_type"] == "text"
    assert result[0]["page"] is None
    assert len(result[0]["text"]) > 0


def test_blank_image_yields_zero_chunks():
    image_bytes = _make_blank_image()

    result = extract(image_bytes, "blank.png")

    assert result == []