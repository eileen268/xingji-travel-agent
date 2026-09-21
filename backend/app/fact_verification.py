"""Deterministic fact verification for canonical places and scheduled routes.

This layer never invents facts and never calls an LLM. It classifies evidence
already obtained from providers, deterministic fallbacks, and research packs.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Callable

from .versions import FACT_VERIFIER_VERSION
from .pre_schedule_resolution import is_route_dependent_real_place,is_resolved_real_place
from .experience_policy import experience_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence(source_type: str, provider: str | None, status: str,
              confidence: float, queried_at: str | None = None) -> dict:
    return {
        'source_type': source_type,
        'provider': provider,
        'queried_at': queried_at,
        'confidence': round(max(0.0, min(1.0, confidence)), 3),
        'status': status,
    }


def normalize_dynamic_hours(place: dict) -> bool:
    """Remove unsupported concrete hours while retaining trusted provider facts.

    Returns True when an unverified concrete display value was replaced.
    """
    from .validators import TRUSTED_HOURS_STATUSES, contains_specific_opening_time
    fact=(place.get('official_facts') or {}).get('opening_hours') or {}
    sources=[source for source in fact.get('sources',[]) if source.get('source_type') in
             ('official_website','official_booking','amap','trusted_third_party') and source.get('source_url')]
    trusted=fact.get('status') in TRUSTED_HOURS_STATUSES and bool(sources)
    note=str(place.get('hours_note') or '')
    if contains_specific_opening_time(note) and not trusted:
        place.setdefault('source_metadata',{})['discarded_unverified_hours_note']=note
        place['hours_note']='开放时间尚未核实，出发前请复核。'
        return True
    if not note:
        place['hours_note']='开放时间尚未核实，出发前请复核。'
    return False


def verify_place_facts(place: dict, timestamp: str | None = None) -> list[dict]:
    """Normalize one place's evidence and return non-blocking fact warnings."""
    timestamp = timestamp or _now()
    warnings = []
    normalized_unknown_hours=normalize_dynamic_hours(place)
    provenance = dict(place.get('fact_provenance') or {})
    provider = place.get('provider')
    confidence = float(place.get('source_confidence') or 0)
    hard_fields = ('canonical_name', 'city', 'district', 'address', 'latitude', 'longitude', 'category')

    if provider == 'amap' and place.get('provider_place_id'):
        for field in hard_fields:
            value = place.get(field)
            provenance[field] = _evidence(
                'provider', 'amap', 'verified' if value not in (None, '') else 'unresolved',
                confidence if value not in (None, '') else 0.0, timestamp,
            )
        complete = all(place.get(field) not in (None, '') for field in ('canonical_name', 'city', 'latitude', 'longitude'))
        place['verification_status'] = 'verified' if complete else 'partially_verified'
    elif place.get('place_type') == 'generated_experience':
        place['verification_status'] = 'generated'
        provenance['identity'] = _evidence('model_knowledge', 'zhipu', 'generated', 0.0, timestamp)
    else:
        place['verification_status'] = 'unverified'
        provenance['identity'] = _evidence(
            'offline_reference' if provider == 'offline_reference' else 'model_knowledge',
            provider, 'unresolved', 0.25 if provider == 'offline_reference' else 0.1, timestamp,
        )

    # Dynamic facts retain their provider status. Official web facts override
    # AMap when available; AMap POI 2.0 weekly hours remain usable provenance.
    official_facts=place.get('official_facts') or {}
    for field, fact_name, note_field in (
        ('opening_hours', 'opening_hours', 'hours_note'), ('ticket_rules', 'ticket_info', 'ticket_note'),
        ('reservation_rules', 'reservation_required', 'reservation_note'),
    ):
        resolved=official_facts.get(fact_name)
        provenance[field]=resolved if resolved and resolved.get('status')!='unavailable' else {
            **_evidence('model_knowledge', None, 'needs_recheck', 0.0, timestamp),
            'value': place.get(note_field) or '出发前请复核'}
    opening_hours=official_facts.get('opening_hours') or {}
    if opening_hours.get('status')=='conflicting':
        warnings.append({
            'pointer': f"/places/{place.get('id', '')}/official_facts/opening_hours",
            'code': 'HOURS_SOURCE_CONFLICT',
            'message': '多个可信来源的营业时间存在差异，展示值按来源优先级选择，请以官方最新信息为准。',
        })
    elif normalized_unknown_hours:
        warnings.append({
            'pointer': f"/places/{place.get('id', '')}/official_facts/opening_hours",
            'code': 'FACT_NEEDS_RECHECK',
            'message': '营业时间缺少可信来源，已移除具体数字；出发前请确认开放情况。',
        })
    resolved_official=official_facts.get('official_url')
    provenance['official_url']=resolved_official if resolved_official else _evidence(
        'unknown', None, 'needs_recheck' if place.get('official_url') else 'unresolved',0.0,None)
    provenance['map_url'] = _evidence(
        'derived', 'amap', 'verified' if place.get('map_url') and provider == 'amap' else
        ('estimated' if place.get('map_url') else 'unresolved'),
        confidence if place.get('map_url') else 0.0, timestamp if place.get('map_url') else None,
    )
    place['fact_provenance'] = provenance

    if place['verification_status'] in ('unverified', 'generated'):
        warnings.append({
            'pointer': f"/places/{place.get('id', '')}",
            'code': 'UNVERIFIED_EXPERIENCE' if place['verification_status'] == 'generated' else 'POI_FACTS_UNVERIFIED',
            'message': '该体验没有可靠真实提供方，不能视为可预约地点。' if place['verification_status'] == 'generated'
                       else '该地点尚未可靠绑定真实 POI，名称与位置需要复核。',
        })
    elif place['verification_status'] == 'partially_verified':
        warnings.append({'pointer': f"/places/{place.get('id', '')}", 'code': 'POI_FACTS_PARTIAL',
                         'message': '该地点已绑定真实 POI，但部分地址或坐标事实仍不完整。'})
    return warnings


