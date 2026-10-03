import asyncio
from typing import Any

from app.query.pipeline import StageEvent

# Queue item that tells the response generator the pipeline has finished.
DONE = ("__done__", {})


def _stage_payload(event: StageEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {"stage": event.stage, "status": event.status}
    if event.duration_ms is not None:
        payload["duration_ms"] = event.duration_ms
    if event.sub_question is not None:
        payload["sub_question"] = event.sub_question
    return payload


class NullEmitter:
    """Forwards nothing, but records stage events and usage so a non-streaming response can report them."""

    def __init__(self) -> None:
        self.stage_events: list[StageEvent] = []
        self.usage_events: list[dict[str, Any]] = []

    def stage(self, event: StageEvent) -> None:
        self.stage_events.append(event)
        self._publish("stage", _stage_payload(event))

    def usage(self, stage: str, model: str, prompt_tokens: int, output_tokens: int) -> None:
        payload = {
            "stage": stage,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "total_tokens": prompt_tokens + output_tokens,
        }
        self.usage_events.append(payload)
        self._publish("usage", payload)

    def finish(self, name: str, payload: dict[str, Any]) -> None:
        self._publish(name, payload)

    def close(self) -> None:
        pass

    def _publish(self, name: str, payload: dict[str, Any]) -> None:
        pass


class QueueEmitter(NullEmitter):
    """Also puts every event on a queue for the streaming response. Usage arrives from worker threads, so
    every put is handed to the event loop."""

    def __init__(self, queue: "asyncio.Queue[tuple[str, dict[str, Any]]]") -> None:
        super().__init__()
        self._queue = queue
        self._loop = asyncio.get_running_loop()

    def close(self) -> None:
        self._publish(*DONE)

    def _publish(self, name: str, payload: dict[str, Any]) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (name, payload))
