# 8. Running the System

Every command below is run from the repository root unless stated otherwise. Paths use forward slashes, which work in PowerShell, Git Bash and on Unix alike; the two places where the shell genuinely matters are marked.

---

## 8.1 Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11 or later | Backend. Developed on 3.13. |
| Node.js | 18.18 or later | Frontend. |
| Git Bash or a POSIX shell | any | Only for `scripts/fetch_corpus.sh`. |
| Tesseract | 5.x | The OCR engine, used for scanned pages and when a vision call fails; vendored at `vendor/tesseract/`. |

A Gemini API key is required for ingestion: vision extraction and embedding both call the API. Without one, ingestion fails at the embedding stage.

---

## 8.2 First-Time Setup

### Backend

```bash
cd backend
python -m venv .venv
```

Activate the virtual environment. This is the one command that differs by shell:

```bash
source .venv/Scripts/activate      # Git Bash on Windows
.venv\Scripts\Activate.ps1         # PowerShell
source .venv/bin/activate          # macOS / Linux
```

Install the package in editable mode, with the development extra for the test suite:

```bash
pip install -e ".[dev]"
```

OCR uses the Tesseract binary, not a Python package with model weights. The vendored copy at `vendor/tesseract/` is the default; set `TESSERACT_CMD` in `backend/.env` to point at a system-wide install instead.

Then create the environment file:

```bash
cp .env.example .env
```

Set `GEMINI_API_KEYS` in `backend/.env`. It accepts a comma-separated list, and the client rotates through the pool on rate-limit errors, so more keys means a faster corpus ingest. `backend/.env` is git-ignored; never commit it.

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
```

`NEXT_PUBLIC_API_BASE_URL` defaults to `http://localhost:8000` and only needs changing if the backend runs elsewhere. Nothing secret belongs in this file: every `NEXT_PUBLIC_*` variable is inlined into the browser bundle.

---

## 8.3 Running the Services

### Backend

From `backend/`, with the virtual environment activated:

```bash
uvicorn app.main:app --reload
```

Serves on http://localhost:8000. Confirm it is up:

```bash
curl http://localhost:8000/api/health
# {"status":"ok","version":"0.1.0"}
```

Interactive API documentation is at http://localhost:8000/docs.

To run without activating the environment, call the interpreter directly:

```bash
cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

### Frontend

From `frontend/`:

```bash
npm run dev
```

Serves on http://localhost:3000, with the overview page at `/` and the chat at `/chat`.

Questions asked in the chat go to `POST /api/query/stream`. While the pipeline runs the chat lists each stage as it starts and finishes; the answer then shows numbered citations (a document and page, or a web URL), a fact-check score, and an expandable breakdown of tokens and estimated cost per stage. The cost uses the Gemini list prices in `frontend/lib/pricing.ts`; update them there if the models or prices change.

**Logs.** The backend logs one line per request (`METHOD path status duration id=<request id>`) and one summary line per answered query, at the level set by `LOG_LEVEL` in `backend/.env` (default `INFO`; health checks log at `DEBUG`). The frontend logs the same request line to the browser console with an `[api]` prefix, at `NEXT_PUBLIC_LOG_LEVEL` in `frontend/.env.local`. Both sides print the same request id (the `X-Request-ID` response header), so a browser line can be matched to its backend line.

**Both services must be running to upload documents or ask questions through the browser.** The backend's CORS policy allows exactly one origin, `FRONTEND_ORIGIN`, which defaults to `http://localhost:3000`. Starting the frontend on a different port (`next dev` will do this automatically if 3000 is taken) causes uploads to fail with a CORS error rather than an obvious one. Either free port 3000 or set `FRONTEND_ORIGIN` in `backend/.env` to match.

Production build:

```bash
npm run build
npm run start
```

---

## 8.4 Tests and Checks

Backend suite, from `backend/`:

```bash
pytest -q                          # whole suite
pytest tests/test_ocr.py -q        # one file
pytest -k vision -q                # by name
pytest -q -x --lf                  # stop at first failure, rerun last failures
```

One test runs the real Tesseract binary at `TESSERACT_CMD` and is skipped when that binary is missing.

Frontend checks, from `frontend/`:

```bash
npx tsc --noEmit                   # types
npm run lint                       # eslint
npm run build                      # full build, includes type checking
```

---

