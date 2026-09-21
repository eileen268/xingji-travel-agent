"""Canonical warning records used at profile assembly boundaries.

Packs predate structured warnings, so producers can still provide legacy text.
This module is the single explicit adapter from those legacy values to the
profile contract; consumers only receive ``WarningRecord``-shaped mappings.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


def normalize_warning(value: Any, *, source: str | None = None) -> dict | None:
    """Return one canonical warning without discarding producer fields."""
    if value is None:
        return None
    if isinstance(value, str):
        message = value.strip()
        if not message:
            return None
        result: dict[str, Any] = {
            'code': 'GENERAL_WARNING',
            'message': message,
            'severity': 'warning',
            'day': None,
            'place_id': None,
            'metadata': {},
        }
    elif isinstance(value, dict):
        # Copy first: canonicalization must never mutate a persisted pack or a
        # warning object retained by an upstream producer.
        result = dict(value)
        message = str(result.get('message') or '').strip()
        if not message:
            return None
        result['message'] = message
        result.setdefault('code', 'GENERAL_WARNING')
        result.setdefault('severity', 'warning')
        result.setdefault('day', None)
        result.setdefault('place_id', None)
        result.setdefault('metadata', {})
        if not isinstance(result['metadata'], dict):
            result['metadata'] = {'legacy_metadata': result['metadata']}
    else:
        # This keeps a bad legacy producer observable instead of crashing
        # final compilation. The original value remains inspectable.
        result = {
            'code': 'GENERAL_WARNING',
            'message': str(value),
            'severity': 'warning',
            'day': None,
            'place_id': None,
            'metadata': {'legacy_type': type(value).__name__},
        }
    # The public WarningRecord stays deliberately small. Preserve all producer
    # evidence, such as interest_id/strength, inside metadata rather than
    # allowing each producer to grow a parallel root-level contract.
    canonical = {'code', 'message', 'severity', 'day', 'place_id', 'metadata', 'pointer', 'invalid_value'}
    extra = {key: result.pop(key) for key in list(result) if key not in canonical}
    if extra:
        result['metadata'] = {**result['metadata'], **extra}
    return result


def _values(values: Any) -> Iterable[Any]:
    if values is None:
        return
    if isinstance(values, (str, dict)):
        yield values
        return
    if isinstance(values, Iterable):
        for value in values:
            # Lists may arrive from old aggregate fields. Flattening here is
            # explicit and preserves item order.
            if isinstance(value, list):
                yield from _values(value)
            else:
                yield value
        return
    yield values


def dedupe_warnings(*sources: tuple[str, Any]) -> list[dict]:
    """Normalize and stably de-duplicate warning values.

    JSON uses sorted keys for equality only. The first normalized mapping is
    retained exactly, preserving source-specific fields and display order.
    """
    result: list[dict] = []
    seen: set[str] = set()
    for source, values in sources:
        for value in _values(values):
            warning = normalize_warning(value, source=source)
            if warning is None:
                continue
            key = json.dumps(warning, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
            if key in seen:
                continue
            seen.add(key)
            result.append(warning)
    return result


def user_warnings(diagnostics: list[dict], places: list[dict], itinerary: list[dict],
                  fact_summary: dict) -> list[dict]:
    """Collapse diagnostic chains into a small set of actionable messages."""
    result=[]
    unresolved=[item for item in fact_summary.get('warnings',[])
                if item.get('code')=='UNRESOLVED_REAL_POI']
    if unresolved:
        names=[]
        by_id={place.get('id'):place for place in places}
        for item in unresolved:
            place=by_id.get(item.get('place_id')) or {}
            names.append(place.get('display_name') or item.get('place_id') or '未知地点')
        names=list(dict.fromkeys(names))
        result.append({'code':'NEEDS_CONFIRMATION','message':f'{len(names)} 个行程地点的位置尚待确认：{"、".join(names[:5])}。',
            'metadata':{'category':'needs_confirmation','items':names}})

    details=fact_summary.get('scheduled_fact_details',[])
    needs=[item for item in details if item.get('needs_recheck')]
    if needs:
        labels={'opening_hours':'开放时间','ticket_info':'票价','reservation_required':'预约'}
        items=[f"{item['place_name']}：{'、'.join(labels.get(v,v) for v in item['needs_recheck'])}" for item in needs]
        preview='；'.join(items[:5])
        result.append({'code':'DYNAMIC_INFORMATION','message':f'{len(needs)} 个最终行程地点有动态信息需要出发前确认：{preview}。',
            'metadata':{'category':'dynamic_information','items':items}})

    quality_days=[]
    for index,day in enumerate(itinerary,1):
        issues=set((day.get('density_evaluation') or {}).get('issues',[]))
        if issues.intersection({'UNDERFILLED_DAY','UNDERFILLED_DAY_NO_GOOD_CANDIDATE','LONG_IDLE_GAP'}):
            quality_days.append(index)
    if quality_days:
        result.append({'code':'SCHEDULE_QUALITY','message':f'第 {"、".join(map(str,quality_days))} 天安排较松，已保留为休息或自由活动时间。',
            'metadata':{'category':'schedule_quality','days':quality_days}})

    meal_items=[]
    for day in itinerary:
        for warning in day.get('schedule_warnings',[]):
            if warning.get('code') in {'MEAL_WINDOW_VIOLATION','MEAL_MISSING','MEALS_TOO_CLOSE','MEAL_OPENING_HOURS_CONFLICT'}:
                meal_items.append(warning.get('message',''))
    if meal_items:
        result.append({'code':'MEAL_SCHEDULE','message':'；'.join(dict.fromkeys(filter(None,meal_items))),
            'metadata':{'category':'schedule_quality','items':list(dict.fromkeys(filter(None,meal_items)))}})

    pending=[item for item in diagnostics if item.get('code') in {
        'ENRICHMENT_TIMEOUT','PRACTICAL_ENRICHMENT_PENDING','LANGUAGE_ENRICHMENT_PENDING'} or
        ('超时' in item.get('message','') and any(token in item.get('message','') for token in ('补充内容','核心行程','语言','贴士')))]
    if pending:
        result.append({'code':'ENRICHMENT_PENDING','message':'部分补充内容尚未完成，可继续生成；核心行程已经可用。',
            'metadata':{'category':'needs_confirmation','items':[item['message'] for item in pending]}})
    content_quality=[item for item in diagnostics if item.get('code') in {
        'FOOD_SCENE_DIVERSITY_LOW','FOOD_CITY_COVERAGE_LOW','INTEREST_COVERAGE_WEAK'}]
    if content_quality:
        result.append({'code':'CONTENT_QUALITY','message':'；'.join(dict.fromkeys(item['message'] for item in content_quality)),
            'metadata':{'category':'schedule_quality','items':[item['message'] for item in content_quality]}})
    return dedupe_warnings(('user',result))
