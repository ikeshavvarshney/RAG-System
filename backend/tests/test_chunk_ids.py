"""Deterministic chunk ids: re-ingesting never duplicates, and a changed file replaces its old chunks."""

import re
from pathlib import Path

import pymupdf

from app.ingestion import splitter
from app.ingestion.chunk_ids import ID_HEX_CHARS, make_chunk_id
from app.ingestion.indexer import get_keyword_index, get_vector_store
from app.ingestion.pipeline import ingest_files

ID_ARGS = dict(
    corpus_scope="persistent",
    source_doc="report.pdf",
    extraction_method="text",
    chunk_type="text",
    page=3,
    location="Intro",
    index=0,
    text="Revenue grew eight percent.",
)


def _pdf(*pages: str) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _stored_ids() -> set[str]:
    return {chunk.chunk_id for chunk in get_vector_store().all_chunks()}


def test_same_input_gives_the_same_id():
    first = make_chunk_id(**ID_ARGS)

    assert first == make_chunk_id(**ID_ARGS)
    assert len(first) == ID_HEX_CHARS and re.fullmatch(r"[0-9a-f]+", first)


def test_every_identifying_field_changes_the_id():
    base = make_chunk_id(**ID_ARGS)
    variants = [
        {"corpus_scope": "session:abc"},
        {"source_doc": "other.pdf"},
        {"extraction_method": "ocr"},
        {"chunk_type": "table"},
        {"page": 4},
        {"page": None},
        {"location": "Methods"},
        {"index": 1},
        {"text": "Revenue grew nine percent."},
    ]

    ids = {make_chunk_id(**{**ID_ARGS, **change}) for change in variants}

    assert base not in ids and len(ids) == len(variants)


def test_the_same_text_on_two_pages_does_not_collide():
    chunks = [
        c
        for page in (1, 2)
        for c in splitter.split(
            "The identical sentence appears on every page of this report.",
            {"source_doc": "r.pdf", "page": page, "extraction_method": "text", "corpus_scope": "persistent"},
        )
    ]

    assert len(chunks) == 2 and len({c["chunk_id"] for c in chunks}) == 2


def test_splitting_the_same_text_twice_gives_identical_ids():
    metadata = {"source_doc": "r.pdf", "page": 1, "extraction_method": "text", "corpus_scope": "persistent"}
    text = "\n\n".join(f"Paragraph {i} " + "word " * 120 for i in range(8))

    first = [c["chunk_id"] for c in splitter.split(text, metadata)]
    second = [c["chunk_id"] for c in splitter.split(text, metadata)]

    assert len(first) > 1 and first == second and len(set(first)) == len(first)


def test_ingesting_the_same_file_twice_leaves_both_indexes_unchanged():
    content = _pdf("Quarterly revenue grew strongly across every region this year.")

    ingest_files([("report.pdf", content)], corpus_scope="persistent")
    ids_before = _stored_ids()
    counts_before = (get_vector_store().count(), len(get_keyword_index()))
    ingest_files([("report.pdf", content)], corpus_scope="persistent")

    assert ids_before and _stored_ids() == ids_before
    assert (get_vector_store().count(), len(get_keyword_index())) == counts_before


def test_two_identical_pages_are_two_chunks_and_stay_two_on_reingest():
    content = _pdf(*["The same boilerplate sentence repeats on this page of the report."] * 2)

    ingest_files([("report.pdf", content)], corpus_scope="persistent")
    ingest_files([("report.pdf", content)], corpus_scope="persistent")

    assert get_vector_store().count() == 2 and len(get_keyword_index()) == 2


def test_a_modified_file_replaces_its_old_chunks():
    old = _pdf("Zebra migration patterns were studied across the savanna this season.")
    new = _pdf("Glacier retreat rates were measured across the alpine valleys this decade.")

    ingest_files([("report.pdf", old)], corpus_scope="persistent")
    old_ids = _stored_ids()
    ingest_files([("report.pdf", new)], corpus_scope="persistent")

    assert old_ids and not (old_ids & _stored_ids())
    assert get_vector_store().count() == 1 and len(get_keyword_index()) == 1
    assert get_keyword_index().search("zebra", 5, corpus_scope="persistent") == []
    assert len(get_keyword_index().search("glacier", 5, corpus_scope="persistent")) == 1


def test_a_modified_file_that_now_yields_nothing_still_clears_both_indexes():
    ingest_files([("report.pdf", _pdf("Zebra migration patterns were studied across the savanna."))], "persistent")

    result = ingest_files([("report.pdf", _pdf(""))], corpus_scope="persistent")

    assert result.index.total_indexed == 0
    assert get_vector_store().count() == 0 and len(get_keyword_index()) == 0
    assert result.index.keyword_index_total == 0


def test_reingesting_one_document_leaves_the_others_alone():
    other = _pdf("Hurricane season forecasts were revised upward by the agency.")
    ingest_files([("a.pdf", _pdf("Zebra migration patterns were studied across the savanna.")), ("b.pdf", other)], "persistent")
    b_ids = {c.chunk_id for c in get_vector_store().all_chunks() if c.source_doc == "b.pdf"}

    ingest_files([("a.pdf", _pdf("Glacier retreat rates were measured across alpine valleys."))], "persistent")

    assert {c.chunk_id for c in get_vector_store().all_chunks() if c.source_doc == "b.pdf"} == b_ids
    assert get_vector_store().count() == 2


def test_a_file_that_fails_extraction_keeps_its_existing_chunks():
    ingest_files([("report.pdf", _pdf("Zebra migration patterns were studied across the savanna."))], "persistent")
    ids_before = _stored_ids()

    result = ingest_files([("report.pdf", b"not a real file")], corpus_scope="persistent")

    assert [f.filename for f in result.failed] == ["report.pdf"]
    assert _stored_ids() == ids_before


def test_scopes_do_not_collide_or_clear_each_other():
    content = _pdf("Quarterly revenue grew strongly across every region this year.")
    persistent = ingest_files([("report.pdf", content)], corpus_scope="persistent").chunks
    session = ingest_files([("report.pdf", content)], corpus_scope="session:abc").chunks

    assert {c["chunk_id"] for c in persistent}.isdisjoint({c["chunk_id"] for c in session})
    assert get_vector_store().count() == len(persistent) + len(session)


def test_the_helper_is_the_only_chunk_id_creator():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name == "chunk_ids.py":
            continue
        # uuid4 may mint session ids and nothing else.
        if "uuid" in text and path.name != "session_store.py":
            offenders.append(f"{path.name}: uses uuid")
        for line in text.splitlines():
            if re.search(r"""["']chunk_id["']\s*(:|\]\s*=)""", line) and "make_chunk_id" not in line and "chunk_id\": \"\"" not in line:
                # A dict key or assignment for a chunk id must come straight from the helper; multi-line
                # calls put the helper on the same line as the key.
                offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == []
