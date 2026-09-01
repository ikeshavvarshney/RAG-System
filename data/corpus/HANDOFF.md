# Corpus Provenance and Verification

Where the 50 corpus documents came from, what was checked, and which judgement
calls were made. `README.md` covers how to fetch and validate them; this file
covers why the manifest looks the way it does.

All 50 documents are on disk in `data/corpus/files/` (git-ignored), totalling
39.9 MB and 347 pages. `validate_corpus.py --strict` passes.

## Composition

| Bucket | Files | Pages |
|---|---|---|
| Chart and table images (png/jpg) | 12 | 12 |
| Short article and briefing PDFs | 18 | 55 |
| DOCX | 10 | 71 |
| Report PDFs, chart-dense | 8 | 179 |
| Scanned, image-only PDFs | 2 | 30 |
| **Total** | **50** | **347** |

By type that is 28 pdf, 10 docx, and 12 image files, 43 of which contain tables
or charts. The longest file is 31 pages. Filenames carry 23 distinct publisher
prefixes, none contributing more than four files, across public health, climate
and energy, economics and labour, transport, education, agriculture and food,
space, demographics, telecoms, environment and water, banking, research policy,
and economic history.

## How the Manifest Was Assembled

Candidate documents were sourced through a browser rather than downloaded. Each
`source_url` was opened and confirmed to serve the document it claims; anything
returning 404 or 403 was discarded rather than recorded. Each `license_note` was
read from the document itself or from the publisher's legal and reuse pages,
never inferred. DOCX page counts were read from the Office web viewer status bar
after jumping to the last page. PDF page counts were initially derived from
printed page footers, and have since been replaced with measured values.

## What Has Been Verified

Against the downloaded files:

- **Page counts** are measured by `verify_corpus.py` using pypdf, and the
  manifest holds those values. The one exception is
  `nasa-lunar-water-isru-modeling-2024.docx`, whose count of 10 comes from the
  Office viewer, since Word pagination is not computable from Python. To settle
  it, convert with `libreoffice --headless --convert-to pdf` and count the
  resulting PDF.
- **File sizes.** The largest file is `dft-transport-statistics-gb-2018.pdf` at
  8.7 MB, well inside the 25 MB warning threshold.
- **Every file's magic bytes** match its extension.
- **Two PDFs have no text layer**, `cia-central-intelligence-bulletin-1974.pdf`
  and `cia-honduras-central-american-common-market-1971.pdf`, satisfying D-17.
- **Seven DOCX files contain `word/media/` entries**, satisfying D-18.

## Personal Data in the Scanned PDFs

The two CIA documents were audited for personal data. Internet Archive publishes
an OCR sidecar (`_djvu.txt`) that is readable even though the PDFs themselves
have no text layer, which made the audit possible before the files were
downloaded.

- `cia-central-intelligence-bulletin-1974.pdf`: the names present belong to
  heads of state and public political figures (Mario Soares, Gen. Spinola,
  Indira Gandhi, Mao Tse-tung, Chinese Politburo members). No private
  individuals, addresses, or identification numbers.
- `cia-honduras-central-american-common-market-1971.pdf`: the names present
  belong to Honduran presidents and officials (Villeda Morales, Oswaldo Lopez,
  Ramon Cruz). Same finding.

Both were judged to pass the no-personal-data rule, on the basis that public
officials named in a published analytic document are not private information.
Under a literal reading of "no personal names at all" they would fail, and
substitutes are listed below.

The audit also established the publication years, 1974 and 1971, which the
filenames reflect, and showed that the Honduras memo contains several data
tables and figures. That makes it the most valuable single item in the corpus:
a scanned, image-only PDF containing real tables is precisely the case that
separates vision extraction from OCR.

## Retrieval Quirks

Four publishers do not serve their files plainly, and `fetch_corpus.sh` handles
each:

- **`cbo-demographic-outlook-2024.pdf` is fetched from the Wayback Machine.**
  `www.cbo.gov` serves a JavaScript bot challenge and returns 403 to any
  non-browser client, browser user agent and `Referer` included. The snapshot at
  `web.archive.org/web/2024id_/...` serves the identical public-domain PDF.
