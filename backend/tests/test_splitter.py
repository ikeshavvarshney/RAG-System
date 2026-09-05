from app.core.config import settings
from app.ingestion import splitter
from app.ingestion.splitter import (
    _attach_heading_only_chunks,
    _merge_trailing_fragments,
    _split_fixed_size,
    _token_len,
    split,
)

MIN = settings.CHUNK_MIN_TOKENS
MAX = settings.CHUNK_MAX_TOKENS


def _meta(**overrides) -> dict:
    base = {
        "source_doc": "report.pdf",
        "page": 1,
        "location": None,
        "extraction_method": "text",
    }
    base.update(overrides)
    return base


def _plain_text(n_paragraphs: int, sentences_per_para: int = 12) -> str:
    paras = []
    for p in range(n_paragraphs):
        sentence = (
            f"Sentence {p} carries several ordinary words so the paragraph has bulk. "
        )
        paras.append((sentence * sentences_per_para).strip())
    return "\n\n".join(paras)


# --------------------------------------------------------------------------- #
# Default structure-aware path
# --------------------------------------------------------------------------- #
def test_short_input_yields_exactly_one_chunk():
    chunks = split(_plain_text(1, sentences_per_para=4), _meta())

    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "text"


def test_long_plain_input_yields_multiple_text_chunks():
    chunks = split(_plain_text(40), _meta())

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk["chunk_type"] == "text"
        assert chunk["text"].strip()


def test_every_chunk_carries_schema_fields():
    chunks = split(_plain_text(30), _meta(page=3, extraction_method="ocr"))

    for chunk in chunks:
        assert chunk["chunk_id"]
        assert chunk["source_doc"] == "report.pdf"
        assert chunk["page"] == 3
        assert chunk["extraction_method"] == "ocr"
        assert chunk["chunk_type"] in {"text", "table", "chart", "image_caption"}
        assert "location" in chunk
        # Fields added downstream must NOT be present here.
        assert "corpus_scope" not in chunk
        assert "dense_vector_id" not in chunk
        assert "embedding_model" not in chunk


def test_empty_or_blank_input_yields_no_chunks():
    assert split("", _meta()) == []
    assert split("   \n\n  \t ", _meta()) == []


def test_location_falls_back_to_metadata_when_no_heading():
    chunks = split(_plain_text(10), _meta(location="page-2 body"))

    assert chunks
    assert all(chunk["location"] == "page-2 body" for chunk in chunks)


# --------------------------------------------------------------------------- #
# (a) heading-structured input -> location tracks the nearest heading
# --------------------------------------------------------------------------- #
def test_chunk_location_matches_nearest_preceding_heading():
    def body(marker: str) -> str:
        return (f"{marker} body sentence with enough words to force a split. " * 120).strip()

    text = (
        "# Overview\n\n" + body("OVERVIEWBODY")
        + "\n\n## Methodology\n\n" + body("METHODOLOGYBODY")
        + "\n\n## Findings\n\n" + body("FINDINGSBODY")
    )

    chunks = split(text, _meta())

    assert {c["location"] for c in chunks} == {"Overview", "Methodology", "Findings"}
    for chunk in chunks:
        if "METHODOLOGYBODY" in chunk["text"]:
            assert chunk["location"] == "Methodology"
        if "OVERVIEWBODY" in chunk["text"]:
            assert chunk["location"] == "Overview"
        if "FINDINGSBODY" in chunk["text"]:
            assert chunk["location"] == "Findings"


# --------------------------------------------------------------------------- #
# (b) a markdown table survives intact as one table chunk, even oversized
# --------------------------------------------------------------------------- #
def test_oversized_markdown_table_is_one_intact_table_chunk():
    header = "| Region | Value | Delta | Note |"
    delim = "| --- | --- | --- | --- |"
    rows = [
        f"| TBLROW Region {i} | Value {i * 7} | Delta {i * 3} | note text {i} |"
        for i in range(150)
    ]
    table_md = "\n".join([header, delim, *rows])

    text = (
        "## Data Section\n\nIntro paragraph before the table.\n\n"
        + table_md
        + "\n\nClosing paragraph after the table."
    )

    chunks = split(text, _meta())
    table_chunks = [c for c in chunks if c["chunk_type"] == "table"]

    assert len(table_chunks) == 1
    table = table_chunks[0]
    assert _token_len(table["text"]) > MAX
    assert table["text"].strip() == table_md.strip()
    assert table["text"].count("\n") + 1 == len(rows) + 2  # header + delim + rows
    assert table["location"] == "Data Section"
    # No table row leaked into a non-table chunk.
    assert all("TBLROW" not in c["text"] for c in chunks if c["chunk_type"] != "table")


def test_small_table_still_protected_as_table_chunk():
    table_md = "| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
    text = "Some lead-in prose.\n\n" + table_md + "\n\nSome trailing prose."

    chunks = split(text, _meta())
    table_chunks = [c for c in chunks if c["chunk_type"] == "table"]

    assert len(table_chunks) == 1
    assert table_chunks[0]["text"].strip() == table_md.strip()


