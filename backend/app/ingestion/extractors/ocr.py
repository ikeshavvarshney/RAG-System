import pytesseract
from PIL import Image

# Explicit path — keeps the wrapper working regardless of system PATH,
# and makes the dependency visible/portable across teammates' machines.
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


class OCRUnavailable(Exception):
    """Raised when the OCR engine cannot be reached at all."""


def run_ocr(image: Image.Image) -> str:
    """Run OCR on a PIL Image and return extracted text.

    NOTE: PaddleOCR (primary engine per D-27, with table-structure markdown
    output) is deferred for this sprint due to Windows install complexity.
    Tesseract is used directly rather than as a fallback for now. Swapping
    in PaddleOCR later is a drop-in replacement behind this same function.
    """
    try:
        text = pytesseract.image_to_string(image)
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRUnavailable(
            "Tesseract binary not found. Install it and verify "
            "tesseract_cmd path in ocr.py"
        ) from exc

    return text.strip()