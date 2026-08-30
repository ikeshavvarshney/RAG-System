# Corpus

Persistent document corpus for the RAG pipeline. Tracked in git, so that both
members build against identical documents. The derived index directory
(`data/chroma/`) is git-ignored.

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
  personal or confidential data. The corpus is committed to git, and its contents
  are sent to Gemini and Tavily during Weeks 2-7.

## Adding a Document

1. Drop the file in `data/corpus/` with an ASCII, hyphenated, space-free name.
2. Add exactly one row to `manifest.csv`:

   | column | rule |
   |---|---|
   | `filename` | must match the file on disk exactly |
   | `doc_type` | `pdf`, `docx`, or `image`; must agree with the file extension |
   | `title` | human-readable title |
   | `source_url` | where it came from |
   | `has_tables_charts` | exactly `yes` or `no` |
   | `license_note` | must state terms permitting redistribution |

   No column may be empty.

3. Validate:

   ```
   python scripts/validate_corpus.py --strict --write-summary
   ```

   `--strict` exits 1 on any error. `--write-summary` regenerates `MANIFEST.md`
   (counts by type, chart-dense count and percentage, total MB, per-file table).

4. Commit the document, the manifest row and the regenerated `MANIFEST.md`
   together.

`README.md`, `manifest.csv`, and `MANIFEST.md` are excluded from the document count.
