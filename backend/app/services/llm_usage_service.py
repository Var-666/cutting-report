from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.llm_usage import LlmUsageRepository


logger = logging.getLogger(__name__)


def _token_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def record_llm_call(
    settings: Settings, response: Any | None, response_body: dict | None = None, *,
    scene: str = "other", occurred_at: datetime | None = None, duration_ms: int = 0,
) -> None:
    """Count an HTTP attempt; use only token counts supplied by the provider."""
    total = None
    prompt = completion = cached = reasoning = None
    if response is not None:
        try:
            body = response_body if response_body is not None else response.json()
            usage = body.get("usage") or {}
            if isinstance(usage, dict):
                prompt = _token_count(usage.get("prompt_tokens"))
                completion = _token_count(usage.get("completion_tokens"))
                prompt_details = usage.get("prompt_tokens_details")
                completion_details = usage.get("completion_tokens_details")
                if isinstance(prompt_details, dict):
                    cached = _token_count(prompt_details.get("cached_tokens"))
                if isinstance(completion_details, dict):
                    reasoning = _token_count(completion_details.get("reasoning_tokens"))
                total = _token_count(usage.get("total_tokens"))
                if total is None and prompt is not None and completion is not None:
                    total = prompt + completion
        except (ValueError, AttributeError, TypeError):
            pass
    try:
        repository = LlmUsageRepository(settings.metadata_db_path)
        repository.initialize()
        repository.record_call(
            total, occurred_at=(occurred_at or datetime.now(ZoneInfo(settings.timezone))).astimezone(ZoneInfo(settings.timezone)),
            scene=scene if scene in {"leadership", "workshop", "equipment", "alarm", "custom", "welding"} else "other",
            model=settings.llm_model[:256], status="responded" if response is not None else "failed",
            duration_ms=max(0, duration_ms), input_tokens=prompt, output_tokens=completion,
            cached_tokens=cached, reasoning_tokens=reasoning,
        )
    except Exception:
        # Usage accounting must not turn an otherwise valid analysis into a failure.
        logger.exception("failed to record LLM usage")