def verify_trip_facts(places: list[dict], itinerary: list[dict], *, connect: Callable | None = None,
                      job_id: str | None = None, run_id: str | None = None) -> dict:
    """Evaluate hard fact evidence and dynamic-fact freshness for a whole trip."""
    started = _now()
    warnings = []
    scheduled_ids={stop.get('place_id') for day in itinerary for stop in day.get('stops',[])}
    for place in places:
        place_warnings=verify_place_facts(place, started)
        if place.get('id') in scheduled_ids:
            warnings.extend(place_warnings)

    place_by_id = {place['id']: place for place in places}
    invalid_references = []
    verified_routes = estimated_routes = unresolved_routes = route_legs = 0
    scheduled_real_ids=set();unresolved_scheduled=[];unresolved_segments=[]
    for day_index, day in enumerate(itinerary):
        for stop_index, stop in enumerate(day.get('stops', [])):
            place = place_by_id.get(stop.get('place_id'))
            if place is None:
                invalid_references.append({
                    'pointer': f'/itinerary/{day_index}/stops/{stop_index}/place_id',
                    'code': 'INVALID_PLACE_REFERENCE', 'message': '行程引用了不存在的 canonical place_id。',
                })
            elif place.get('city') != day.get('city'):
                invalid_references.append({
                    'pointer': f'/itinerary/{day_index}/stops/{stop_index}/place_id',
                    'code': 'PLACE_CITY_MISMATCH', 'message': '行程地点与当天城市不一致。',
                })
            if place and is_route_dependent_real_place(place):
                scheduled_real_ids.add(place['id'])
                if not is_resolved_real_place(place):
                    unresolved_scheduled.append((day_index,stop_index,place))
        segments=day.get('segments') or [
            {'route_status':stop.get('route_status','unresolved')} for stop in day.get('stops',[])[1:]
        ]
        for segment_index, segment in enumerate(segments):
            route_legs += 1
            status = segment.get('route_status', 'unresolved')
            if status == 'verified': verified_routes += 1
            elif status == 'estimated_by_distance': estimated_routes += 1
            else:
                unresolved_routes += 1
                origin=place_by_id.get(segment.get('from_place_id')) or {}
                destination=place_by_id.get(segment.get('to_place_id')) or {}
                unresolved_segments.append((day_index,segment_index,segment,origin,destination))

    if estimated_routes:
        warnings.append({'pointer': '/itinerary', 'code': 'ROUTE_ESTIMATED',
                         'message': f'{estimated_routes} 段路线使用坐标距离保守估算，出发前请复核。'})
    if unresolved_routes:
        warnings.append({'pointer': '/itinerary', 'code': 'ROUTE_UNRESOLVED',
                         'message': f'{unresolved_routes} 段路线缺少可靠坐标或路线结果，出发前请复核。'})
    for day_index,segment_index,segment,origin,destination in unresolved_segments:
        origin_name=origin.get('display_name') or segment.get('from_place_id') or '上一站'
        destination_name=destination.get('display_name') or segment.get('to_place_id') or '下一站'
        reason=(segment.get('route_error_code') or
                ('ROUTE_INPUT_INVALID' if not is_resolved_real_place(origin) or not is_resolved_real_place(destination)
                 else 'ROUTE_NOT_FOUND'))
        warnings.append({'pointer':f'/itinerary/{day_index}/segments/{segment_index}',
            'code':'ROUTE_SEGMENT_UNRESOLVED',
            'message':f'第 {day_index+1} 天“{origin_name} → {destination_name}”未取得高德路线。',
            'day':day_index+1,'place_id':destination.get('id'),
            'metadata':{'from_place_id':segment.get('from_place_id'),'to_place_id':segment.get('to_place_id'),
                        'reason':reason,'fallback_schedule_minutes':segment.get('fallback_schedule_minutes',0)}})
    for day_index,stop_index,place in unresolved_scheduled:
        warnings.append({'pointer':f'/itinerary/{day_index}/stops/{stop_index}/place_id',
            'code':'UNRESOLVED_REAL_POI',
            'message':f'第 {day_index+1} 天的“{place.get("display_name") or place.get("local_name") or place["id"]}”尚未可靠绑定高德 POI。'})
    dynamic_fact_names = ('opening_hours', 'ticket_info', 'reservation_required')
    scheduled_places=[place_by_id[pid] for pid in scheduled_ids if pid in place_by_id]
    dynamic_facts=[];scheduled_fact_details=[];official_fact_count=provider_fact_count=0
    trusted={'verified','provider_verified'}
    for place in scheduled_places:
        missing=[]
        for name in dynamic_fact_names:
            fact=(place.get('official_facts') or {}).get(name) or {'status':'unavailable'}
            dynamic_facts.append(fact)
            if fact.get('status') not in trusted:missing.append(name)
            if fact.get('status') in trusted:
                provider_fact_count+=1
                if fact.get('source_type') in ('official_website','official_booking'):official_fact_count+=1
        scheduled_fact_details.append({'place_id':place['id'],'place_name':place.get('display_name') or place['id'],
                                       'needs_recheck':missing})
    facts_needing_recheck = sum(
        fact.get('status') in ('needs_recheck', 'partially_verified', 'unavailable', 'conflicting')
        for fact in dynamic_facts
    )
    if facts_needing_recheck:
        affected=sum(bool(item['needs_recheck']) for item in scheduled_fact_details)
        warnings.append({'pointer': '/itinerary', 'code': 'DYNAMIC_FACTS_NEED_RECHECK',
                         'message': f'{affected} 个最终行程地点存在需要出发前确认的动态信息。',
                         'metadata':{'items':scheduled_fact_details}})

    status_counts = {name: sum(place.get('verification_status') == name for place in places)
                     for name in ('verified', 'partially_verified', 'unverified', 'generated')}
    verified_or_partial = status_counts['verified'] + status_counts['partially_verified']
    real_places=[place for place in places if is_route_dependent_real_place(place)]
    resolved_real=sum(is_resolved_real_place(place) for place in real_places)
    scheduled_real=[place_by_id[place_id] for place_id in scheduled_real_ids if place_id in place_by_id]
    scheduled_resolved=sum(is_resolved_real_place(place) for place in scheduled_real)
    ambiguous_scheduled=sum((place.get('source_metadata') or {}).get('resolution_status')=='ambiguous'
                            for place in scheduled_real)
    verified_experiences=sum(experience_status(place)=='verified_experience' for place in places)
    suggested_experiences=sum(experience_status(place)=='suggested_experience' for place in places)
    generated_scheduled=sum(experience_status(place_by_id[pid])=='suggested_experience'
                            for pid in scheduled_ids if pid in place_by_id)
    total_scheduled_facts=len(dynamic_facts)
    summary = {
        'version': FACT_VERIFIER_VERSION,
        'verified_place_count': status_counts['verified'],
        'partially_verified_place_count': status_counts['partially_verified'],
        'unverified_place_count': status_counts['unverified'],
        'generated_experience_count': status_counts['generated'],
        'verified_place_rate': round(verified_or_partial / len(places), 3) if places else 0.0,
        'route_leg_count': route_legs,
        'verified_route_count': verified_routes,
        'estimated_route_count': estimated_routes,
        'unresolved_route_count': unresolved_routes,
        'verified_route_rate': round(verified_routes / route_legs, 3) if route_legs else 0.0,
        'real_poi_resolution_rate': round(resolved_real/len(real_places),3) if real_places else 1.0,
        'scheduled_real_poi_count':len(scheduled_real),
        'resolved_real_poi_count':scheduled_resolved,
        'scheduled_real_poi_resolution_rate':round(scheduled_resolved/len(scheduled_real),3) if scheduled_real else 1.0,
        'ambiguous_real_poi_count':ambiguous_scheduled,
        'unresolved_real_poi_count': len(unresolved_scheduled),
        'llm_place_count_in_final_itinerary': sum(str(pid).startswith('llm_') for pid in scheduled_real_ids),
        'route_segment_count':route_legs,
        'fallback_route_count':estimated_routes+unresolved_routes,
        'route_coverage_rate': round(verified_routes/route_legs,3) if route_legs else 1.0,
        'official_source_count': sum(bool(place.get('official_url')) for place in scheduled_places),
        'verified_dynamic_fact_count': sum(fact.get('status') in ('verified','provider_verified') for fact in dynamic_facts),
        'conflicting_dynamic_fact_count': sum(fact.get('status') == 'conflicting' for fact in dynamic_facts),
        'unavailable_dynamic_fact_count': sum(fact.get('status') == 'unavailable' for fact in dynamic_facts),
        'medium_facts_needing_recheck': facts_needing_recheck,
        'verified_experience_count':verified_experiences,
        'suggested_experience_count':suggested_experiences,
        'generated_experience_in_schedule_count':generated_scheduled,
        'scheduled_place_fact_coverage':round(provider_fact_count/total_scheduled_facts,3) if total_scheduled_facts else 1.0,
        'official_fact_coverage':round(official_fact_count/total_scheduled_facts,3) if total_scheduled_facts else 1.0,
        'provider_fact_coverage':round(provider_fact_count/total_scheduled_facts,3) if total_scheduled_facts else 1.0,
        'needs_recheck_count':facts_needing_recheck,
        'scheduled_fact_details':scheduled_fact_details,
        'hard_fact_errors': invalid_references,
        'warnings': list({(item['pointer'], item['code']): item for item in warnings}.values()),
        'verified_at': started,
    }
    if connect:
        with connect() as db:
            db.execute("""INSERT INTO fact_verification_runs
              (verification_run_id,job_id,run_id,verifier_version,status,summary_json,started_at,finished_at)
              VALUES(?,?,?,?,?,?,?,?)""",
              (str(uuid.uuid4()), job_id, run_id, FACT_VERIFIER_VERSION,
               'invalid' if invalid_references else ('completed_with_warnings' if summary['warnings'] else 'completed'),
               json.dumps(summary, ensure_ascii=False), started, _now()))
            for place in places:
                db.execute("""UPDATE canonical_places SET verification_status=?,fact_provenance=?,updated_at=?
                  WHERE place_id=?""", (place['verification_status'], json.dumps(place['fact_provenance'], ensure_ascii=False),
                                         started, place['id']))
    return summary
