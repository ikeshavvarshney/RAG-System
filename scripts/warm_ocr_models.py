#!/usr/bin/env python3
"""Download the PP-StructureV3 models once, so the OCR structure baseline (RQ2, D-27) runs offline afterwards.

Needs network access the first time. It builds the same pipeline the app builds when OCR_ENGINE=paddle-structure,
which makes PaddleX fetch the models the pipeline lists, then runs ONE prediction on a small synthetic table image.
That second step matters: PaddleX creates some models (the table-orientation classifier) only on the first table
prediction, so building the pipeline alone leaves them undownloaded and the first real table fails offline. The
script then prints what is cached and how big it is. It never touches Gemini or Tavily. The weights live in your
home directory, not in the repository, and must not be committed.

    backend/.venv/Scripts/python scripts/warm_ocr_models.py

After it succeeds, run with HF_HUB_OFFLINE=1 and PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True to prove nothing is
fetched. The options below mirror _STRUCTURE_OPTIONS in backend/app/ingestion/extractors/paddle_ocr.py; a test keeps
the two in step.
"""

import os
import sys
import time
from pathlib import Path

# Keep identical to backend/app/ingestion/extractors/paddle_ocr.py::_STRUCTURE_OPTIONS (tests/test_ocr.py checks it).
STRUCTURE_OPTIONS = dict(
    enable_mkldnn=False,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    use_seal_recognition=False,
    use_formula_recognition=False,
    use_chart_recognition=False,
    use_table_recognition=True,
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
)


def cache_roots() -> list[Path]:
    paddlex = Path(os.environ.get("PADDLE_PDX_CACHE_HOME", Path.home() / ".paddlex")) / "official_models"
    return [paddlex]


def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def describe_cache() -> dict[str, int]:
    models: dict[str, int] = {}
    for root in cache_roots():
        if not root.is_dir():
            print(f"  (no cache directory at {root})")
            continue
        for model in sorted(p for p in root.iterdir() if p.is_dir()):
            models[str(model)] = folder_size(model)
    return models


def print_cache(title: str, models: dict[str, int]) -> None:
    print(f"\n{title}: {len(models)} models, {sum(models.values()) / 1e6:.1f} MB")
    for path, size in models.items():
        print(f"  {size / 1e6:8.1f} MB  {path}")


def synthetic_table():
    """A 4x3 ruled table with text, drawn with numpy and OpenCV (both installed with paddleocr)."""
    import cv2
    import numpy as np

    image = np.full((300, 520, 3), 255, dtype=np.uint8)
    left, top, cell_w, cell_h = 30, 30, 150, 60
    for r in range(5):
        cv2.line(image, (left, top + r * cell_h), (left + 3 * cell_w, top + r * cell_h), (0, 0, 0), 2)
    for c in range(4):
        cv2.line(image, (left + c * cell_w, top), (left + c * cell_w, top + 4 * cell_h), (0, 0, 0), 2)
    for r in range(4):
        for c in range(3):
            label = ("Year", "Region", "Value")[c] if r == 0 else (str(2019 + r), "North", str(10 * r + c))[c]
            cv2.putText(image, label, (left + c * cell_w + 12, top + r * cell_h + 38), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    return image


def main() -> int:
    try:
        from paddleocr import PPStructureV3
    except ImportError as exc:
        print(f"paddleocr is not installed in this Python ({exc}). Use backend/.venv, or: pip install -e \"backend[ocr]\"")
        return 2

    before = describe_cache()
    print_cache("Cached before", before)
    print("\nLoading PP-StructureV3 (downloads any missing model; this can take several minutes)...")

    started = time.perf_counter()
    try:
        pipeline = PPStructureV3(**STRUCTURE_OPTIONS)
        print("\nPipeline built. Running one prediction on a synthetic table so lazily created models download too...")
        list(pipeline.predict(synthetic_table()))
    except Exception as exc:  # noqa: BLE001 - report exactly why, never fall back quietly
        print(f"\nFAILED after {time.perf_counter() - started:.0f}s: {type(exc).__name__}: {exc}")
        print("The structure baseline cannot run until this succeeds. No fallback was attempted.")
        return 1
    elapsed = time.perf_counter() - started

    after = describe_cache()
    print_cache("Cached after", after)
    added = [Path(p).name for p in after if p not in before]
    print(f"\nLoaded in {elapsed:.0f}s. Newly downloaded: {', '.join(added) or 'nothing (already cached)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