## 8.5 Fetching the Corpus

The corpus documents are git-ignored; `data/corpus/manifest.csv` is the tracked artefact and the documents are re-fetchable from it. This keeps a 250 MB corpus out of the repository while letting both machines reproduce an identical one.

**Requires a POSIX shell** (Git Bash on Windows), plus `curl` and a Python interpreter:

```bash
chmod +x scripts/fetch_corpus.sh
./scripts/fetch_corpus.sh
```

Files land in `data/corpus/files/`. The script is safe to re-run: it skips any file already on disk, so an interrupted download resumes by running it again. It prints `ok`, `skip` or `FAILED` per file and a count at the end. Failures are usually a publisher blocking the request or a moved URL, in which case fetch that file by hand and drop it in with the manifest's filename.

Optional arguments override the manifest and output directory:

```bash
./scripts/fetch_corpus.sh path/to/manifest.csv path/to/output
```

If no Python interpreter is found on `PATH`, point at one explicitly:

```bash
PYTHON=python ./scripts/fetch_corpus.sh
```

### Measuring and validating

After fetching, rewrite the manifest's page counts from the actual files:

```bash
pip install pypdf
python scripts/verify_corpus.py
```

Then check the corpus against the collection rules in D-15 to D-18 (document count, table and chart floor, at least one scanned PDF, at least one DOCX with embedded images, filename hygiene):

```bash
python scripts/validate_corpus.py                    # report only
python scripts/validate_corpus.py --strict           # exit 1 on any error
python scripts/validate_corpus.py --write-summary    # regenerate MANIFEST.md
```

---

## 8.6 Ingesting Documents

### Through the browser

Start both services, open http://localhost:3000/chat, and drop files onto the upload panel. Progress is shown for the upload, then for extraction and indexing. The result reports how many passages were indexed, broken down by extraction method, and names any file that failed with its reason.

### Through the API

The endpoint takes a multipart body with one `files` part per document:

```bash
curl -X POST http://localhost:8000/api/ingest \
  -F "files=@data/corpus/files/epa-water-technical-assistance-2023.pdf" \
  -F "files=@data/corpus/files/fao-soil-proposal-template-undated.docx"
```

To send the whole corpus in one request, build the arguments in a loop (Git Bash or any POSIX shell):

```bash
args=(); for f in data/corpus/files/*; do args+=(-F "files=@$f"); done
curl -X POST http://localhost:8000/api/ingest "${args[@]}"
```

The response reports per-file success and failure:

```json
{
  "chunk_count": 22,
  "indexed": {
    "total": 22,
    "by_extraction_method": {"text": 22, "ocr": 0, "vision": 0},
    "failed": 0,
    "vector_store_total": 52,
    "keyword_index_total": 52
  },
  "succeeded": ["epa-water-technical-assistance-2023.pdf", "..."],
  "failed": []
}
```

**Expect a full-corpus ingest to take roughly 30 to 45 minutes.** The model calls are nearly all of it. Across the 50-document corpus the vision plan selects about 74 units (the 12 standalone images, 8 embedded DOCX images, and some 54 PDF pages with tables or charts), each a 15 to 30 second call, and about 560 passages are embedded at the 0.9 second pacing that keeps the embedding API inside its free-tier ceiling. OCR runs locally with Tesseract on the 30 scanned pages of the two CIA PDFs and on any page whose vision reply was unusable, at about a second per page.

A larger key pool reduces rate-limit stalls but not per-call latency, so it helps only if the run is actually being throttled.

Three properties make a run safe to interrupt. Vision responses and OCR results are cached by image content, so a re-run does not pay for either twice. Passage identifiers are deterministic and a re-ingested document replaces its earlier passages, so running the same files again does not duplicate them. And each embedding batch is written as soon as it succeeds.

One property makes it worth batching anyway: `ingest_files` extracts every file before it indexes anything. If a 50-file request dies during extraction, nothing from that request is indexed, and the extraction work (though not the API cost) is repeated.

Limits enforced by the endpoint: 60 files per request, 50 MB per file, and `.pdf`, `.docx`, `.jpg`, `.jpeg`, `.png` only. Type is decided by sniffing magic bytes, not by the extension or the declared Content-Type. An unsupported or oversized file is reported on its own and the rest of the batch still ingests.

---

## 8.7 OCR

