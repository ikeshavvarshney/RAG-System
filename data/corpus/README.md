# Corpus

Persistent document corpus for the RAG pipeline. The documents live in
`data/corpus/files/` and are git-ignored; `manifest.csv` beside them is tracked
and is what both members build against. Fetching the manifest's URLs reproduces
an identical corpus on either machine. The derived index directory
(`data/chroma/`) is also git-ignored.

## Collection Rules

- **Size:** 20-50 documents (D-15).
- **Domain:** mixed general domain: news articles, wiki-style and report PDFs,
  assorted DOCX files, and standalone chart or table images.
- **Media families:** at least one of each: `.pdf`, `.docx`, and image
  (`.jpg`, `.jpeg`, `.png`).
- **At least 8 documents with genuine tables, charts, or figures** (D-16). This
  is the ablation-signal floor for RESEARCH-02: a chart-sparse corpus gives the
  study nothing to measure. Annual reports, statistical bulletins, survey PDFs,
  and scanned table screenshots are reliable sources.
- **At least 1 scanned or image-only PDF**, exercising the D-17 code path.
- **At least 1 DOCX with embedded images**, exercising the D-18 code path.
- **Filenames:** ASCII only, hyphenated, no spaces. The validator enforces this,
  as spaces and non-ASCII characters do not transfer reliably between the two
  development machines.
- **Size per file:** the validator warns above 25 MB and errors above 50 MB.
- **Licensing:** every document must be freely redistributable and free of
  personal or confidential data. Document contents are sent to Gemini and Tavily
  during Weeks 2-7.

## Obtaining the Documents

A fresh clone contains `manifest.csv` but no documents. Fetch them before
running anything that reads the corpus.

### 1. Prerequisites

The scripts need `bash`, `curl`, and Python. On Windows, `bash` and `curl` ship
with Git for Windows; run the commands below from Git Bash rather than
PowerShell or `cmd`.

PDF page counting and the scanned-PDF check need `pypdf`, which is already a
backend dependency:

```
cd backend && pip install -e .        # or: pip install pypdf
```

The download itself works without it, but the scanned-PDF check is then reported
as skipped rather than passing.

### 2. Download

From the repository root:

```
chmod +x scripts/fetch_corpus.sh     # first time only
./scripts/fetch_corpus.sh
```

Roughly 40 MB across 50 files, a couple of minutes on a normal connection. The
script reads `filename` and `source_url` from every manifest row and writes into
`data/corpus/files/`, printing `ok`, `skip`, or `FAILED` per file and a failure
count at the end. It also:

- skips files already present, so it is safe to re-run after a partial or failed
  download;
- follows redirects (`curl -L`), which `archive.org` requires;
- sends a browser user agent and a `Referer` header, without which some
  publishers return 403;
- unwraps PDFs delivered inside a `multipart/form-data` envelope, as the NHTSA
  publication API does.

To re-download a single file, delete it and run the script again. Positional
arguments override the defaults (`./scripts/fetch_corpus.sh <manifest>
<output-dir>`), and `PYTHON=` selects an interpreter.

### 3. Measure and validate

```
python scripts/verify_corpus.py                             # write back true page counts
python scripts/validate_corpus.py --strict --write-summary  # enforce the rules above
```

On Windows, call the virtualenv interpreter directly:
`backend/.venv/Scripts/python.exe scripts/verify_corpus.py`.

A clean run ends with `All corpus checks passed.` and regenerates `MANIFEST.md`.

`HANDOFF.md` documents how the manifest was assembled, the licensing decisions
behind it, and pre-verified substitutes should a source URL go dead.

## The Two Scripts

The split between them is deliberate:

- **`verify_corpus.py` measures.** It opens every downloaded file, counts PDF
  pages, and rewrites the `pages` column with true values. The sourcing targets
  the corpus was assembled against (50 files, 15 MB per file, 250 MB total,
  250-400 total pages) are printed as advisory notes, since they describe how
  the document list was chosen rather than what the project requires.
- **`validate_corpus.py` is the gate.** It enforces the collection rules above,
  which derive from the requirements and methodology documents. Nothing else is
  binding. `--strict` exits 1 on any error; `--write-summary` regenerates
  `MANIFEST.md`.

Where the two disagree, the collection rules take precedence. Both need `pypdf`;
without it, the scanned-PDF check is reported as skipped rather than silently
passed.

## Adding a Document

1. Place the file in `data/corpus/files/` with an ASCII, hyphenated,
   space-free name.
2. Add exactly one row to `manifest.csv`:

   | column | rule |
   |---|---|
   | `filename` | must match the file on disk exactly |
   | `doc_type` | `pdf`, `docx`, or `image`; must agree with the file extension |
   | `title` | human-readable title |
   | `source_url` | where it came from |
   | `has_tables_charts` | exactly `yes` or `no` |
   | `pages` | page count; `1` for images |
   | `license_note` | must state terms permitting redistribution |

   No column may be empty.

3. Validate:

   ```
   python scripts/validate_corpus.py --strict --write-summary
   ```

   `--strict` exits 1 on any error. `--write-summary` regenerates `MANIFEST.md`
   (counts by type, chart-dense count and percentage, total MB, per-file table).

4. Commit the manifest row and the regenerated `MANIFEST.md` together. The
   document itself is not committed; the manifest row is what carries it to the
   other machine.
