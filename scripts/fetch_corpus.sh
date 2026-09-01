#!/usr/bin/env bash
# Downloads the corpus described in data/corpus/manifest.csv into
# data/corpus/files/, which is git-ignored: the manifest is the tracked artefact,
# the bytes are re-fetchable from it.
# Run from a machine with normal internet access. Requires: bash, curl, python3.
#   chmod +x scripts/fetch_corpus.sh && ./scripts/fetch_corpus.sh
set -uo pipefail
MANIFEST="${1:-data/corpus/manifest.csv}"
OUT="${2:-data/corpus/files}"
# python3 does not exist on a default Windows install; python does.
PY_BIN="${PYTHON:-}"
if [ -z "$PY_BIN" ]; then
  for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "" >/dev/null 2>&1; then
      PY_BIN="$candidate"; break
    fi
  done
fi
if [ -z "$PY_BIN" ]; then echo "no python interpreter found; set PYTHON=..." >&2; exit 1; fi

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

PAIRS="$(mktemp)"
trap 'rm -f "$PAIRS"' EXIT
mkdir -p "$OUT"
fail=0
"$PY_BIN" - "$MANIFEST" <<'PY' > "$PAIRS"
import csv, sys
# On Windows, text-mode stdout emits CRLF, leaving a trailing \r on every URL,
# which curl rejects as "Malformed input to a URL function".
sys.stdout.reconfigure(newline='\n')
with open(sys.argv[1], newline='', encoding='utf-8') as fh:
    for r in csv.DictReader(fh):
        print(r['filename'] + '\t' + r['source_url'])
PY
while IFS=$'\t' read -r name url; do
  name="${name%$'\r'}"; url="${url%$'\r'}"
  [ -n "$name" ] || continue
  if [ -s "$OUT/$name" ]; then echo "skip   $name"; continue; fi
  # Some publishers (cdc.gov among them) 403 a request with no Referer.
  origin="$(printf '%s' "$url" | sed -E 's#^(https?://[^/]+).*#\1#')"
  # -L follows redirects (archive.org redirects to rotating datanodes)
  if curl -fsSL --max-time 180 -A "$UA" -H "Accept: */*" -H "Referer: $origin/" \
       -o "$OUT/$name" "$url"; then
    # NHTSA's ViewPublication API returns the PDF inside a multipart/form-data
    # envelope. Unwrap anything whose payload does not start at byte 0.
    case "$name" in
      *.pdf)
        if [ "$(head -c 4 "$OUT/$name")" != "%PDF" ]; then
          if "$PY_BIN" - "$OUT/$name" <<'PY'
import sys
p = sys.argv[1]
data = open(p, 'rb').read()
start = data.find(b'%PDF')
end = data.rfind(b'%%EOF')
if start == -1 or end == -1:
    raise SystemExit(1)
open(p, 'wb').write(data[start:end + 5])
PY
          then echo "       unwrapped multipart envelope: $name"
          else echo "FAILED $name  <- not a PDF and no PDF payload inside"
               rm -f "$OUT/$name"; fail=$((fail+1)); continue
          fi
        fi
        ;;
    esac
    echo "ok     $name  ($(du -h "$OUT/$name" | cut -f1))"
  else
    echo "FAILED $name  <- $url"; rm -f "$OUT/$name"; fail=$((fail+1))
  fi
done < "$PAIRS"
echo "---"; echo "failed downloads: $fail"; echo "files on disk: $(ls -1 "$OUT" | wc -l)"
