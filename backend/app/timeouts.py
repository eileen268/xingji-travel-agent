"""Execution budgets. Provider I/O, packs and the whole task are separate clocks."""
from __future__ import annotations

import os


def _seconds(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def pack_timeout(pack_id: str) -> float:
    if pack_id.startswith("places-"):
        return _seconds("PACK_TIMEOUT_PLACES_SECONDS", 210)
    if pack_id == "itinerary-plan":
        return _seconds("PACK_TIMEOUT_ITINERARY_PLAN_SECONDS", 210)
    if pack_id.startswith("itinerary-day-"):
        return _seconds("PACK_TIMEOUT_ITINERARY_DAY_SECONDS", 210)
    if pack_id == "modules-practical":
        return _seconds("PACK_TIMEOUT_PRACTICAL_SECONDS", 210)
    if pack_id == "modules-language-notes":
        return _seconds("PACK_TIMEOUT_LANGUAGE_SECONDS", 210)
    return _seconds("PACK_TIMEOUT_DEFAULT_SECONDS", 90)


def task_safety_timeout() -> float:
    # A circuit breaker for genuinely stuck workers, not the normal build budget.
    return _seconds("TASK_SAFETY_TIMEOUT_SECONDS", 1800)


def provider_timeout_config() -> dict[str, float]:
    return {
        "connect": _seconds("LLM_CONNECT_TIMEOUT_SECONDS", 15),
        "read": _seconds("LLM_READ_TIMEOUT_SECONDS", 100),
        "write": _seconds("LLM_WRITE_TIMEOUT_SECONDS", 30),
        "pool": _seconds("LLM_POOL_TIMEOUT_SECONDS", 15),
    }


def provider_retry_config() -> dict[str, float | int]:
    try: retries=max(0,min(1,int(os.getenv("MAX_PROVIDER_RETRIES","1"))))
    except ValueError: retries=1
    low=_seconds("PROVIDER_RETRY_BACKOFF_MIN_SECONDS",1)
    high=max(low,_seconds("PROVIDER_RETRY_BACKOFF_MAX_SECONDS",3))
    return {"max_retries":retries,"backoff_min":low,"backoff_max":high}