Tesseract is the only OCR engine. It runs on scanned PDF pages, which go to it directly, and on pages where the vision reply was unusable. A page takes about a second.

`TESSERACT_CMD` in `backend/.env` names the binary. It defaults to the vendored copy at `vendor/tesseract/tesseract.exe`, and a relative path is resolved against the repository root. If the binary cannot be run, every page that needs OCR is reported as a per-file error naming that path.

Tesseract returns text in reading order and does not recover table rows and columns, so a table on a scanned page is indexed as flattened text. Results are cached in `backend/data/cache/ocr/`, so a run that is stopped and started again does not recognise the same page twice.

---

## 8.8 Data and Reset

| Path | Contents | Tracked |
|---|---|---|
| `backend/data/chroma/` | Vector store and keyword index | No |
| `backend/data/cache/vision/` | Cached vision responses, keyed by image | No |
| `backend/data/cache/ocr/` | Cached OCR text, keyed by image | No |
| `backend/data/cache/embeddings/` | Cached embeddings | No |
| `data/corpus/files/` | Corpus documents | No |
| `data/corpus/manifest.csv` | Corpus manifest | Yes |

To start from an empty index, stop the backend and delete the store:

```bash
rm -rf backend/data/chroma
```

Leave the caches in place unless you are deliberately re-testing extraction: deleting `backend/data/cache/vision/` means every figure is transcribed again, at full API cost. Deleting `backend/data/cache/ocr/` is cheap: every scanned page is recognised again at about a second each.

---

## 8.9 Troubleshooting

**`Cannot reach the backend` in the browser, or a CORS error on upload.** The frontend is not on the origin the backend allows. Check that it is on port 3000, or set `FRONTEND_ORIGIN` in `backend/.env` to the port actually in use.

**`Tesseract unavailable (PermissionError ...)`.** `TESSERACT_CMD` points at a directory rather than at the binary. Windows reports executing a directory as `WinError 5, Access is denied`, which reads like a permissions problem but is not. The value must name the executable, for example `vendor/tesseract/tesseract.exe`. A relative path is resolved against the repository root.

**Every figure comes back as `ocr` and none as `vision`.** The vision calls are failing. Check `GEMINI_API_KEYS` is set and that the log does not show repeated rate limits; the fallback is deliberate and silent by design, so the log is the only place it shows.

**Ingestion appears to hang.** It is most likely working. A figure sent to vision takes 15 to 30 seconds, the request returns only when the whole batch is finished, and nothing is logged per page. Splitting the corpus into smaller batches keeps each run short.

---

## 8.10 Running the Evaluation

The RAGAS harness lives in `eval/` and needs its own extra, installed from `backend/`:

```bash
pip install -e ".[eval]"
```

The judge is a Groq-hosted model rather than Gemini, so that answers are not scored by the model family that wrote them. Set `GROQ_EVAL_API_KEYS` (comma-separated) and `GROQ_EVAL_MODEL` in `backend/.env`.

Run from the repository root. The harness sends each question in `data/eval/reference_set.json` through the live pipeline with the answer cache bypassed, saves the raw results, then scores one question at a time:

```bash
python eval/run_eval.py --audit                        # report on the reference set and exit
python eval/run_eval.py --run-name baseline --limit 5  # first 5 questions
python eval/run_eval.py --run-name baseline --resume   # continue a run that stopped partway
python eval/run_eval.py --from-raw baseline            # score saved raw results without re-running the pipeline
```

Results are written to `eval/results/` (git-ignored) as `<run-name>.raw.json` and `<run-name>.scored.json`. Each question is labelled by where its answer came from (corpus, web, mixed, or none).

A run can change pipeline settings without touching `backend/.env`:

```bash
python eval/run_eval.py --run-name dense-only --set FUSION_DENSE_WEIGHT=1.0   # one override, repeatable
python eval/run_eval.py --run-name naive --preset naive                       # the naive baseline
```

The `naive` preset is dense-only retrieval with no expansion, reranking, or decomposition, read from a separate fixed-size-chunk index. Build that index once, from `backend/`:

```bash
python scripts/ingest_naive.py --dry-run     # count chunks, embed nothing
python scripts/ingest_naive.py               # build data/chroma_naive
```

Compare two scored runs, using only the questions both answered from the corpus:

```bash
python eval/compare_runs.py baseline naive
```
