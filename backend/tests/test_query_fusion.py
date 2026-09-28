from app.query.fusion import fuse
from app.query.retrieval import RetrievalHit, RetrievalResult


def _vector(chunk_id: str, score: float, query: str) -> RetrievalHit:
    return RetrievalHit(chunk_id, f"text {chunk_id}", {"source_doc": f"{chunk_id}.pdf"}, score, "vector", query)


def _keyword(chunk_id: str, score: float) -> RetrievalHit:
    return RetrievalHit(chunk_id, f"text {chunk_id}", {"source_doc": f"{chunk_id}.pdf"}, score, "keyword")


def _result() -> RetrievalResult:
    return RetrievalResult(
        vector_hits=[
            _vector("dense_only", 0.9, "q1"),
            _vector("both", 0.8, "q1"),
            _vector("dense_only", 0.9, "q2"),
            _vector("both", 0.7, "q2"),
        ],
        keyword_hits=[_keyword("sparse_only", 12.0), _keyword("both", 9.0)],
    )


def _ids(candidates) -> list[str]:
    return [c.chunk_id for c in candidates]


def test_chunk_found_by_both_retrievers_ranks_first():
    fused = fuse(_result(), dense_weight=0.5, k=60)

    assert _ids(fused)[0] == "both"


def test_duplicates_collapse_with_provenance_from_every_list():
    fused = fuse(_result(), dense_weight=0.5, k=60)

    assert len(fused) == len(set(_ids(fused))) == 3
    both = next(c for c in fused if c.chunk_id == "both")
    assert {(p.retriever, p.query, p.rank) for p in both.provenance} == {
        ("vector", "q1", 2),
        ("vector", "q2", 2),
        ("keyword", None, 2),
    }


def test_provenance_keeps_the_raw_retriever_scores():
    fused = fuse(_result(), dense_weight=0.5, k=60)

    both = next(c for c in fused if c.chunk_id == "both")
    assert {(p.retriever, p.query, p.score) for p in both.provenance} == {
        ("vector", "q1", 0.8),
        ("vector", "q2", 0.7),
        ("keyword", None, 9.0),
    }


def test_dense_weight_reorders_results():
    dense_heavy = _ids(fuse(_result(), dense_weight=0.9, k=60))
    sparse_heavy = _ids(fuse(_result(), dense_weight=0.1, k=60))

    assert dense_heavy.index("dense_only") < dense_heavy.index("sparse_only")
    assert sparse_heavy.index("sparse_only") < sparse_heavy.index("dense_only")


def test_many_dense_lists_do_not_outvote_one_sparse_list():
    result = RetrievalResult(
        vector_hits=[_vector("dense_top", 0.9, f"q{i}") for i in range(4)],
        keyword_hits=[_keyword("sparse_top", 5.0)],
    )

    scores = {c.chunk_id: c.score for c in fuse(result, dense_weight=0.5, k=60)}

    assert scores["dense_top"] == scores["sparse_top"]


def test_extreme_weights_give_single_channel_rankings():
    assert _ids(fuse(_result(), dense_weight=1.0, k=60)) == ["dense_only", "both"]
    assert _ids(fuse(_result(), dense_weight=0.0, k=60)) == ["sparse_only", "both"]


def test_empty_channels_are_handled():
    assert fuse(RetrievalResult(), dense_weight=0.5, k=60) == []
    only_sparse = RetrievalResult(keyword_hits=[_keyword("a", 1.0)])
    assert _ids(fuse(only_sparse, dense_weight=0.5, k=60)) == ["a"]
