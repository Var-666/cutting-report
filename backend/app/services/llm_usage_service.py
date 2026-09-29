from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.database.llm_usage import LlmUsageRepository


logger = logging.getLogger(__name__)


def _token_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def record_llm_call(settings: Settings, response: Any | None) -> None:
    """Count an HTTP attempt; use only token counts supplied by the provider."""
    total = None
    if response is not None:
        try:
            usage = response.json().get("usage") or {}
            if isinstance(usage, dict):
                total = _token_count(usage.get("total_tokens"))
                if total is None:
                    prompt = _token_count(usage.get("prompt_tokens"))
                    completion = _token_count(usage.get("completion_tokens"))
                    if prompt is not None and completion is not None:
                        total = prompt + completion
        except (ValueError, AttributeError, TypeError):
            pass
    try:
        repository = LlmUsageRepository(settings.metadata_db_path)
        repository.initialize()
        repository.record_call(total)
    except Exception:
        # Usage accounting must not turn an otherwise valid analysis into a failure.
        logger.exception("failed to record LLM usage")
