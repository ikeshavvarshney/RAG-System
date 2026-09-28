from app.core.usage import UsageTracker


def test_total_tokens_sums_prompt_and_output():
    tracker = UsageTracker()
    tracker.record(stage="a", model="gemini-3.6-flash", prompt_tokens=10, output_tokens=5)

    assert tracker.total_tokens() == 15


def test_by_stage_attributes_tokens_correctly():
    tracker = UsageTracker()
    tracker.record(stage="ingest", model="gemini-3.6-flash", prompt_tokens=10, output_tokens=5)
    tracker.record(stage="query", model="gemini-3.6-flash", prompt_tokens=20, output_tokens=10)

    assert tracker.by_stage() == {"ingest": 15, "query": 30}


def test_multiple_entries_same_stage_accumulate():
    tracker = UsageTracker()
    tracker.record(stage="ingest", model="gemini-3.6-flash", prompt_tokens=5, output_tokens=5)
    tracker.record(stage="ingest", model="gemini-3.6-flash", prompt_tokens=5, output_tokens=5)

    assert tracker.total_tokens() == 20
    assert tracker.by_stage() == {"ingest": 20}


def test_request_count_counts_entries_optionally_by_stage():
    tracker = UsageTracker()
    tracker.record("web_search", "tavily", 0, 0)
    tracker.record("web_search", "tavily", 0, 0)
    tracker.record("query", "m", 5, 5)

    assert tracker.request_count() == 3
    assert tracker.request_count("web_search") == 2
    assert tracker.request_count("missing") == 0