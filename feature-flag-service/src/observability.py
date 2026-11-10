import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.config import settings


logger = logging.getLogger("feature_flags")
logger.setLevel(logging.getLevelNamesMapping().get(settings.LOG_LEVEL.upper(), logging.INFO))
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Записать структурированное событие приложения в отдельной JSON-строке."""
    logger.log(level, json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": logging.getLevelName(level),
        "event": event,
        **fields,
    }, ensure_ascii=False))
