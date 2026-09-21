"""Immutable version metadata attached to jobs and packs."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .versions import (ALLOCATOR_VERSION, PIPELINE_VERSION, PROMPT_VERSION,
                       SCHEMA_VERSION, SCHEDULER_VERSION, VALIDATOR_VERSION,
                       POI_RESOLUTION_VERSION, FACT_VERIFIER_VERSION, REPLAN_VERSION)


def generation_manifest(mode: str = "REAL_LLM") -> dict:
    model = os.getenv("ZHIPU_MODEL", "glm-4-plus") if mode == "REAL_LLM" else None
    has_amap=bool((os.getenv("AMAP_API_KEY", "") or os.getenv("AMAP_WEB_SERVICE_KEY", "")).strip())
    return {
        "mode": mode,
        "model_name": model,
        "model_version": os.getenv("ZHIPU_MODEL_VERSION", model or "not-applicable"),
        "prompt_version": PROMPT_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "scheduler_version": SCHEDULER_VERSION,
        "allocator_version": ALLOCATOR_VERSION,
        "poi_provider": "amap" if mode == "REAL_LLM" and has_amap else ("replay" if mode == "REPLAY" else "none"),
        "poi_resolution_version": POI_RESOLUTION_VERSION,
        "route_provider": "amap" if mode == "REAL_LLM" and has_amap else "deterministic_estimate",
        "fact_verifier_version": FACT_VERIFIER_VERSION,
        "replan_version": REPLAN_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
