from google import genai

from app.core.key_rotation import gemini_keys
from app.core.usage import UsageTracker


class GeminiClient:
    """Single wrapper owning key rotation + usage recording for every
    Gemini call. A call that bypasses this wrapper bypasses both —
    that's obvious in review, and is the whole point of D-32.
    """

    def __init__(self, tracker: UsageTracker | None = None):
        self.tracker = tracker if tracker is not None else UsageTracker()

    def generate(self, stage: str, model: str, prompt: str) -> str:
        api_key = gemini_keys.next()
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )

        usage = response.usage_metadata
        self.tracker.record(
            stage=stage,
            model=model,
            prompt_tokens=usage.prompt_token_count,
            output_tokens=usage.candidates_token_count,
        )

        return response.text