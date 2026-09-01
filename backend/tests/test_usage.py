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