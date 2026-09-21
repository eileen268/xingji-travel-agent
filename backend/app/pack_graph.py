"""Versioned dependency graph for resumable seven-module builds."""
from __future__ import annotations

import hashlib
import json
from .versions import PIPELINE_VERSION, PROMPT_VERSION, SCHEMA_VERSION
from .contracts import contract_version

PACK_PROMPT_VERSIONS = {"itinerary-plan": "candidate-backups-2"}
PACK_SCHEMA_VERSIONS = {"itinerary-plan": "candidate-backups-2", "itinerary-allocation": "ownership-1"}

BASE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "framing": (),
    "places-core": ("framing",),
    "places-experiences": ("places-core",),
    "places-food": ("places-core", "places-experiences"),
    "itinerary-plan": ("places-core", "places-experiences", "places-food"),
    "itinerary-allocation": ("itinerary-plan",),
    "modules-language-notes": ("framing",),
}


def dependencies(pack_id: str, days: int) -> tuple[str, ...]:
    if pack_id.startswith("itinerary-day-"):
        return ("itinerary-allocation",)
    if pack_id == "itinerary-coherence":
        return ("itinerary-allocation", *(f"itinerary-day-{i}" for i in range(1, days + 1)))
    if pack_id == "modules-practical":
        return ("places-experiences", "places-food", "itinerary-coherence")
    if pack_id == "destination-profile":
        return ("places-core", "places-experiences", "places-food", "itinerary-coherence", "modules-practical", "modules-language-notes")
    return BASE_DEPENDENCIES.get(pack_id, ())


def pack_ids(days: int) -> list[str]:
    return [
        "framing", "places-core", "places-experiences", "places-food", "itinerary-plan", "itinerary-allocation",
        *(f"itinerary-day-{i}" for i in range(1, days + 1)),
        "itinerary-coherence", "modules-practical", "modules-language-notes", "destination-profile",
    ]


def descendants(pack_id: str, days: int) -> set[str]:
    result: set[str] = set()
    changed = True
    while changed:
        changed = False
        for candidate in pack_ids(days):
            if candidate != pack_id and candidate not in result and any(d == pack_id or d in result for d in dependencies(candidate, days)):
                result.add(candidate); changed = True
    return result


def signature(pack_id: str, normalized_input: dict, dependency_signatures: dict[str, str]) -> str:
    prompt_version="day-decisions-1" if pack_id.startswith("itinerary-day-") else PACK_PROMPT_VERSIONS.get(pack_id,PROMPT_VERSION)
    schema_version=contract_version(pack_id)
    body = {
        "pack_id": pack_id,
        "input": normalized_input,
        "dependencies": dependency_signatures,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "pipeline_version": PIPELINE_VERSION,
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
