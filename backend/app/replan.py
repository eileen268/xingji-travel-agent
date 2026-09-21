"""Deterministic, scoped itinerary replanning over persisted canonical packs."""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import collect_places
from .day_planner import canonicalize_day_decision
from .interests import derive_day_interests
from .models import BuildInput, StructuredTripConstraints
from .pack_graph import dependencies, pack_ids
from .pack_store import PackStore
from .pipeline import assemble
from .place_allocator import (
    allocation_outline, build_canonical_place_pool, finalize_selected,
    reallocate_days, rebuild_available_pool, release_place,
    validate_cross_day_ownership,
)
from .experience_policy import is_schedulable_experience
from .scheduler import schedule_day_with_routes
from .place_resolution import PlaceResolver
from .pre_schedule_resolution import (apply_resolution_mapping,build_day_resolution_contexts,
                                      enforce_pre_schedule_resolution)
from .services.amap import AmapClient, AmapConfigurationError, AmapRouteService
from .services.official import OfficialFactService
from .validators import check_handoff, validate_day, validate_trip_coherence
from .versions import REPLAN_VERSION
from .trip_constraints import compile_trip_constraints


def _now():
    return datetime.now(timezone.utc).isoformat()


class ReplanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['remove_stop', 'replace_stop', 'regenerate_day', 'change_pace', 'change_transport']
    day: int | None = Field(default=None, ge=1, le=30)
    place_id: str | None = None
    replacement_place_id: str | None = None
    pace: Literal['relaxed', 'balanced', 'intensive'] | None = None
    local_transport: list[Literal['transit', 'taxi', 'self_drive', 'cycling']] | None = None

    @model_validator(mode='after')
    def required_action_fields(self):
        if self.action in ('remove_stop', 'replace_stop', 'regenerate_day') and self.day is None:
            raise ValueError('该操作必须提供 day')
        if self.action in ('remove_stop', 'replace_stop') and not self.place_id:
            raise ValueError('该操作必须提供 place_id')
        if self.action == 'replace_stop' and not self.replacement_place_id:
            raise ValueError('替换地点必须提供 replacement_place_id')
        if self.action == 'change_pace' and self.pace is None:
            raise ValueError('调整节奏必须提供 pace')
        if self.action == 'change_transport' and not self.local_transport:
            raise ValueError('调整交通方式必须提供 local_transport')
        if self.action in ('change_pace', 'change_transport') and self.day is not None:
            raise ValueError('节奏和交通方式是全程设置，不接受单日 day')
        return self


class ReplanError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422, details: list[dict] | None = None):
        self.code = code; self.status_code = status_code; self.details = details or []
        super().__init__(message)


