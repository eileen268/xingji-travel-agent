"""Deterministic policy separating real experiences from unbound concepts."""
from __future__ import annotations


def experience_status(place: dict) -> str | None:
    if place.get('type') != 'experience':
        return None
    generated = (place.get('place_type') == 'generated_experience' or
                 (place.get('entity_kind') == 'experience_concept' and not place.get('linked_place_id')))
    if generated and not place.get('user_created'):
        return 'suggested_experience'
    if (place.get('provider') == 'amap' and place.get('provider_place_id') and
            place.get('latitude') is not None and place.get('longitude') is not None):
        return 'verified_experience'
    # Legacy/manual/offline records without an explicit concept marker retain
    # their existing verification state instead of being silently relabelled.
    return None


def is_schedulable_experience(place: dict) -> bool:
    """User-created activities remain schedulable; LLM concepts do not."""
    status = experience_status(place)
    return status is None or status == 'verified_experience' or bool(place.get('user_created'))


def apply_experience_policy(places: list[dict]) -> None:
    for place in places:
        place['experience_status'] = experience_status(place)
