"""Resolve route-dependent real places before planning or scheduling.

The gate is deliberately deterministic: it may replace an LLM identity with a
provider-backed canonical identity, but it never invents coordinates or turns
an unresolved real venue into a generated experience.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .place_resolution import PlaceResolver, ResolutionContext


REAL_PLACE_TYPES = {'sight', 'restaurant', 'chain', 'hotel', 'transport'}


def is_generated_experience(place: dict) -> bool:
    return (place.get('place_type') == 'generated_experience' or
            (place.get('type') == 'experience' and place.get('entity_kind') == 'experience_concept'
             and not place.get('linked_place_id')))


def is_route_dependent_real_place(place: dict) -> bool:
    if is_generated_experience(place):
        return False
    return (place.get('type') in REAL_PLACE_TYPES or place.get('place_type') in REAL_PLACE_TYPES or
            (place.get('type') == 'experience' and place.get('entity_kind') == 'poi'))


def is_resolved_real_place(place: dict) -> bool:
    return bool(
        is_route_dependent_real_place(place)
        and place.get('provider') == 'amap'
        and place.get('provider_place_id')
        and place.get('latitude') is not None
        and place.get('longitude') is not None
        and not str(place.get('id', '')).startswith('llm_')
    )


def needs_resolution(place: dict) -> bool:
    return is_route_dependent_real_place(place) and not is_resolved_real_place(place)


def replace_place_references(value: Any, mapping: dict[str, str]) -> Any:
    """Replace exact canonical IDs recursively, including assignment-map keys."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [replace_place_references(item, mapping) for item in value]
    if isinstance(value, dict):
        return {mapping.get(key, key): replace_place_references(item, mapping)
                for key, item in value.items()}
    return value


@dataclass
class ResolutionGateResult:
    id_mapping: dict[str, str] = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)
    attempted: int = 0
    resolved: int = 0
    unresolved: int = 0
    skipped_generated: int = 0
    traces: list[dict] = field(default_factory=list)

    @property
    def resolution_rate(self) -> float:
        return round(self.resolved / self.attempted, 3) if self.attempted else 1.0

    def metrics(self) -> dict:
        return {
            'attempted_real_poi_count': self.attempted,
            'resolved_real_poi_count': self.resolved,
            'unresolved_real_poi_count': self.unresolved,
            'skipped_generated_experience_count': self.skipped_generated,
            'real_poi_resolution_rate': self.resolution_rate,
            'resolution_traces': self.traces,
        }


async def resolve_scheduled_place(place: dict,resolver: PlaceResolver,
                                  context: ResolutionContext|None=None):
    """Resolve one route-dependent stop before the scheduler sees it."""
    if not needs_resolution(place):return None
    return await resolver.resolve_place(place,context)


async def enforce_pre_schedule_resolution(
    places: list[dict], resolver: PlaceResolver, referenced_ids: Iterable[str] | None = None,
    contexts: dict[str, ResolutionContext] | None = None,
) -> ResolutionGateResult:
    """Resolve every relevant real place and retain explicit unresolved evidence."""
    result = ResolutionGateResult()
    selected = set(referenced_ids) if referenced_ids is not None else None
    for place in places:
        old_id = str(place.get('id', ''))
        if selected is not None and old_id not in selected:
            continue
        if is_generated_experience(place):
            result.skipped_generated += 1
            continue
        if not is_route_dependent_real_place(place):
            continue
        result.attempted += 1
        resolution=await resolve_scheduled_place(place,resolver,(contexts or {}).get(old_id))
        result.traces.append({
            'original_place_id':old_id,
            'original_name':place.get('display_name') or place.get('local_name') or old_id,
            'status':resolution.status if resolution else 'already_resolved',
            'queries':resolution.queries if resolution else [],
            'confidence':resolution.confidence if resolution else place.get('source_confidence'),
            'selected_place_id':place.get('id') if is_resolved_real_place(place) else None,
            'selected_provider_place_id':place.get('provider_place_id'),
            'resolution_reason':resolution.resolution_reason if resolution else 'already provider-backed',
            'candidates':([candidate.model_dump(mode='json') for candidate in resolution.candidates]
                          if resolution else []),
        })
        if is_resolved_real_place(place):
            result.resolved += 1
            if old_id and old_id != place.get('id'):
                result.id_mapping[old_id] = place['id']
            place['source_metadata'] = {
                **(place.get('source_metadata') or {}),
                'pre_schedule_resolution_status': 'resolved',
            }
            continue
        # Preserve established internal IDs on provider outages/no-match. An
        # existing llm_* identity remains visibly unresolved, while historical
        # offline/manual IDs do not churn merely because the provider is down.
        if old_id and not old_id.startswith('llm_'):
            place['id']=old_id
        result.unresolved += 1
        place['verification_status'] = 'unverified'
        place['source_metadata'] = {
            **(place.get('source_metadata') or {}),
            'pre_schedule_resolution_status': 'REAL_POI_UNRESOLVED',
        }
        result.warnings.append({
            'pointer': f'/places/{place.get("id", old_id)}',
            'code': 'REAL_POI_UNRESOLVED',
            'severity': 'warning',
            'day': None,
            'place_id': place.get('id', old_id),
            'message': f'{place.get("display_name") or place.get("local_name") or old_id} 未能可靠绑定高德 POI，路线只能使用显式 fallback。',
            'invalid_value': old_id,
            'metadata': {'resolution_status': 'REAL_POI_UNRESOLVED',
                         'resolver_status':resolution.status if resolution else 'unresolved',
                         'resolution_reason':resolution.resolution_reason if resolution else 'missing provider identity or coordinates',
                         'queries':resolution.queries if resolution else [],
                         'candidate_count':len(resolution.candidates) if resolution else 0},
        })
    return result


def apply_resolution_mapping(mapping: dict[str, str], *values: Any) -> tuple[Any, ...]:
    return tuple(replace_place_references(value, mapping) for value in values)


def build_day_resolution_contexts(places: list[dict], ordered_ids: Iterable[str],
                                  area_labels: Iterable[str] = ()) -> dict[str, ResolutionContext]:
    """Build neighbor and day-cluster evidence before mutating any place IDs."""
    by_id={str(place.get('id')):place for place in places};ordered=list(dict.fromkeys(map(str,ordered_ids)))
    resolved_points=[(float(by_id[place_id]['latitude']),float(by_id[place_id]['longitude'])) for place_id in ordered
                     if place_id in by_id and by_id[place_id].get('latitude') is not None
                     and by_id[place_id].get('longitude') is not None]
    contexts={}
    for index,place_id in enumerate(ordered):
        previous=next((by_id[ordered[i]] for i in range(index-1,-1,-1)
                       if ordered[i] in by_id and by_id[ordered[i]].get('latitude') is not None),None)
        following=next((by_id[ordered[i]] for i in range(index+1,len(ordered))
                        if ordered[i] in by_id and by_id[ordered[i]].get('latitude') is not None),None)
        point=lambda place:((float(place['latitude']),float(place['longitude'])) if place else None)
        contexts[place_id]=ResolutionContext(previous_coordinates=point(previous),next_coordinates=point(following),
            day_cluster_coordinates=resolved_points,area_labels=list(area_labels))
    return contexts