def test_lone_pipe_line_is_not_treated_as_a_table():
    text = _plain_text(6) + "\n\n| this is just prose with a pipe char |\n\n" + _plain_text(6)

    chunks = split(text, _meta())

    assert all(c["chunk_type"] == "text" for c in chunks)


# --------------------------------------------------------------------------- #
# (c) plain-text chunk sizes mostly fall inside [MIN, MAX]
# --------------------------------------------------------------------------- #
def test_plain_text_chunk_sizes_mostly_in_range():
    chunks = split(_plain_text(45), _meta())

    assert len(chunks) >= 5
    sizes = [_token_len(c["text"]) for c in chunks]
    in_range = sum(1 for s in sizes if MIN <= s <= MAX)
    assert in_range / len(sizes) >= 0.8
    # Nothing wildly oversized on the plain-text path (no unsplittable table).
    assert all(s <= MAX for s in sizes)


# --------------------------------------------------------------------------- #
# (d) a short trailing fragment merges into the previous chunk
# --------------------------------------------------------------------------- #
def test_short_trailing_fragment_merges_into_previous_chunk():
    body = (
        "Filler sentence with a fair number of everyday words to build bulk. " * 200
    ).strip()
    tail = "TAILSENTINEL a brief closing remark."
    text = body + "\n\n" + tail

    chunks = split(text, _meta())

    assert len(chunks) >= 2
    last = chunks[-1]
    assert "TAILSENTINEL" in last["text"]
    # The tail was folded in, so the final chunk is a full-size chunk, not a
    # sub-minimum standalone fragment.
    assert _token_len(last["text"]) >= MIN
    # And no non-final chunk is a sub-minimum fragment either.
    for chunk in chunks[:-1]:
        assert _token_len(chunk["text"]) >= MIN


def test_merge_trailing_fragments_folds_small_text_tail():
    chunks = [
        {"chunk_id": "1", "text": "big body " * 400, "chunk_type": "text",
         "location": "S1", "source_doc": "d", "page": 1, "extraction_method": "text"},
        {"chunk_id": "2", "text": "tiny tail", "chunk_type": "text",
         "location": "S1", "source_doc": "d", "page": 1, "extraction_method": "text"},
    ]

    _merge_trailing_fragments(chunks)

    assert len(chunks) == 1
    assert "tiny tail" in chunks[0]["text"]


def test_merge_trailing_fragments_never_merges_a_table():
    chunks = [
        {"chunk_id": "1", "text": "| a | b |\n| - | - |", "chunk_type": "table",
         "location": None, "source_doc": "d", "page": 1, "extraction_method": "text"},
        {"chunk_id": "2", "text": "tiny", "chunk_type": "text",
         "location": None, "source_doc": "d", "page": 1, "extraction_method": "text"},
    ]

    _merge_trailing_fragments(chunks)

    assert len(chunks) == 2


def test_attach_heading_only_chunk_folds_into_next_chunk():
    chunks = [
        {"chunk_id": "1", "text": "## Section Two", "chunk_type": "text",
         "location": "Section Two", "source_doc": "d", "page": 1,
         "extraction_method": "text"},
        {"chunk_id": "2", "text": "Body text of the section.", "chunk_type": "text",
         "location": "Section Two", "source_doc": "d", "page": 1,
         "extraction_method": "text"},
    ]

    _attach_heading_only_chunks(chunks)

    assert len(chunks) == 1
    assert chunks[0]["text"].startswith("## Section Two")
    assert "Body text of the section." in chunks[0]["text"]
    assert chunks[0]["location"] == "Section Two"


# --------------------------------------------------------------------------- #
# Fixed-size fallback (Week 2 behaviour), gated behind FIXED_SIZE_MODE
# --------------------------------------------------------------------------- #
def _make_long_text(word_count: int) -> str:
    return " ".join(f"word{i}" for i in range(word_count))


def test_fixed_size_mode_delegates_to_week2_splitter(monkeypatch):
    monkeypatch.setattr(splitter, "FIXED_SIZE_MODE", True)
    text = _make_long_text(2000)

    chunks = split(text, _meta())

    assert len(chunks) > 1
    assert all(c["source_doc"] == "report.pdf" for c in chunks)
    assert all(c["chunk_type"] == "text" for c in chunks)


def test_fixed_size_mode_chunk_dict_is_valid_chunk_model(monkeypatch):
    from app.shared.schemas.chunk import Chunk

    monkeypatch.setattr(splitter, "FIXED_SIZE_MODE", True)
    chunks = split(_make_long_text(2000), _meta(page=2, extraction_method="ocr"))

    assert chunks
    for chunk_dict in chunks:
        # pipeline.py adds corpus_scope downstream before constructing Chunk.
        model = Chunk(**chunk_dict, corpus_scope="persistent")
        assert model.chunk_type == "text"
        assert model.extraction_method == "ocr"


def test_split_fixed_size_short_input_is_single_chunk():
    chunks = _split_fixed_size(_make_long_text(100), _meta())

    assert len(chunks) == 1


def test_split_fixed_size_consecutive_chunks_share_overlap():
    chunks = _split_fixed_size(_make_long_text(2000), _meta())

    first_words = chunks[0]["text"].split()
    second_words = chunks[1]["text"].split()
    assert any(word in second_words[:60] for word in first_words[-60:])