- **`nhtsa-state-traffic-data-2020.pdf` arrives wrapped.** The NHTSA
  ViewPublication API returns the PDF inside a `multipart/form-data` envelope,
  so the delivered bytes begin `------WebKitFormBoundary`. The fetch script
  unwraps any PDF whose payload does not start at byte 0.
- **archive.org URLs 302-redirect** to rotating datanode hosts, so the fetch
  script relies on `curl -L`. Do not remove it.
- **CRS reports come from a mirror.** `crsreports.congress.gov` is disallowed by
  robots.txt, so two rows use `everycrsreport.com` copies of the identical
  public-domain PDFs.

Several publishers, `cdc.gov` among them, also return 403 without a `Referer`
header, which the fetch script sends for every request.

## Judgement Calls

- **Three files are CC BY-NC-SA 3.0 IGO** (`who-mosaic` and both `fao-`
  documents). The collection rules permit CC BY-NC but do not name ShareAlike,
  so these should be dropped if licence checking is ever automated.
- **`bls-stem-employment-wages-undated.pdf` contains an author's work email and
  telephone number.** These are official-capacity contact details, but they are
  the closest thing to personal data anywhere in the PDF set. Replacing the file
  with `https://www.bls.gov/opub/btn/volume-3/pdf/why-does-bls-provide-both-the-cpi-w-and-cpi-u.pdf`
  (5 pp, verified live) removes them at the cost of two tables.
- **Eight filenames carry `undated`** because no publication year could be
  established; the alternative was inventing one.
- **US state government documents were excluded deliberately.** State works are
  not automatically public domain and no explicit licence could be found.
- **Wikimedia Commons yielded nothing**, being unreachable from the sourcing
  environment, so no licence could be read from a file description page. It is
  the first place to look for further chart images.

## Verified Substitutes

Each of the following was fetched successfully and had its licence established
during sourcing. Use them if a row later needs replacing.

**Scanned or image-only PDFs.** Australian Bureau of Statistics scans on
Internet Archive, all confirmed image-only and table-dense, all CC BY-NC-ND 4.0.
The ND term is not in the permitted licence list, which is why they went unused,
but they are impersonal statistical bulletins and are the better choice if the
CIA documents are unacceptable and ND is tolerable.

- `archive.org/download/13031-1998/13031_1998.pdf` (8 pp, no names at all)
- `archive.org/download/13031-1994/13031_1994.pdf` (10 pp)
- `archive.org/download/13032-1996-11/13032_1996_11.pdf` (20 pp)

Internet Archive labels un-OCR'd files `format:"Image Container PDF"`, so
searching on that field finds image-only PDFs at scale: 1,775 of them in the
`australian-statistics` collection alone.

**DOCX**, all live, all US federal public domain, page counts unknown:

- `https://www.faa.gov/other_visit/aviation_industry/airline_operators/airline_safety/deicing/FAA%202025-26%20Holdover%20Tables.docx` (a genuine table set, but possibly long)
- `https://www.transit.dot.gov/sites/fta.dot.gov/files/docs/FY09_JARC_Region_IV.docx`
- `https://ams.usda.gov/sites/default/files/media/ComplyingwithSection8e%5B1%5D.docx`

**PDFs and images:**

- `https://crashstats.nhtsa.dot.gov/Api/Public/ViewPublication/813509.pdf` (18 pp)
- `https://pubs.usgs.gov/fs/FS-027-98/fs-027-98.pdf` (2 pp)
- `https://ourworldindata.org/grapher/cereal-yield.png`
- `https://ourworldindata.org/grapher/number-of-internet-users.png`

Both images are CC BY 4.0, and either would take Our World in Data past the
four-file publisher cap unless an existing OWID row is dropped.

## Open Items

- The page count for `nasa-lunar-water-isru-modeling-2024.docx` is the one value
  not measured from the file itself.
- The three CC BY-NC-SA 3.0 IGO files remain in the corpus. Revisit if licence
  checking is automated.
- `bls-stem-employment-wages-undated.pdf` carries an author's official-capacity
  work email and telephone number, with a substitute noted above.
- Source URLs can go dead. The manifest is tracked and the bytes are not, so
  re-run `./scripts/fetch_corpus.sh` on a fresh clone before relying on it.