def replan_options(connect: Callable, job_id: str, day: int) -> dict:
    """Return the persisted, day-scoped choices that the UI may safely expose."""
    with connect() as db:
        job = db.execute('SELECT status,result FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise ReplanError('NOT_FOUND', '行程不存在', 404)
        if job['status'] not in ('done', 'done_with_warnings') or not job['result']:
            raise ReplanError('TRIP_NOT_STABLE', '只能调整已经生成完成的行程', 409)
        allocation, _ = _payload(db, job_id, 'itinerary-allocation')
    profile = json.loads(job['result'])
    if day < 1 or day > len(profile['itinerary']):
        raise ReplanError('INVALID_DAY', 'day 超出当前行程范围')
    allocated = next((item for item in allocation['days'] if item['day'] == day), None)
    if not allocated:
        raise ReplanError('ALLOCATION_DAY_MISSING', '当天候选分配不存在', 409)
    places = {place['id']: place for place in profile['places']}
    scheduled_ids = [stop['place_id'] for stop in profile['itinerary'][day - 1]['stops']]
    preferred = allocated.get('preferred_candidate_ids', [])
    backup = allocated.get('backup_candidate_ids', [])
    fixed = {allocated['primary_anchor_id'], allocated.get('fixed_meal_stop_id')}
    assignments = allocation.get('place_assignments', {})

    stops = []
    for place_id in scheduled_ids:
        place = places.get(place_id, {})
        owner = assignments.get(place_id, {})
        immutable = place_id in fixed or bool(owner.get('fixed'))
        stops.append({
            'place_id': place_id,
            'display_name': place.get('display_name') or place.get('local_name') or place.get('canonical_name') or place_id,
            'role': ('primary' if place_id == allocated['primary_anchor_id'] else
                     'fixed_meal' if place_id == allocated.get('fixed_meal_stop_id') else owner.get('role', 'selected')),
            'removable': not immutable,
            'replaceable': not immutable and place_id in {*preferred, *backup},
        })
    candidates = []
    for place_id in dict.fromkeys([*preferred, *backup]):
        if place_id in scheduled_ids:
            continue
        place = places.get(place_id)
        if not place or place.get('city') != allocated['city'] or not is_schedulable_experience(place):
            continue
        candidates.append({
            'place_id': place_id,
            'display_name': place.get('display_name') or place.get('local_name') or place.get('canonical_name') or place_id,
            'place_type': place.get('place_type') or place.get('type'),
            'description': place.get('description', ''),
            'pool': 'preferred' if place_id in preferred else 'backup',
        })
    return {
        'job_id': job_id,
        'day': day,
        'city': allocated['city'],
        'pace': profile['trip']['preferences']['pace'],
        'local_transport': profile['trip']['preferences']['localTransport'],
        'stops': stops,
        'replacement_candidates': candidates,
    }


def _payload(db, job_id, pack_id):
    row = db.execute('SELECT status,payload,source_payload FROM job_packs WHERE job_id=? AND pack_id=?',
                     (job_id, pack_id)).fetchone()
    if not row or row['status'] != 'valid' or not row['payload']:
        raise ReplanError('PACK_NOT_READY', f'{pack_id} 尚未形成有效数据包', 409)
    return json.loads(row['payload']), json.loads(row['source_payload']) if row['source_payload'] else None


def _decision_from_day(day, outline, source=None):
    if isinstance(source, dict) and 'optional_stop_order' in source:
        return canonicalize_day_decision(copy.deepcopy(source))
    fixed = {outline['primary_anchor_id'], outline.get('fixed_meal_stop_id')}
    return {
        'optional_stop_order': [stop['place_id'] for stop in day['stops'] if stop['place_id'] not in fixed],
        'day_intent': day.get('theme') or outline.get('intent') or '当天行程',
        'practical_notes': [day.get('summary') or '按现场开放、天气与体力灵活调整。'],
    }


def _route_service(connect):
    try:
        return AmapRouteService(AmapClient(connect=connect), connect)
    except AmapConfigurationError:
        return None


def _claim(connect, job_id, request, idempotency_key):
    replan_id = str(uuid.uuid4()); created = _now()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if idempotency_key:
            previous = db.execute('SELECT * FROM trip_replan_runs WHERE job_id=? AND idempotency_key=?',
                                  (job_id, idempotency_key)).fetchone()
            if previous:
                if previous['request_json'] != request.model_dump_json():
                    raise ReplanError('IDEMPOTENCY_CONFLICT', '同一 Idempotency-Key 不能执行不同的局部重规划', 409)
                if previous['status'] == 'completed':
                    return None, dict(previous)
                raise ReplanError('REPLAN_IN_PROGRESS', '相同的局部重规划正在执行', 409)
        job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise ReplanError('NOT_FOUND', '行程不存在', 404)
        if job['status'] not in ('done', 'done_with_warnings') or not job['result']:
            raise ReplanError('TRIP_NOT_STABLE', '只能调整已经生成完成的行程', 409)
        lease = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
        changed = db.execute("""UPDATE jobs SET status='validating',stage='local-replan',run_id=?,lock_owner='local-replan',
            lease_expires_at=?,current_operation='local_replan',updated_at=?
            WHERE id=? AND status IN ('done','done_with_warnings')""",
            (replan_id, lease, created, job_id)).rowcount
        if changed != 1:
            raise ReplanError('REPLAN_CONFLICT', '该行程正在被其他操作修改', 409)
        affected = [f'itinerary-day-{request.day}'] if request.day else []
        affected += ['itinerary-coherence', 'destination-profile']
        db.execute("""INSERT INTO trip_replan_runs
          (replan_id,job_id,idempotency_key,action,day_number,request_json,status,affected_packs_json,before_result,created_at)
          VALUES(?,?,?,?,?,?,'running',?,?,?)""",
          (replan_id, job_id, idempotency_key, request.action, request.day, request.model_dump_json(),
           json.dumps(affected, ensure_ascii=False), job['result'], created))
        return replan_id, dict(job)


def _release_failed(connect, job_id, replan_id, prior_status, exc):
    with connect() as db:
        db.execute("""UPDATE trip_replan_runs SET status='failed',error_code=?,error_message=?,completed_at=?
          WHERE replan_id=?""", (getattr(exc, 'code', type(exc).__name__), str(exc), _now(), replan_id))
        db.execute("""UPDATE jobs SET status=?,stage='done',run_id=NULL,lock_owner=NULL,lease_expires_at=NULL,
          current_operation='completed',updated_at=? WHERE id=? AND run_id=?""",
          (prior_status, _now(), job_id, replan_id))


async def replan_trip(connect: Callable, job_id: str, request: ReplanRequest,
                      idempotency_key: str | None = None, route_service=None) -> dict:
    replan_id, claimed = _claim(connect, job_id, request, idempotency_key)
    if replan_id is None:
        affected = json.loads(claimed['affected_packs_json'])
        before_profile=json.loads(claimed['before_result'])
        affected_days = ([claimed['day_number']] if claimed['day_number'] else
                         list(range(1,len(before_profile['itinerary'])+1)))
        return {'job_id': job_id, 'replan_id': claimed['replan_id'], 'status': 'completed',
                'affected_days': affected_days, 'affected_packs': affected,
                'replan_version': REPLAN_VERSION, 'idempotent_replay': True}
    prior_status = claimed['status']
    try:
        trip = BuildInput.model_validate_json(claimed['preferences'])
        manifest = json.loads(claimed['manifest']) if claimed.get('manifest') else None
        with connect() as db:
            framing, _ = _payload(db, job_id, 'framing')
            plan, _ = _payload(db, job_id, 'itinerary-plan')
            allocation, _ = _payload(db, job_id, 'itinerary-allocation')
            place_packs = {name: _payload(db, job_id, name)[0]
                           for name in ('places-core', 'places-experiences', 'places-food')}
            modules = {name: _payload(db, job_id, name)[0]
                       for name in ('modules-practical', 'modules-language-notes')}
            itinerary = [] ; sources = {}
            for index in range(1, trip.days + 1):
                day, source = _payload(db, job_id, f'itinerary-day-{index}')
                itinerary.append(day); sources[index] = source
            user_places=[json.loads(row['place_json']) for row in db.execute(
                'SELECT place_json FROM trip_user_places WHERE job_id=? ORDER BY created_at',(job_id,))]
            edit_states={row['day_number']:{'stop_overrides':json.loads(row['stop_overrides_json']),
                'day_transport':row['day_transport'],'segment_overrides':json.loads(row['segment_overrides_json'])}
                for row in db.execute('SELECT * FROM trip_day_edit_state WHERE job_id=?',(job_id,))}
        trip_constraints = (StructuredTripConstraints.model_validate(framing['constraints'])
                            if framing.get('constraints') else compile_trip_constraints(trip))
        user_pack={'places':user_places}
        places = collect_places({**place_packs,'user-places':user_pack}); by_id = {place['id']: place for place in places}
        target_days = {request.day} if request.day else set(range(1, trip.days + 1))
        if request.day and request.day > trip.days:
            raise ReplanError('INVALID_DAY', 'day 超出当前行程范围')

        referenced=set();contexts={}
        for day_no in target_days:
            outline=allocation_outline(allocation,plan['days'][day_no-1])
            referenced.update([outline['primary_anchor_id'],outline.get('fixed_meal_stop_id')])
            referenced.update(outline.get('candidate_place_ids',[]));referenced.update(outline.get('backup_candidate_ids',[]))
            referenced.update(stop['place_id'] for stop in itinerary[day_no-1]['stops'])
            ordered=[*[stop['place_id'] for stop in itinerary[day_no-1]['stops']],outline['primary_anchor_id'],
                     *outline.get('candidate_place_ids',[]),
                     *([outline['fixed_meal_stop_id']] if outline.get('fixed_meal_stop_id') else []),
                     *outline.get('backup_candidate_ids',[])]
            contexts.update(build_day_resolution_contexts(places,ordered,outline.get('area_labels',[])))
        referenced.discard(None)
        gate=await enforce_pre_schedule_resolution(places,PlaceResolver(connect,job_id,'pre-schedule-resolution'),referenced,contexts)
        if gate.id_mapping:
            resolved_by_old={old:next(place for place in places if place['id']==new) for old,new in gate.id_mapping.items()}
            for pack in [*place_packs.values(),user_pack]:
                pack['places']=[copy.deepcopy(resolved_by_old.get(place['id'],place)) for place in pack.get('places',[])]
            plan,allocation,itinerary,sources,edit_states,modules=apply_resolution_mapping(
                gate.id_mapping,plan,allocation,itinerary,sources,edit_states,modules)
            places=collect_places({**place_packs,'user-places':user_pack});by_id={place['id']:place for place in places}

        if request.action == 'change_pace':
            trip = trip.model_copy(update={'preferences': trip.preferences.model_copy(update={'pace': request.pace})})
        elif request.action == 'change_transport':
            trip = trip.model_copy(update={'preferences': trip.preferences.model_copy(update={'localTransport': request.local_transport})})

        day_decisions = {}
        if request.action == 'regenerate_day':
            allocation = reallocate_days(allocation, plan, places, target_days,
                must_go=trip.preferences.mustGo, interests=trip.preferences.interests)

        for day_no in target_days:
            outline = allocation_outline(allocation, plan['days'][day_no - 1])
            decision = _decision_from_day(itinerary[day_no - 1], outline, sources[day_no])
            if request.action in ('remove_stop', 'replace_stop'):
                fixed = {outline['primary_anchor_id'], outline.get('fixed_meal_stop_id')}
                owner=allocation.get('place_assignments',{}).get(request.place_id)
                if request.place_id in fixed or (owner and owner.get('fixed')):
                    raise ReplanError('IMMUTABLE_STOP', '主锚点和固定用餐不能通过可选地点操作删除或替换')
                current = [stop['place_id'] for stop in itinerary[day_no - 1]['stops']]
                if request.place_id not in current:
                    raise ReplanError('STOP_NOT_IN_DAY', '指定地点不在当天实际日程中')
                optional = decision['optional_stop_order']
                if request.action == 'replace_stop':
                    allowed = [*outline.get('candidate_place_ids', []), *outline.get('backup_candidate_ids', [])]
                    replacement = request.replacement_place_id
                    if replacement not in allowed or replacement not in by_id or by_id[replacement]['city'] != outline['city']:
                        raise ReplanError('REPLACEMENT_OUTSIDE_DAY_POOL', '替换地点必须来自当天已分配的优先或备用候选池')
                    if not is_schedulable_experience(by_id[replacement]):
                        raise ReplanError('SUGGESTED_EXPERIENCE_NOT_SCHEDULABLE','该体验尚无可靠真实提供方，只能保留为体验灵感')
                    if replacement in current:
                        raise ReplanError('DUPLICATE_REPLACEMENT', '替换地点已经在当天日程中')
                    optional = ([replacement if value == request.place_id else value for value in optional]
                                if request.place_id in optional else [*optional,replacement])
                else:
                    optional = [value for value in optional if value != request.place_id]
                allocated_day = next(day for day in allocation['days'] if day['day'] == day_no)
                for key in ('preferred_candidate_ids', 'backup_candidate_ids'):
                    allocated_day[key] = [value for value in allocated_day[key] if value != request.place_id]
                release_place(allocation, request.place_id, day=day_no)
                canonical = build_canonical_place_pool(places)
                allocation['available_place_ids'] = sorted(rebuild_available_pool(canonical, allocation['place_assignments']))
                outline = allocation_outline(allocation, plan['days'][day_no - 1])
                decision['optional_stop_order'] = list(dict.fromkeys(optional))
            elif request.action == 'regenerate_day':
                # Prefer a fresh legal composition while keeping the immutable anchor and meal.
                current = set(decision['optional_stop_order'])
                fresh = [*outline.get('backup_candidate_ids', []), *outline.get('candidate_place_ids', [])]
                decision['optional_stop_order'] = [value for value in fresh if value not in current] + [value for value in fresh if value in current]
                decision['optional_stop_order'] = decision['optional_stop_order'][:4]
                decision['day_intent'] = outline.get('intent') or decision['day_intent']
            day_decisions[day_no] = decision

        service = route_service if route_service is not None else _route_service(connect)
        for day_no in sorted(target_days):
            outline = allocation_outline(allocation, plan['days'][day_no - 1])
            day = await schedule_day_with_routes(day_decisions[day_no], outline, places, trip, day_no - 1,
                                                 service, connect, job_id,constraints=edit_states.get(day_no),
                                                 trip_constraints=trip_constraints)
            strengths = derive_day_interests(day, places, trip.preferences.interests)
            day['derived_interests'] = list(strengths); day['day_interest_strength'] = strengths
            errors = validate_day(day, places, expected_date=outline['date'], expected_city=outline['city'],
                                  required_ids=(outline['primary_anchor_id'], outline.get('fixed_meal_stop_id')))
            if errors:
                raise ReplanError('REPLANNED_DAY_INVALID', json.dumps(errors[:3], ensure_ascii=False))
            itinerary[day_no - 1] = day

        for owner in allocation['place_assignments'].values():
            owner['status'] = 'reserved'
            owner['reservation'] = 'soft' if owner['role'] == 'backup' else 'hard'
        allocation = finalize_selected(allocation, itinerary, places)
        ownership_errors = validate_cross_day_ownership(allocation, places, itinerary)
        coherence_errors = validate_trip_coherence(itinerary, plan, places, trip.preferences.model_dump(mode='json'))
        if ownership_errors or coherence_errors:
            raise ReplanError('REPLAN_COHERENCE_FAILED', json.dumps((ownership_errors + coherence_errors)[:5], ensure_ascii=False))

        packs = {**place_packs, **modules, 'user-places':user_pack, 'itinerary': {'itinerary': itinerary},
                 'enrichment': {'practical': 'complete', 'language_notes': 'complete', 'pending_packs': []},
                 'enrichment_warnings': []}
        scheduled_ids={stop['place_id'] for day in itinerary for stop in day.get('stops',[])}
        official_warnings=await OfficialFactService(connect,job_id).resolve_places(
            [place for place in places if place['id'] in scheduled_ids])
        old_profile = json.loads(claimed['result'])
        warnings = list(old_profile.get('generation', {}).get('warnings', []))
        warnings.extend(official_warnings)
        warnings.append({'code':'LOCAL_REPLAN_APPLIED',
                         'message':f'已执行局部重规划（{request.action}）；研究资料包和未受影响日期均已复用。'})
        profile = assemble(trip, job_id, packs, old_profile.get('generation', {}).get('mode', 'llm'),
                           warnings, manifest)
        handoff = check_handoff(profile, packs)
        if handoff:
            raise ReplanError('REPLAN_HANDOFF_FAILED', json.dumps(handoff[:5], ensure_ascii=False))

        store = PackStore(connect, job_id, trip.model_dump(mode='json'), trip.days, manifest, replan_id)
        if gate.id_mapping:
            for pack_id,payload in place_packs.items():
                store.save_valid(pack_id,payload,store.expected_signature(pack_id,dependencies(pack_id,trip.days)),0,0,
                                 observability={'repair_result':'pre_schedule_resolution'},source_payload=payload)
            for pack_id,payload in modules.items():
                store.save_valid(pack_id,payload,store.expected_signature(pack_id,dependencies(pack_id,trip.days)),0,0,
                                 observability={'repair_result':'pre_schedule_resolution'},source_payload=payload)
            store.save_valid('itinerary-plan',plan,store.expected_signature('itinerary-plan',dependencies('itinerary-plan',trip.days)),0,0,
                             observability={'repair_result':'pre_schedule_resolution'},source_payload=plan)
        if request.action in ('change_pace', 'change_transport'):
            with connect() as db:
                db.execute('UPDATE jobs SET preferences=? WHERE id=? AND run_id=?', (trip.model_dump_json(), job_id, replan_id))
            store.save_valid('framing', {'preferences': trip.model_dump(mode='json'),
                                         'constraints': trip_constraints.model_dump(mode='json')},
                             store.expected_signature('framing', dependencies('framing', trip.days)), 0, 0,
                             observability={'repair_result': 'replan_setting_changed'})
            # These packs are intentionally reused: pace and local transport are scheduler concerns.
            for pack_id in ('places-core', 'places-experiences', 'places-food', 'itinerary-plan'):
                store.resign_valid(pack_id,store.expected_signature(pack_id,dependencies(pack_id,trip.days)))
        store.save_valid('itinerary-allocation', allocation,
                         store.expected_signature('itinerary-allocation', dependencies('itinerary-allocation', trip.days)), 0, 0,
                         observability={'repair_result': 'local_replan'}, source_payload=allocation)
        for day_no in sorted(target_days):
            pack_id = f'itinerary-day-{day_no}'
            store.save_valid(pack_id, itinerary[day_no - 1],
                             store.expected_signature(pack_id, dependencies(pack_id, trip.days)), 0, 0,
                             observability={'repair_result': 'local_replan'}, source_payload=day_decisions[day_no])
        if request.action in ('change_pace', 'change_transport'):
            for day_no in sorted(set(range(1, trip.days + 1)) - target_days):
                pack_id = f'itinerary-day-{day_no}'
                store.save_valid(pack_id, itinerary[day_no - 1],
                                 store.expected_signature(pack_id, dependencies(pack_id, trip.days)), 0, 0,
                                 observability={'repair_result': 'replan_reused'}, source_payload=sources[day_no])
        store.save_valid('itinerary-coherence', {'passed': True, 'errors': []},
                         store.expected_signature('itinerary-coherence', dependencies('itinerary-coherence', trip.days)), 0, 0,
                         observability={'repair_result': 'local_replan'})
        # Module content is reused and re-signed after a global scheduler-only setting change.
        if request.action in ('change_pace', 'change_transport'):
            for pack_id in ('modules-practical', 'modules-language-notes'):
                store.resign_valid(pack_id,store.expected_signature(pack_id,dependencies(pack_id,trip.days)))
        store.save_valid('destination-profile', profile,
                         store.expected_signature('destination-profile', dependencies('destination-profile', trip.days)), 0, 0,
                         validation_warnings=profile['fact_verification']['warnings'],
                         observability={'repair_result': 'local_replan'}, source_payload=profile)

        final_status = 'done_with_warnings' if profile['generation']['warnings'] else 'done'
        affected = [f'itinerary-day-{day_no}' for day_no in sorted(target_days)] + ['itinerary-coherence', 'destination-profile']
        completed = _now()
        with connect() as db:
            changed = db.execute("""UPDATE jobs SET status=?,stage='done',progress=100,result=?,packs=?,report=?,error=NULL,
              run_id=NULL,lock_owner=NULL,lease_expires_at=NULL,current_operation='completed',updated_at=?
              WHERE id=? AND run_id=?""", (final_status, json.dumps(profile, ensure_ascii=False),
              json.dumps(packs, ensure_ascii=False), json.dumps({'passed': True, 'errors': [], 'replan_id': replan_id,
              'replan_version': REPLAN_VERSION}, ensure_ascii=False), completed, job_id, replan_id)).rowcount
            if changed != 1:
                raise ReplanError('REPLAN_LEASE_LOST', '局部重规划提交时失去任务写锁', 409)
            db.execute("""UPDATE trip_replan_runs SET status='completed',affected_packs_json=?,after_result=?,completed_at=?
              WHERE replan_id=?""", (json.dumps(affected, ensure_ascii=False), json.dumps(profile, ensure_ascii=False),
                                      completed, replan_id))
        return {'job_id': job_id, 'replan_id': replan_id, 'status': final_status,
                'affected_days': sorted(target_days), 'affected_packs': affected,
                'replan_version': REPLAN_VERSION, 'idempotent_replay': False}
    except Exception as exc:
        _release_failed(connect, job_id, replan_id, prior_status, exc)
        raise
