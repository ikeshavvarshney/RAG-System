from unittest.mock import MagicMock, patch

from app.core.gemini_client import GeminiClient
from app.core.usage import UsageTracker


def test_generate_records_usage_from_response_metadata():
    tracker = UsageTracker()
    client = GeminiClient(tracker=tracker)

    mock_response = MagicMock()
    mock_response.text = "mocked response"
    mock_response.usage_metadata.prompt_token_count = 10
    mock_response.usage_metadata.candidates_token_count = 5

    with patch("app.core.gemini_client.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.return_value = mock_response

        result = client.generate(stage="test_stage", model="gemini-3.6-flash", prompt="hi")

    assert result == "mocked response"
    assert tracker.total_tokens() == 15
    assert tracker.by_stage() == {"test_stage": 15}


def test_multiple_calls_sum_correctly_by_stage():
    tracker = UsageTracker()
    client = GeminiClient(tracker=tracker)

    mock_response = MagicMock()
    mock_response.text = "mocked"
    mock_response.usage_metadata.prompt_token_count = 10
    mock_response.usage_metadata.candidates_token_count = 10

    with patch("app.core.gemini_client.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.return_value = mock_response

        client.generate(stage="stage_a", model="gemini-3.6-flash", prompt="hi")
        client.generate(stage="stage_a", model="gemini-3.6-flash", prompt="hi")
        client.generate(stage="stage_b", model="gemini-3.6-flash", prompt="hi")

    assert tracker.total_tokens() == 60
    assert tracker.by_stage() == {"stage_a": 40, "stage_b": 20}