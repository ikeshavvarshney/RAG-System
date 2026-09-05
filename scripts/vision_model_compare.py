"""D-28: empirical Flash-vs-Pro comparison for the vision extractor (INGEST-02).

Downloads a handful of real corpus figures (Our World in Data / US Census /
NOAA / USGS charts) and renders two real government-report table pages, then
sends each image to every model in MODELS with the same structured
transcription prompt (the same prompt vision.py uses). Raw responses are
written under out/ for side-by-side review.

Usage:
    python scripts/vision_model_compare.py

Requires GEMINI_API_KEYS in backend/.env.

--------------------------------------------------------------------------------
FINDINGS (2026-09-05 run) — PROVISIONAL, Flash-only

The free-tier key has NO usable Gemini Pro quota: gemini-pro-latest and
gemini-3.1-pro-preview returned HTTP 429 on every call (even spaced 45s apart);
gemini-2.5-pro returned HTTP 404 on :generateContent. So no Pro column exists
yet — the comparison must be re-run when a Pro-capable key is available.

gemini-3.6-flash (7/8 figures; 1 transient HTTP 503):
  * Tables: transcribed a two-section USDA table (values + percent-share
    sub-table) as clean markdown, kept footnotes and the source line, and also
    transcribed a second figure on the same page with 13 years of data points.
  * Charts: correctly named chart type (line / stacked bar / dual-axis combo /
    choropleth), title, every axis label with units and visible range,
    legend/series, and per-point value reads (explicitly hedged as approximate
    on dense multi-series lines).
  * Weak spots: multi-series line-chart values are eyeballed; choropleth gave
    value bins + regional examples rather than per-country numbers; it
    sometimes infers structure labels ("Table 4", "Figure 5").
  * Latency 10-43 s/call (mean ~22 s), 600-1630 output tokens.

Decision: keep VISION_MODEL="gemini-3.6-flash" as PROVISIONAL. Re-run this
script against gemini-pro-latest once a paid key is available before locking.
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path

import pymupdf

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "scripts" / "vision_cmp_out"
IMG = OUT / "img"
OUT.mkdir(parents=True, exist_ok=True)
IMG.mkdir(parents=True, exist_ok=True)

KEY = next(
    line.split("=", 1)[1].strip().split(",")[0]
    for line in (REPO / "backend" / ".env").read_text(encoding="utf-8").splitlines()
    if line.startswith("GEMINI_API_KEYS=")
)

# Add Pro ids here once the key has quota, e.g. "gemini-pro-latest".
MODELS = ["gemini-3.6-flash"]

PROMPT = (
    "You are transcribing a figure from a document for a retrieval system.\n"
    "The FIRST line of your reply must be exactly one of:\n"
    "CONTENT_TYPE: table\nCONTENT_TYPE: chart\nCONTENT_TYPE: figure\n"
    "From the next line onward:\n"
    "- table: transcribe it as GitHub-flavored markdown, preserving every row, "
    "column and cell value.\n"
    "- chart: state the chart type, its title, each axis label with units and "
    "visible range, and every data point or series value you can read.\n"
    "- figure: give a one-paragraph factual caption of what is visibly present.\n"
    "Add no interpretation or commentary beyond what is visible."
)

UA = {"User-Agent": "Mozilla/5.0 (RAG-System corpus fetch; research)"}

CHARTS = {
    "owid-life-expectancy": "https://ourworldindata.org/grapher/life-expectancy.png",
    "owid-co2-per-capita": "https://ourworldindata.org/grapher/co-emissions-per-capita.png",
    "owid-gdp-per-capita": "https://ourworldindata.org/grapher/gdp-per-capita-worldbank.png",
    "census-population-growth": "https://www.census.gov/content/dam/Census/library/visualizations/2026/demo/population-growth-slows.jpg",
    "noaa-co2-vs-temp": "https://www.climate.gov/sites/default/files/2025-05/carbon-dioxide-vs-global-temperature-anomaly-1850-2024.png",
    "usgs-eo-satellites": "https://d9-wret.s3.us-west-2.amazonaws.com/assets/palladium/production/s3fs-public/media/images/LRS%20Satellites%20launched%20per%20year-Q2-2026.png",
}
PDFS = {
    "bls-spotlight-2013": "https://www.bls.gov/spotlight/2013/statistics/pdf/statistics.pdf",
    "usda-ers-sugar-2025": "https://ers.usda.gov/sites/default/files/_laserfiche/outlooks/112958/SSS-M-443.pdf",
}


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def _charts() -> dict[str, tuple[bytes, str]]:
    out = {}
    for name, url in CHARTS.items():
        ext = ".jpg" if url.lower().endswith(".jpg") else ".png"
        p = IMG / f"{name}{ext}"
        if not p.exists():
            print(f"  download {name}", flush=True)
            p.write_bytes(_fetch(url))
        raw = p.read_bytes()
        out[name] = (raw, "image/jpeg" if raw[:3] == b"\xff\xd8\xff" else "image/png")
    return out


def _table_pages() -> dict[str, tuple[bytes, str]]:
    out = {}
    for name, url in PDFS.items():
        pdf = IMG / f"{name}.pdf"
        if not pdf.exists():
            print(f"  download {name}.pdf", flush=True)
            pdf.write_bytes(_fetch(url))
        doc = pymupdf.open(pdf)
        best_page, best_cells = 3, 0
        for i in range(min(len(doc), 25)):
            try:
                for t in doc[i].find_tables():
                    if t.row_count * t.col_count > best_cells:
                        best_cells, best_page = t.row_count * t.col_count, i
            except Exception:
                pass
        png = doc[best_page].get_pixmap(dpi=170).tobytes("png")
        (IMG / f"{name}-p{best_page + 1}.png").write_bytes(png)
        out[f"{name}-p{best_page + 1}"] = (png, "image/png")
        doc.close()
    return out


def _call(model: str, image: bytes, mime: str) -> tuple[str, float]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={KEY}"
    body = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(image).decode()}},
            {"text": PROMPT},
        ]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 4096},
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        payload = json.load(r)
    return payload["candidates"][0]["content"]["parts"][0]["text"], time.time() - t0


def main() -> None:
    figures = {**_charts(), **_table_pages()}
    print(f"\n{len(figures)} figures x {len(MODELS)} models\n", flush=True)
    for name, (img, mime) in figures.items():
        for model in MODELS:
            try:
                text, dt = _call(model, img, mime)
                (OUT / f"{name}__{model}.txt").write_text(text, encoding="utf-8")
                print(f"{name:<32} {model:<20} {dt:6.1f}s  chars={len(text)}", flush=True)
            except Exception as e:  # noqa: BLE001 - log and keep going
                print(f"{name:<32} {model:<20} ERROR {type(e).__name__}: {str(e)[:160]}", flush=True)
            time.sleep(3)
    print(f"\nraw outputs: {OUT}", flush=True)


if __name__ == "__main__":
    main()
