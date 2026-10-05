import logging
import sys

from app.core.config import settings

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging() -> None:
    """Give the app.* loggers their own handler, so INFO lines show under uvicorn (whose root level is WARNING)."""
    app_logger = logging.getLogger("app")
    app_logger.setLevel(settings.LOG_LEVEL.upper())
    if not any(getattr(h, "_app_handler", False) for h in app_logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
        handler._app_handler = True  # type: ignore[attr-defined]
        app_logger.addHandler(handler)
