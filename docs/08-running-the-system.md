# 8. Running the System

Every command below is run from the repository root unless stated otherwise. Paths use forward slashes, which work in PowerShell, Git Bash and on Unix alike; the two places where the shell genuinely matters are marked.

---

## 8.1 Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11 or later | Backend. Developed on 3.13. |
| Node.js | 18.18 or later | Frontend. |
| Git Bash or a POSIX shell | any | Only for `scripts/fetch_corpus.sh`. |
| Tesseract | 5.x | Optional. Last-resort OCR engine; vendored at `vendor/tesseract/`. |

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

PaddleOCR is deliberately not part of the base dependencies. It pulls roughly seventy packages and a large CPU wheel, and ingestion degrades to Tesseract without it, so it is a separate extra:

```bash
pip install -e ".[ocr]"
```

That extra also carries `paddlex[ocr]`, which PP-StructureV3 needs. Model weights are not bundled: they download to `~/.paddlex/` on the first OCR call, which takes about a minute.

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

**Both services must be running to upload documents through the browser.** The backend's CORS policy allows exactly one origin, `FRONTEND_ORIGIN`, which defaults to `http://localhost:3000`. Starting the frontend on a different port (`next dev` will do this automatically if 3000 is taken) causes uploads to fail with a CORS error rather than an obvious one. Either free port 3000 or set `FRONTEND_ORIGIN` in `backend/.env` to match.

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

The suite exercises the real PaddleOCR engine, so the first run after a clean install pays the one-off model download.

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

**Expect a full-corpus ingest to take roughly half an hour.** Measured against the 50-document corpus: 264 PDF pages, of which only 30 need vision (the two scanned CIA PDFs account for all of them), plus the 12 standalone images. That is 42 vision calls at 15 to 30 seconds each, and about 1,340 passages to embed at the 0.9 second pacing that keeps the embedding API inside its free-tier ceiling. Vision is therefore the smaller cost; most of the time is embedding.

A larger key pool reduces rate-limit stalls but not per-call latency, so it helps only if the run is actually being throttled.

Two properties make a long run safe to interrupt. Vision responses are cached by image content, so a re-run does not pay for transcription twice. Indexing is idempotent, keyed on passage identity, and each embedding batch is written as soon as it succeeds, so re-running an interrupted ingest tops up the index rather than duplicating it.

One property makes it worth batching anyway: `ingest_files` extracts every file before it indexes anything. If a 50-file request dies during extraction, nothing from that request is indexed, and the extraction work (though not the API cost) is repeated.

Limits enforced by the endpoint: 60 files per request, 50 MB per file, and `.pdf`, `.docx`, `.jpg`, `.jpeg`, `.png` only. Type is decided by sniffing magic bytes, not by the extension or the declared Content-Type. An unsupported or oversized file is reported on its own and the rest of the batch still ingests.

---

## 8.7 Switching the OCR Engine

`OCR_ENGINE` in `backend/.env` selects which engine is attempted first. Each falls through to the next when unavailable, so this chooses what is tried, not what is required.

| Value | Cost per page | Use |
|---|---|---|
| `paddle` | ~3s | Default. Plain text recognition. |
| `paddle-structure` | ~75s on CPU | PP-StructureV3: recovers table rows and columns as markdown. The RQ2 baseline. |
| `tesseract` | ~1s | Skips paddle entirely. |

`paddle-structure` needs the `[ocr]` extra installed. It is not the default because at 75 seconds per page a full corpus pass takes hours; turn it on for the RQ2 baseline runs that need recovered tables. See D-27 for the measurements behind that choice.

Note that OCR runs rarely in normal operation. Vision handles figures and scanned pages, so a corpus ingested with a working vision path produces few or no `ocr` passages.

---

## 8.8 Data and Reset

| Path | Contents | Tracked |
|---|---|---|
| `backend/data/chroma/` | Vector store and keyword index | No |
| `backend/data/cache/vision/` | Cached vision responses, keyed by image | No |
| `backend/data/cache/embeddings/` | Cached embeddings | No |
| `data/corpus/files/` | Corpus documents | No |
| `data/corpus/manifest.csv` | Corpus manifest | Yes |

To start from an empty index, stop the backend and delete the store:

```bash
rm -rf backend/data/chroma
```

Leave the caches in place unless you are deliberately re-testing extraction: deleting `backend/data/cache/vision/` means every figure is transcribed again, at full API cost.

---

## 8.9 Troubleshooting

**`Cannot reach the backend` in the browser, or a CORS error on upload.** The frontend is not on the origin the backend allows. Check that it is on port 3000, or set `FRONTEND_ORIGIN` in `backend/.env` to the port actually in use.

**`Tesseract unavailable (PermissionError ...)`.** `TESSERACT_CMD` points at a directory rather than at the binary. Windows reports executing a directory as `WinError 5, Access is denied`, which reads like a permissions problem but is not. The value must name the executable, for example `vendor/tesseract/tesseract.exe`. A relative path is resolved against the repository root.

**Every figure comes back as `ocr` and none as `vision`.** The vision calls are failing. Check `GEMINI_API_KEYS` is set and that the log does not show repeated rate limits; the fallback is deliberate and silent by design, so the log is the only place it shows.

**Ingestion appears to hang.** It is most likely working. A scanned page or a figure takes 15 to 30 seconds, and the request returns only when the whole batch is finished. The backend log reports progress per stage.

**`paddlex` reports a `DependencyError` for PP-StructureV3.** The `[ocr]` extra is not installed: `pip install -e ".[ocr]"` from `backend/`.
