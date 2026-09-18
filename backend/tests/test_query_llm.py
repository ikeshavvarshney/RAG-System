import asyncio
from unittest.mock import MagicMock, patch

from google.genai import types

from app.core.config import settings
from app.core.gemini_client import GeminiClient
from app.query import llm
from app.query.expansion import expand_query
from app.query.greeting import detect_greeting
from app.query.guardrails.input import check_input


def test_generate_disables_thinking_and_caps_output(monkeypatch):
    client = MagicMock()
    client.generate.return_value = "ok"
    monkeypatch.setattr(llm, "_client", client)

    assert asyncio.run(llm.generate("stage", "prompt", 123)) == "ok"

    stage, model, prompt, config = client.generate.call_args.args
    assert (stage, model, prompt) == ("stage", settings.QUERY_MODEL, "prompt")
    assert config.thinking_config.thinking_budget == 0
    assert config.max_output_tokens == 123


def test_gemini_client_passes_config_to_the_sdk():
    config = types.GenerateContentConfig(max_output_tokens=7)
    with patch("app.core.gemini_client.genai.Client") as client_cls, patch(
        "app.core.gemini_client.gemini_keys.next", return_value="k"
    ):
        generate_content = client_cls.return_value.models.generate_content
        generate_content.return_value.text = "ok"

        GeminiClient().generate("s", "m", "p", config=config)

    assert generate_content.call_args.kwargs["config"] is config


def test_every_query_llm_call_is_token_capped(fake_llm):
    fake_llm.replies["query_guardrail"] = '{"safe": true}'
    fake_llm.replies["query_greeting"] = '{"kind": "other"}'
    fake_llm.replies["query_expansion"] = "[]"

    asyncio.run(check_input("what is in the report?"))
    asyncio.run(detect_greeting("report summary please"))
    asyncio.run(expand_query("what is in the report?"))

    assert len(fake_llm.token_caps) == 3
    assert all(0 < cap <= 400 for cap in fake_llm.token_caps)
