"""User-controlled day edits with preview, revisions and deterministic scheduling."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

import httpx
from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import collect_places
from .day_planner import canonicalize_day_decision
from .interests import derive_day_interests
from .fact_verification import normalize_dynamic_hours
from .models import BuildInput, CustomActivity, Place, StructuredTripConstraints
from .pack_graph import dependencies
from .pack_store import PackStore
from .pipeline import assemble
from .place_allocator import allocation_outline, assign_place, build_canonical_place_pool, finalize_selected, rebuild_available_pool, release_place, validate_cross_day_ownership
from .place_resolution import PlaceResolver,score_candidate
from .pre_schedule_resolution import (apply_resolution_mapping,build_day_resolution_contexts,
                                      enforce_pre_schedule_resolution)
from .replan import ReplanError, _decision_from_day, _route_service
from .replan_intent import (AddFreeTimeAction, AddPlaceAction, AvoidAction, DedicateDayAction,
                            KeepPlaceAction, MovePlaceAction, MustIncludeAction, RemovePlaceAction,
                            RedistributeStopsAction, ReplacePlaceAction, ReflowAction, ReplanDayAction, SetActivityPreferenceAction, SetCityDayAction,
                            SetMealPreferenceAction, SetPaceAction, SetTimeWindowAction,
                            SetTransportAction, StructuredReplanRequest, normalize_replan_request,
                            parse_replan_deterministic, resolve_scope)
from .scheduler import (_density, _end_minute, _mode, _open_for_visit, _start_minute,
                        schedule_day_with_routes)
from .scheduler_config import ADD_PLACE_ESTIMATED_ROUTE_SAFETY_MARGIN_MINUTES, PACE_CONFIG
from .services.amap import AmapClient, AmapConfigurationError, AmapProviderError
from .services.amap.poi_detail import get_poi_detail
from .services.amap.poi_search import AmapPoiCandidate, search_pois
from .services.amap.route import estimate_route
from .validators import check_handoff, map_url, validate_day, validate_trip_coherence
from .versions import REPLAN_VERSION, SCHEDULER_VERSION
from .trip_constraints import compile_trip_constraints


def _now(): return datetime.now(timezone.utc).isoformat()


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SearchAddRequest(StrictRequest):
    query: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=40)
    preferred_time: str | None = None
    stay_minutes: int | None = Field(default=None, ge=15, le=480)
    provider_place_id: str | None = None


class CustomActivityRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=100)
    note: str = Field(default='', max_length=1000)
    start_time_preference: str | None = None
    stay_minutes: int = Field(default=60, ge=15, le=480)
    location_optional: str | None = Field(default=None, max_length=300)
    linked_place_id: str | None = None


class StopEditRequest(StrictRequest):
    stay_minutes: int | None = Field(default=None, ge=15, le=480)
    preferred_start_time: str | None = None
    hard_start_time: str | None = None
    locked: bool | None = None
    must_keep: bool | None = None
    note: str | None = Field(default=None, max_length=1000)
    priority: int | None = Field(default=None, ge=1, le=5)
    transport_to_next: Literal['auto','walking','public_transit','driving','taxi'] | None = None


class DayTransportRequest(StrictRequest):
    transport_mode: Literal['auto','walking','public_transit','driving','taxi']


class SegmentTransportRequest(StrictRequest):
    origin_place_id: str
    destination_place_id: str
    transport_mode: Literal['auto','walking','public_transit','driving','taxi']


class InstructionRequest(StrictRequest):
    instruction: str = Field(min_length=2, max_length=2000)
    selected_place_id: str | None = None
    ui_scope_context: Literal['current_day','trip'] = 'current_day'
    anchor_day_id: str | None = None


@dataclass
class _ExecutionPlan:
    """Internal projection of canonical actions; never exposed as a parser contract."""
    scope: str='current_day'
    affected_day_ids: list[int]=field(default_factory=list)
    dedicated_place_id: str|None=None
    dedicated_place_query: str|None=None
    keep_place_ids: list[str]=field(default_factory=list)
    keep_locks: dict[str,bool]=field(default_factory=dict)
    remove_place_ids: list[str]=field(default_factory=list)
    add_actions: list=field(default_factory=list)
    avoid_actions: list=field(default_factory=list)
    replace_actions: list=field(default_factory=list)
    move_actions: list=field(default_factory=list)
    transport_actions: list=field(default_factory=list)
    time_actions: list=field(default_factory=list)
    meal_actions: list=field(default_factory=list)
    activity_actions: list=field(default_factory=list)
    city_actions: list=field(default_factory=list)
    reflow_actions: list=field(default_factory=list)
    replan_day: bool=False
    free_time_actions: list=field(default_factory=list)
    redistribute_removed_stops: bool=False
    pace: str|None=None
    max_walking_load: str|None=None


def _day_number(day_id: str | int, job_id: str) -> int:
    if isinstance(day_id, int): return day_id
    if str(day_id).isdigit(): return int(day_id)
    match = re.search(r'-day-(\d+)$', str(day_id))
    if not match or not str(day_id).startswith(job_id):
        raise ReplanError('INVALID_DAY', 'day_id 与当前行程不匹配')
    return int(match.group(1))


def _payload(db, job_id, pack_id):
    row=db.execute('SELECT payload,source_payload,status FROM job_packs WHERE job_id=? AND pack_id=?',(job_id,pack_id)).fetchone()
    if not row or row['status']!='valid' or not row['payload']:
        raise ReplanError('PACK_NOT_READY',f'{pack_id} 尚未形成有效数据包',409)
    return json.loads(row['payload']),json.loads(row['source_payload']) if row['source_payload'] else None


def _load(connect,job_id,day_no):
    with connect() as db:
        job=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not job:raise ReplanError('NOT_FOUND','行程不存在',404)
        if job['status'] not in ('done','done_with_warnings') or not job['result']:
            raise ReplanError('TRIP_NOT_STABLE','只能编辑已经生成完成的行程',409)
        trip=BuildInput.model_validate_json(job['preferences'])
        if day_no<1 or day_no>trip.days:raise ReplanError('INVALID_DAY','day_id 超出当前行程范围')
        framing,_=_payload(db,job_id,'framing')
        plan,_=_payload(db,job_id,'itinerary-plan');allocation,_=_payload(db,job_id,'itinerary-allocation')
        place_packs={name:_payload(db,job_id,name)[0] for name in ('places-core','places-experiences','places-food')}
        modules={name:_payload(db,job_id,name)[0] for name in ('modules-practical','modules-language-notes')}
        itinerary=[];sources={}
        for index in range(1,trip.days+1):
            itinerary_day,source=_payload(db,job_id,f'itinerary-day-{index}');itinerary.append(itinerary_day);sources[index]=source
        user_places=[json.loads(row['place_json']) for row in db.execute('SELECT place_json FROM trip_user_places WHERE job_id=? ORDER BY created_at',(job_id,))]
        edit_rows=db.execute('SELECT * FROM trip_day_edit_state WHERE job_id=?',(job_id,)).fetchall()
    edits={index:{'stop_overrides':{},'day_transport':None,'segment_overrides':{}} for index in range(1,trip.days+1)}
    for row in edit_rows:
        edits[row['day_number']]={'stop_overrides':json.loads(row['stop_overrides_json']),'day_transport':row['day_transport'],
              'segment_overrides':json.loads(row['segment_overrides_json'])}
    packs={**place_packs,**modules,'user-places':{'places':user_places}}
    trip_constraints=(StructuredTripConstraints.model_validate(framing['constraints'])
                      if framing.get('constraints') else compile_trip_constraints(trip))
    return {'job':dict(job),'trip':trip,'trip_constraints':trip_constraints,'plan':plan,'allocation':allocation,'place_packs':place_packs,'modules':modules,
            'itinerary':itinerary,'sources':sources,'user_places':user_places,'edits':edits,'edit':edits[day_no],'packs':packs}


def _snapshot(state,day_no):
    return {'day':copy.deepcopy(state['itinerary'][day_no-1]),'allocation':copy.deepcopy(state['allocation']),
            'user_places':copy.deepcopy(state['user_places']),'edit':copy.deepcopy(state['edit'])}


def _scope_snapshot(state,day_numbers):
    return {'days':{str(day):copy.deepcopy(state['itinerary'][day-1]) for day in day_numbers},
            'allocation':copy.deepcopy(state['allocation']),'user_places':copy.deepcopy(state['user_places']),
            'edits':{str(day):copy.deepcopy(state['edits'][day]) for day in day_numbers},
            'plan':copy.deepcopy(state['plan']),'sources':{str(day):copy.deepcopy(state['sources'].get(day)) for day in day_numbers}}


def _scope_diff(before,after,places):
    days=[];combined={'added':[],'removed':[],'moved':[],'transport_changed':[],'before':[],'after':[]}
    for key,old_day in before['days'].items():
        day=int(key);new_day=after['days'][key];item=_diff(old_day,new_day,places);item['day']=day;days.append(item)
        for field in ('added','removed','moved','transport_changed'):
            combined[field].extend([{**entry,'day':day} for entry in item[field]])
    combined['days']=days;combined['affected_day_ids']=sorted(int(key) for key in before['days'])
    return combined


def _diff(before,after,places):
    names={p['id']:p.get('display_name',p['id']) for p in places}
    old={s['place_id']:s for s in before['stops']};new={s['place_id']:s for s in after['stops']}
    added=[{'place_id':pid,'name':names.get(pid,pid),'time':new[pid]['arrival_time']} for pid in new if pid not in old]
    removed=[{'place_id':pid,'name':names.get(pid,pid),'time':old[pid]['arrival_time']} for pid in old if pid not in new]
    moved=[{'place_id':pid,'name':names.get(pid,pid),'before':old[pid]['arrival_time'],'after':new[pid]['arrival_time']}
           for pid in old.keys()&new.keys() if old[pid]['arrival_time']!=new[pid]['arrival_time']]
    transport=[{'place_id':pid,'name':names.get(pid,pid),'before':old[pid].get('transport_mode'),'after':new[pid].get('transport_mode')}
               for pid in old.keys()&new.keys() if old[pid].get('transport_mode')!=new[pid].get('transport_mode')]
    return {'added':added,'removed':removed,'moved':moved,'transport_changed':transport,
            'before':[{ 'time':s['arrival_time'],'name':names.get(s['place_id'],s['place_id'])} for s in before['stops']],
            'after':[{ 'time':s['arrival_time'],'name':names.get(s['place_id'],s['place_id'])} for s in after['stops']]}


def _place_from_candidate(candidate:AmapPoiCandidate,city:str,query:str):
    now=_now();place_url=map_url(candidate.city or city,candidate.name)
    today=candidate.opening_hours_today;weekly=candidate.opening_hours_weekly
    opening_value={'today':today,'weekly':weekly} if today or weekly else None
    opening_source=({'fact_name':'opening_hours','value':opening_value,'source_url':place_url,'source_type':'amap',
        'extracted_at':now,'confidence':.8,'raw_evidence':json.dumps(opening_value,ensure_ascii=False),
        'provider_place_id':candidate.provider_place_id} if opening_value else None)
    opening_fact=({'value':opening_value,'source_url':place_url,'source_type':'amap','extracted_at':now,
        'confidence':.8,'raw_evidence':json.dumps(opening_value,ensure_ascii=False),'status':'provider_verified',
        'provider_place_id':candidate.provider_place_id,'sources':[opening_source]} if opening_source else None)
    source_metadata={'user_query':query,'amap_business':candidate.business,'amap_queried_at':now,
        'amap_opening_hours':candidate.opening_hours,'amap_opening_hours_today':today,
        'amap_opening_hours_weekly':weekly,'amap_phone':candidate.phone,
        'amap_provider_rating':candidate.provider_rating,'amap_provider_cost':candidate.provider_cost,
        'amap_business_area':candidate.business_area,'amap_business_tags':candidate.business_tags}
    place={
      'id':f'amap-{candidate.provider_place_id}','type':'sight','city':city,
      'canonical_name':candidate.name,'local_name':candidate.name,'english_name':None,'display_name':candidate.name,
      'name_source':'map_provider','provider':'amap','provider_place_id':candidate.provider_place_id,
      'district':candidate.district,'address':candidate.address,'latitude':candidate.latitude,'longitude':candidate.longitude,
      'category':candidate.category,'subcategory':candidate.subcategory,'place_type':'sight','source_confidence':1.0,
      'source_metadata':source_metadata,
      'fact_provenance':{key:{'source_type':'provider','provider':'amap','queried_at':now,'confidence':1.0}
                         for key in ('canonical_name','city','district','address','latitude','longitude','category')},
      'official_facts':({'opening_hours':opening_fact} if opening_fact else {}),'verification_status':'verified','entity_kind':'poi','experience_title':None,'linked_place_id':None,
      'booking_status':'not_applicable','description':f'用户通过高德确认添加的地点：{candidate.name}。',
      'official_url':candidate.website,'map_url':place_url,'coordinates':None,
      'rating':None,'review_count':None,'hours_note':candidate.opening_hours_weekly or candidate.opening_hours or '营业时间出发前请复核。',
      'ticket_note':'票务信息出发前请复核。','reservation_note':'预约要求出发前请复核。','recheck_note':'出发前请复核',
      'knowledge_status':'model_knowledge','cuisine':'','signature_dishes':'','experience_type':'',
      'semantic_tags':candidate.business_tags or [],'interest_affinity':{},'semantic_source':['amap'],
      'semantic_confidence':{},'affinity_reasons':{},'semantic_version':'interest-taxonomy-v2','user_created':True,'custom_activity':None}
    if opening_fact:place['fact_provenance']['opening_hours']=opening_fact
    normalize_dynamic_hours(place)
    return Place.model_validate(place).model_dump(mode='json')


def _custom_place(request:CustomActivityRequest,city:str):
    activity=CustomActivity(activity_id=f'custom-{uuid.uuid4().hex}',title=request.title,note=request.note,
        start_time_preference=request.start_time_preference,stay_minutes=request.stay_minutes,
        location_optional=request.location_optional,linked_place_id=request.linked_place_id)
    place={'id':activity.activity_id,'type':'experience','city':city,'canonical_name':request.title,'local_name':request.title,
      'english_name':None,'display_name':request.title,'name_source':'derived','provider':'manual','provider_place_id':None,
      'district':None,'address':request.location_optional,'latitude':None,'longitude':None,'category':'用户自定义活动',
      'subcategory':None,'place_type':'generated_experience','source_confidence':1.0,'source_metadata':{'initiated_by':'user'},
      'fact_provenance':{},'official_facts':{},'verification_status':'generated','entity_kind':'experience_concept',
      'experience_title':request.title,'linked_place_id':request.linked_place_id,'booking_status':'unverified',
      'description':request.note or '用户添加的自定义活动。','official_url':None,'map_url':None,'coordinates':None,'rating':None,
      'review_count':None,'hours_note':'该自定义活动无固定营业时间，出发前请复核。',
      'ticket_note':'该自定义活动费用由用户自行安排，出发前请复核。',
      'reservation_note':'如涉及第三方服务，请在出发前请复核。',
      'recheck_note':'出发前请复核','knowledge_status':'model_knowledge','cuisine':'','signature_dishes':'','experience_type':'custom_activity',
      'semantic_tags':['custom_activity'],'interest_affinity':{},'semantic_source':['user'], 'semantic_confidence':{},
      'affinity_reasons':{},'semantic_version':'interest-taxonomy-v2','user_created':True,'custom_activity':activity.model_dump(mode='json')}
    return Place.model_validate(place).model_dump(mode='json')


def _assign(state,day_no,place):
    normalize_dynamic_hours(place)
    if place['id'] not in {p['id'] for p in state['user_places']}:state['user_places'].append(place)
    allocation=state['allocation'];allocated=next(d for d in allocation['days'] if d['day']==day_no)
    preferred=allocated['preferred_candidate_ids'];backup=allocated['backup_candidate_ids'];role='preferred'
    if place['id'] not in [*preferred,*backup]:
        if len(preferred)<4:preferred.append(place['id'])
        elif len(backup)<4:backup.append(place['id']);role='backup'
        else:
            removable=next((pid for pid in reversed(backup) if not allocation.get('place_assignments',{}).get(pid,{}).get('fixed')),None)
            if removable:
                backup.remove(removable);release_place(allocation,removable,day=day_no);backup.append(place['id']);role='backup'
            else:raise ReplanError('DAY_CANDIDATE_POOL_FULL','当天候选池已满，且没有可安全释放的备用地点')
    allocation.setdefault('canonical_place_ids',[])
    if place['id'] not in allocation['canonical_place_ids']:allocation['canonical_place_ids'].append(place['id'])
    allocation.setdefault('place_assignments',{})[place['id']]={'place_id':place['id'],'day':day_no,'role':role,
        'reservation':'hard','status':'reserved','fixed':False}
    allocation['available_place_ids']=[pid for pid in allocation.get('available_place_ids',[]) if pid!=place['id']]


def _decision(state,day_no):
    outline=allocation_outline(state['allocation'],state['plan']['days'][day_no-1])
    result=_decision_from_day(state['itinerary'][day_no-1],outline,state['sources'].get(day_no))
    actual=[s['place_id'] for s in state['itinerary'][day_no-1]['stops']]
    fixed={outline['primary_anchor_id'],outline.get('fixed_meal_stop_id')}
    pool=[*outline.get('candidate_place_ids',[]),*outline.get('backup_candidate_ids',[])]
    existing=[pid for pid in actual if pid not in fixed and pid in pool]
    forced=[pid for pid,value in state['edit'].get('stop_overrides',{}).items()
            if pid in pool and (value.get('must_keep') or value.get('force_include'))]
    result['optional_stop_order']=list(dict.fromkeys([*existing,*forced]))
    return canonicalize_day_decision(result),outline


async def _schedule(state,job_id,day_no,route_service=None,pace_override=None):
    state['edit']=state['edits'].setdefault(day_no,{'stop_overrides':{},'day_transport':None,'segment_overrides':{}})
    outline=allocation_outline(state['allocation'],state['plan']['days'][day_no-1])
    referenced={outline['primary_anchor_id'],outline.get('fixed_meal_stop_id'),
                *outline.get('candidate_place_ids',[]),*outline.get('backup_candidate_ids',[]),
                *[stop['place_id'] for stop in state['itinerary'][day_no-1]['stops']]}
    referenced.discard(None)
    places=collect_places({**state['packs'],'user-places':{'places':state['user_places']}})
    resolver=PlaceResolver(state.get('connect'),job_id,'pre-schedule-resolution')
    current_order=[stop['place_id'] for stop in state['itinerary'][day_no-1]['stops']]
    planned=[outline['primary_anchor_id'],*outline.get('candidate_place_ids',[]),
             *([outline['fixed_meal_stop_id']] if outline.get('fixed_meal_stop_id') else []),
             *outline.get('backup_candidate_ids',[])]
    ordered=list(dict.fromkeys([*current_order,*planned]))
    contexts=build_day_resolution_contexts(places,ordered,outline.get('area_labels',[]))
    gate=await enforce_pre_schedule_resolution(places,resolver,referenced,contexts)
    state['_resolution_mapping']=gate.id_mapping
    if gate.id_mapping:
        resolved_by_old={old:next(place for place in places if place['id']==new)
                         for old,new in gate.id_mapping.items()}
        for pack in [*state['place_packs'].values(),{'places':state['user_places']}]:
            pack['places']=[copy.deepcopy(resolved_by_old.get(place['id'],place)) for place in pack.get('places',[])]
        (state['plan'],state['allocation'],state['itinerary'],state['sources'],state['edits'],state['modules'])=apply_resolution_mapping(
            gate.id_mapping,state['plan'],state['allocation'],state['itinerary'],state['sources'],state['edits'],state['modules'])
        state['edit']=state['edits'][day_no]
        state['packs']={**state['place_packs'],**state['modules'],'user-places':{'places':state['user_places']}}
        places=collect_places(state['packs'])
    decision,outline=_decision(state,day_no)
    state['packs']['user-places']={'places':state['user_places']}
    trip=state['trip']
    if pace_override:trip=trip.model_copy(update={'preferences':trip.preferences.model_copy(update={'pace':pace_override})})
    service=route_service if route_service is not None else _route_service(state.get('connect'))
    day=await schedule_day_with_routes(decision,outline,places,trip,day_no-1,service,state.get('connect'),job_id,
                                       constraints=state['edit'],trip_constraints=state['trip_constraints'])
    strengths=derive_day_interests(day,places,trip.preferences.interests);day['derived_interests']=list(strengths);day['day_interest_strength']=strengths
    errors=validate_day(day,places,expected_date=outline['date'],expected_city=outline['city'],required_ids=(outline['primary_anchor_id'],outline.get('fixed_meal_stop_id')))
    if errors:raise ReplanError('REPLAN_NO_FEASIBLE_SCHEDULE',json.dumps(errors[:4],ensure_ascii=False),422)
    state['itinerary'][day_no-1]=day
    return day,places


def _minute(value):
    hour,minute=map(int,str(value).split(':'))
    return hour*60+minute


def _clock(value):return f'{value//60:02d}:{value%60:02d}'


def _route_payload(route,origin_id,destination_id,mode,buffer_minutes,idle_minutes=0):
    duration=math.ceil(route.duration_seconds/60) if route.duration_seconds is not None else None
    return {'segment_id':f'{origin_id}->{destination_id}','from_place_id':origin_id,'to_place_id':destination_id,
        'mode':{'transit':'public_transit'}.get(mode,mode),'distance_meters':route.distance_meters,
        'duration_minutes':duration,'buffer_minutes':buffer_minutes,'fallback_schedule_minutes':0,
        'idle_minutes':idle_minutes,'route_status':route.verification_status,'route_provider':route.provider,
        'route_queried_at':route.queried_at,
        'route_input':{'origin_place_id':route.origin_place_id,'destination_place_id':route.destination_place_id,
            'origin_latitude':route.origin_latitude,'origin_longitude':route.origin_longitude,
            'destination_latitude':route.destination_latitude,'destination_longitude':route.destination_longitude,
            'selected_mode':route.mode},'route_error_code':route.route_error,'route_http_status':route.http_status,
        'route_provider_code':route.provider_response_code}


def _route_feasibility_trace(route,origin,destination,mode,*,required=True,safety_margin=0):
    if not required:
        return {'required':False,'amap_called':False,'mode':mode,'route_status':'not_applicable'}
    return {'required':True,'from_place_id':origin.get('id'),'from_name':origin.get('display_name') or origin.get('name'),
        'from_coordinates':{'latitude':origin.get('latitude'),'longitude':origin.get('longitude')},
        'to_place_id':destination.get('id'),'to_name':destination.get('display_name') or destination.get('name'),
        'to_coordinates':{'latitude':destination.get('latitude'),'longitude':destination.get('longitude')},
        'mode':{'transit':'public_transit'}.get(mode,mode),'amap_called':bool(route and (route.provider=='amap' or route.route_error)),
        'cache_hit':bool(route.cache_hit) if route else False,'http_status':route.http_status if route else None,
        'provider_code':route.provider_response_code if route else None,'distance_meters':route.distance_meters if route else None,
        'duration_seconds':route.duration_seconds if route else None,
        'duration_minutes':math.ceil(route.duration_seconds/60) if route and route.duration_seconds is not None else None,
        'route_provider':route.provider if route else None,'route_status':route.verification_status if route else 'unresolved',
        'verification_status':'needs_recheck' if route and route.verification_status=='estimated_by_distance' else
                              ('verified' if route and route.verification_status=='verified' else 'unavailable'),
        'route_error':route.route_error if route else 'ROUTE_UNRESOLVED','safety_margin_minutes':safety_margin}


def _sync_incoming_route(stop,segment):
    labels={'walking':'步行','driving':'驾车或出租车','public_transit':'公共交通'}
    stop.update(transport_mode=labels.get(segment['mode'],segment['mode']),transfer_minutes=segment['duration_minutes'],
        route_minutes=segment['duration_minutes'],buffer_minutes=segment['buffer_minutes'],
        fallback_schedule_minutes=segment['fallback_schedule_minutes'],idle_minutes=segment['idle_minutes'],
        distance_km=(round(segment['distance_meters']/1000,2) if segment['distance_meters'] is not None else None),
        route_status=segment['route_status'],route_provider=segment['route_provider'],route_queried_at=segment['route_queried_at'],
        route_input=segment['route_input'],route_error_code=segment['route_error_code'],
        route_http_status=segment['route_http_status'],route_provider_code=segment['route_provider_code'])


def _insertion_conflict(place_name,requested,earliest,reason,options=None):
    message=(f'要在 {requested} 到达{place_name}，当前安排最早只能在 {_clock(earliest)} 到达'
             if requested and earliest is not None else f'{place_name}无法在不改变现有日程的情况下加入')
    details=[{'type':'insertion_conflict','message':message,'requested_start_time':requested,
              'earliest_feasible_arrival':_clock(earliest) if earliest is not None else None,'reason':reason}]
    for option in options or []:details.append({'type':'adjustment_option','message':option})
    raise ReplanError('REPLAN_INSERTION_CONFLICT',message,409,details)


async def _insert_place_preserving_schedule(state,job_id,day_no,place,action,route_service=None):
    """Insert one stop without reordering, moving or dropping existing stops."""
    day=copy.deepcopy(state['itinerary'][day_no-1]);stops=day['stops'];places=collect_places(
        {**state['packs'],'user-places':{'places':state['user_places']}})
    by_id={item['id']:item for item in places};by_id[place['id']]=place
    if place['id'] in {stop['place_id'] for stop in stops}:
        return day,places,{'inserted_place_id':place['id'],'already_scheduled':True,'route_legs':[]}
    if place.get('city') and place.get('city')!=day.get('city'):
        raise ReplanError('REPLAN_CROSS_CITY_CONFLICT',f'{place["display_name"]}不属于当天城市 {day.get("city")}',409,
            [{'type':'city_mismatch','message':'新增地点与当天城市不一致','place_city':place.get('city'),'day_city':day.get('city')}])
    trip=state['trip'];pace=PACE_CONFIG[trip.preferences.pace]
    requested=getattr(action,'requested_start_time',None) or getattr(action,'preferred_start_time',None)
    strength=getattr(action,'time_constraint_strength','hard' if requested else 'preferred')
    tolerance=int(getattr(action,'time_tolerance_minutes',0) or 0)
    period=getattr(action,'preferred_period','any')
    target=_minute(requested) if requested else {'morning':9*60,'afternoon':14*60,'evening':18*60,'any':None}[period]
    dwell=int(getattr(action,'stay_minutes',None) or 90)
    trip_constraints=(state['trip_constraints'].model_dump(mode='json')
                      if hasattr(state['trip_constraints'],'model_dump') else state['trip_constraints'])
    start=_start_minute(trip,day_no-1,trip_constraints);end=_end_minute(trip,day_no-1,trip_constraints)
    day_date=str(trip.startDate.fromordinal(trip.startDate.toordinal()+day_no-1))
    has_departure=any(str(value.get('date'))==day_date for value in trip_constraints.get('departure_constraints',[]))
    # A user who explicitly asks for an evening start has also expressed that
    # the day may extend beyond the default optimization window. A real
    # departure constraint remains authoritative.
    effective_end=(max(end,min(23*60+59,target+dwell))
                   if target is not None and strength in {'hard','strong'} and not has_departure else end)
    service=route_service if route_service is not None else _route_service(state.get('connect'))
    old_segments={(item['from_place_id'],item['to_place_id']):copy.deepcopy(item) for item in day.get('segments',[])}
    choices=[];attempts=[]
    for position in range(len(stops)+1):
        previous=stops[position-1] if position else None;following=stops[position] if position<len(stops) else None
        previous_place=by_id.get(previous['place_id']) if previous else None;following_place=by_id.get(following['place_id']) if following else None
        inbound=None;outbound=None;inbound_minutes=0;outbound_minutes=0;inbound_buffer=0;outbound_buffer=0
        inbound_safety=0;outbound_safety=0;leg_traces=[]
        try:
            if previous_place:
                override=state['edits'][day_no].get('segment_overrides',{}).get(f'{previous_place["id"]}->{place["id"]}') or state['edits'][day_no].get('day_transport')
                mode=_mode(trip,previous_place,place,override)
                inbound=await service.route(previous_place,place,mode,day['city']) if service else estimate_route(previous_place,place,mode)
                inbound_safety=(ADD_PLACE_ESTIMATED_ROUTE_SAFETY_MARGIN_MINUTES
                                if inbound.verification_status=='estimated_by_distance' else 0)
                leg_traces.append(_route_feasibility_trace(inbound,previous_place,place,mode,safety_margin=inbound_safety))
                if inbound.duration_seconds is None or inbound.distance_meters is None:raise ValueError('route_unavailable')
                inbound_minutes=math.ceil(inbound.duration_seconds/60);inbound_buffer=max(pace.route_buffer_minutes,math.ceil(inbound_minutes*.15))
            if following_place:
                override=state['edits'][day_no].get('segment_overrides',{}).get(f'{place["id"]}->{following_place["id"]}') or state['edits'][day_no].get('day_transport')
                mode_out=_mode(trip,place,following_place,override)
                outbound=await service.route(place,following_place,mode_out,day['city']) if service else estimate_route(place,following_place,mode_out)
                outbound_safety=(ADD_PLACE_ESTIMATED_ROUTE_SAFETY_MARGIN_MINUTES
                                 if outbound.verification_status=='estimated_by_distance' else 0)
                leg_traces.append(_route_feasibility_trace(outbound,place,following_place,mode_out,safety_margin=outbound_safety))
                if outbound.duration_seconds is None or outbound.distance_meters is None:raise ValueError('route_unavailable')
                outbound_minutes=math.ceil(outbound.duration_seconds/60);outbound_buffer=max(pace.route_buffer_minutes,math.ceil(outbound_minutes*.15))
        except AmapProviderError:
            attempts.append({'position':position,'reason':'route_unavailable','route_legs':leg_traces});continue
        except ValueError as exc:
            attempts.append({'position':position,'reason':'route_unavailable','route_legs':leg_traces});continue
        earliest=(start if previous is None else _minute(previous['arrival_time'])+previous['dwell_minutes']+inbound_minutes+inbound_buffer+inbound_safety)
        actual=max(earliest,target) if target is not None else earliest
        if requested and strength in {'hard','strong'} and earliest>target+tolerance:
            attempts.append({'position':position,'reason':'requested_time_conflict','earliest':earliest,'route_legs':leg_traces});continue
        if requested and strength=='hard':actual=target
        if _open_for_visit(place,actual,dwell) is False:
            attempts.append({'position':position,'reason':'opening_hours_conflict','earliest':earliest,'route_legs':leg_traces});continue
        if actual+dwell>effective_end:
            attempts.append({'position':position,'reason':'day_window_conflict','earliest':earliest,'route_legs':leg_traces});continue
        if following and actual+dwell+outbound_minutes+outbound_buffer+outbound_safety>_minute(following['arrival_time']):
            attempts.append({'position':position,'reason':'next_stop_conflict','earliest':earliest,'route_legs':leg_traces});continue
        score=(abs(actual-(target if target is not None else actual)),position)
        choices.append((score,position,actual,inbound,outbound,inbound_minutes,outbound_minutes,inbound_buffer,outbound_buffer,
                        inbound_safety,outbound_safety,leg_traces))
    if not choices:
        if attempts and all(item['reason']=='route_unavailable' for item in attempts):
            raise ReplanError('REPLAN_ROUTE_UNRESOLVED',f'{place["display_name"]}相邻路线缺少有效坐标或无法估算',409,
                [{'type':'route_unresolved','message':'相邻路线无法获得真实结果，也无法根据可信坐标进行保守估算。',
                  'attempts':attempts}])
        earliest_values=[item.get('earliest') for item in attempts if item.get('earliest') is not None]
        earliest=(min(earliest_values,key=lambda value:abs(value-target)) if earliest_values and target is not None
                  else (min(earliest_values) if earliest_values else None))
        reason=next((item['reason'] for item in attempts if item['reason']!='requested_time_conflict'),attempts[0]['reason'] if attempts else 'no_slot')
        if reason=='opening_hours_conflict':
            raise ReplanError('REPLAN_OPENING_HOURS_CONFLICT',f'{place["display_name"]}在指定时间无法确认开放',409,
                [{'type':'opening_hours_conflict','message':'新增地点的开放时间与指定到达时间冲突。','attempts':attempts}])
        if reason in {'requested_time_conflict','next_stop_conflict','day_window_conflict'}:
            message=(f'按保守路线计算，最早只能在 {_clock(earliest)} 到达{place["display_name"]}' if earliest is not None
                     else f'{place["display_name"]}没有可行插入时段')
            raise ReplanError('REPLAN_NO_FEASIBLE_SLOT',message,409,
                [{'type':'route_or_time_conflict','message':message,'requested_start_time':requested,
                  'earliest_feasible_arrival':_clock(earliest) if earliest is not None else None,'attempts':attempts}])
        _insertion_conflict(place['display_name'],requested,earliest,reason,[
            f'将{place["display_name"]}调整到更晚的可行时间',
            '缩短相邻活动的停留时间','移动或移除一个已有活动','取消新增'])
    _,position,arrival,inbound,outbound,in_min,out_min,in_buffer,out_buffer,in_safety,out_safety,leg_traces=min(choices,key=lambda item:item[0])
    template=copy.deepcopy(stops[position-1] if position else (stops[0] if stops else {}))
    new_stop=template
    new_stop.update(place_id=place['id'],arrival_time=_clock(arrival),dwell_minutes=dwell,
        estimated_cost=None,cost_note='费用与适用条件尚未核实，出发前请复核。',
        practical_note=place.get('description') or '用户新增地点，开放与接待条件出发前请复核。',
        time_guard='用户新增地点；现有日程保持不变。',preferred_start_time=requested,
        hard_start_time=requested if strength=='hard' else None,locked=False,must_keep=False,user_created=True,
        note='由用户新增，采用保守插入，不调整已有停靠。',priority=4,transport_to_next=None,
        meal_role=None,meal_window_preferred_start=None,meal_window_preferred_end=None,
        meal_window_acceptable_start=None,meal_window_acceptable_end=None,meal_window_status='not_applicable',
        meal_timing_penalty=0,meal_timing_reason='')
    if inbound:
        idle=max(0,arrival-(_minute(stops[position-1]['arrival_time'])+stops[position-1]['dwell_minutes'])-in_min-in_buffer)
        inbound_segment=_route_payload(inbound,stops[position-1]['place_id'],place['id'],inbound.mode,in_buffer,idle)
        _sync_incoming_route(new_stop,inbound_segment)
    else:
        inbound_segment=None;new_stop.update(transport_mode='当天首站',transfer_minutes=None,route_minutes=None,
            buffer_minutes=0,fallback_schedule_minutes=0,idle_minutes=max(0,arrival-start),distance_km=None,
            route_status='not_applicable',route_provider=None,route_queried_at=None,route_input=None,
            route_error_code=None,route_http_status=None,route_provider_code=None)
    outbound_segment=None
    if outbound:
        idle=max(0,_minute(stops[position]['arrival_time'])-(arrival+dwell)-out_min-out_buffer)
        outbound_segment=_route_payload(outbound,place['id'],stops[position]['place_id'],outbound.mode,out_buffer,idle)
    new_stops=[*stops[:position],new_stop,*stops[position:]]
    replacement={}
    if inbound_segment:replacement[(inbound_segment['from_place_id'],inbound_segment['to_place_id'])]=inbound_segment
    if outbound_segment:replacement[(outbound_segment['from_place_id'],outbound_segment['to_place_id'])]=outbound_segment
    new_segments=[]
    for left,right in zip(new_stops,new_stops[1:]):
        key=(left['place_id'],right['place_id']);segment=replacement.get(key) or old_segments.get(key)
        if not segment:_insertion_conflict(place['display_name'],requested,None,'route_segment_missing')
        new_segments.append(segment)
    # Only the inserted stop and (for a middle insertion) its immediate
    # successor receive new route metadata. Every unaffected stop is copied
    # byte-for-byte from the existing schedule.
    if outbound_segment:_sync_incoming_route(new_stops[position+1],outbound_segment)
    day['stops']=new_stops;day['segments']=new_segments
    estimated_legs=[leg for leg in leg_traces if leg.get('route_status')=='estimated_by_distance']
    if estimated_legs:
        day.setdefault('schedule_warnings',[]).append({'code':'ROUTE_ESTIMATED_FOR_INSERTION','severity':'warning','day':day_no,
            'place_id':place['id'],'message':'该段路线暂未获得高德实时结果，当前按保守时间估算，出发前建议复核。',
            'metadata':{'estimated_segments':[f'{leg["from_place_id"]}->{leg["to_place_id"]}' for leg in estimated_legs],
                        'safety_margin_minutes':ADD_PLACE_ESTIMATED_ROUTE_SAFETY_MARGIN_MINUTES}})
    opening_fact=(place.get('official_facts') or {}).get('opening_hours') or {}
    if opening_fact.get('status') not in {'verified','provider_verified','conflicting'} or not opening_fact.get('sources'):
        day.setdefault('schedule_warnings',[]).append({'code':'FACT_NEEDS_RECHECK','severity':'warning','day':day_no,
            'place_id':place['id'],'message':f'{place["display_name"]}开放时间尚未核实，当前可加入行程，出发前建议确认开放情况。',
            'metadata':{'fact':'opening_hours','requested_start_time':requested}})
    density=_density(new_stops,new_segments,by_id,start,effective_end,pace,0,[place['id']],0,False)
    day['density_evaluation']=density;day['total_travel_minutes']=density['total_route_minutes']
    day['total_visit_minutes']=density['visit_minutes']+density['meal_minutes']
    state['itinerary'][day_no-1]=day
    previous=stops[position-1] if position else None;following=stops[position] if position<len(stops) else None
    previous_place=by_id.get(previous['place_id']) if previous else None;following_place=by_id.get(following['place_id']) if following else None
    previous_end=(_minute(previous['arrival_time'])+previous['dwell_minutes']) if previous else None
    trace={'inserted_place_id':place['id'],'inserted_place_name':place.get('display_name'),
        'inserted_place_coordinates':{'latitude':place.get('latitude'),'longitude':place.get('longitude')},
        'previous_stop':({'place_id':previous['place_id'],'name':previous_place.get('display_name'),
                          'coordinates':{'latitude':previous_place.get('latitude'),'longitude':previous_place.get('longitude')}} if previous else None),
        'next_stop':({'place_id':following['place_id'],'name':following_place.get('display_name'),
                      'coordinates':{'latitude':following_place.get('latitude'),'longitude':following_place.get('longitude')}} if following else None),
        'requested_start_time':requested,'previous_stop_end_time':_clock(previous_end) if previous_end is not None else None,
        'available_gap_minutes':target-previous_end if target is not None and previous_end is not None else None,
        'route_buffer_minutes':in_buffer,'safety_margin_minutes':in_safety,
        'earliest_feasible_arrival':_clock(previous_end+in_min+in_buffer+in_safety) if previous_end is not None else _clock(start),
        'route_legs':leg_traces,'used_fallback_estimate':bool(estimated_legs)}
    return day,places,trace


def _claim(connect,job_id,action,day_no,request,affected_days=None):
    run_id=str(uuid.uuid4());created=_now()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');job=db.execute('SELECT status,result FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not job:raise ReplanError('NOT_FOUND','行程不存在',404)
        if job['status'] not in ('done','done_with_warnings'):raise ReplanError('REPLAN_CONFLICT','行程正在被其他操作修改',409)
        changed=db.execute("UPDATE jobs SET status='validating',stage='editable-replan',run_id=?,lock_owner='editable-replan',lease_expires_at=?,current_operation='editable_replan',updated_at=? WHERE id=? AND status IN ('done','done_with_warnings')",
            (run_id,(datetime.now(timezone.utc)+timedelta(minutes=3)).isoformat(),created,job_id)).rowcount
        if changed!=1:raise ReplanError('REPLAN_CONFLICT','行程正在被其他操作修改',409)
        affected_days=affected_days or [day_no]
        packs=[*[f'itinerary-day-{day}' for day in affected_days],'itinerary-coherence','destination-profile']
        db.execute("INSERT INTO trip_replan_runs(replan_id,job_id,action,day_number,request_json,status,affected_packs_json,before_result,created_at) VALUES(?,?,?,?,?,'running',?,?,?)",
            (run_id,job_id,action,day_no,json.dumps(request,ensure_ascii=False),json.dumps(packs,ensure_ascii=False),job['result'],created))
    return run_id,job['status']


def _restore_claim(connect,job_id,run_id,status,exc):
    with connect() as db:
        db.execute('UPDATE trip_replan_runs SET status=?,error_code=?,error_message=?,completed_at=? WHERE replan_id=?',('failed',getattr(exc,'code',type(exc).__name__),str(exc),_now(),run_id))
        db.execute("UPDATE jobs SET status=?,stage='done',run_id=NULL,lock_owner=NULL,lease_expires_at=NULL,current_operation='completed',updated_at=? WHERE id=? AND run_id=?",(status,_now(),job_id,run_id))


async def _commit(connect,job_id,day_no,state,before,action,request,*,initiated_by='user',instruction=None,parsed=None,scheduled=True,affected_days=None):
    affected_days=sorted(set(affected_days or [day_no]));run_id,prior=_claim(connect,job_id,action,day_no,request,affected_days);state['connect']=connect
    try:
        if scheduled:
            for number in affected_days:await _schedule(state,job_id,number)
        places=collect_places({**state['packs'],'user-places':{'places':state['user_places']}})
        for owner in state['allocation'].get('place_assignments',{}).values():
            owner['status']='reserved';owner['reservation']='soft' if owner['role']=='backup' else 'hard'
        state['allocation']=finalize_selected(state['allocation'],state['itinerary'],places)
        ownership=validate_cross_day_ownership(state['allocation'],places,state['itinerary'])
        coherence=validate_trip_coherence(state['itinerary'],state['plan'],places,state['trip'].preferences.model_dump(mode='json'))
        if ownership or coherence:raise ReplanError('REPLAN_COHERENCE_FAILED',json.dumps((ownership+coherence)[:5],ensure_ascii=False))
        packs={**state['place_packs'],**state['modules'],'user-places':{'places':state['user_places']},
               'itinerary':{'itinerary':state['itinerary']},'enrichment':{'practical':'complete','language_notes':'complete','pending_packs':[]},'enrichment_warnings':[]}
        old=json.loads(state['job']['result']);warnings=list(old.get('generation',{}).get('warnings',[]))
        warnings.append({'code':'EDITABLE_REPLAN_APPLIED','day':day_no,
                         'message':f'用户已编辑 Day {"、".join(map(str,affected_days))}；未受影响日期与研究资料均已复用。'})
        profile=assemble(state['trip'],job_id,packs,old.get('generation',{}).get('mode','llm'),warnings,json.loads(state['job']['manifest']) if state['job'].get('manifest') else None)
        handoff=check_handoff(profile,packs)
        if handoff:raise ReplanError('REPLAN_HANDOFF_FAILED',json.dumps(handoff[:5],ensure_ascii=False))
        if 'days' not in before:before={'days':{str(day_no):before['day']},'allocation':before['allocation'],'user_places':before['user_places'],'edits':{str(day_no):before['edit']}}
        after=_scope_snapshot(state,affected_days);diff=_scope_diff(before,after,places);completed=_now()
        store=PackStore(connect,job_id,state['trip'].model_dump(mode='json'),state['trip'].days,json.loads(state['job']['manifest']) if state['job'].get('manifest') else None,run_id)
        if state.get('_resolution_mapping'):
            for pack_id,payload in state['place_packs'].items():
                store.save_valid(pack_id,payload,store.expected_signature(pack_id,dependencies(pack_id,state['trip'].days)),0,0,
                    observability={'repair_result':'pre_schedule_resolution'},source_payload=payload)
            for pack_id,payload in state['modules'].items():
                store.save_valid(pack_id,payload,store.expected_signature(pack_id,dependencies(pack_id,state['trip'].days)),0,0,
                    observability={'repair_result':'pre_schedule_resolution'},source_payload=payload)
            store.save_valid('itinerary-plan',state['plan'],store.expected_signature('itinerary-plan',dependencies('itinerary-plan',state['trip'].days)),0,0,
                observability={'repair_result':'pre_schedule_resolution'},source_payload=state['plan'])
        store.save_valid('itinerary-allocation',state['allocation'],store.expected_signature('itinerary-allocation',dependencies('itinerary-allocation',state['trip'].days)),0,0,observability={'repair_result':'editable_replan'},source_payload=state['allocation'])
        for number in affected_days:
            changed_day=state['itinerary'][number-1]
            source={'optional_stop_order':[s['place_id'] for s in changed_day['stops'] if s['place_id'] not in {state['plan']['days'][number-1]['primary_anchor_id'],state['plan']['days'][number-1].get('fixed_meal_stop_id')}],
                    'day_intent':changed_day['theme'],'practical_notes':[changed_day['summary']]}
            store.save_valid(f'itinerary-day-{number}',changed_day,store.expected_signature(f'itinerary-day-{number}',dependencies(f'itinerary-day-{number}',state['trip'].days)),0,0,observability={'repair_result':'editable_replan'},source_payload=source)
        store.save_valid('itinerary-coherence',{'passed':True,'errors':[]},store.expected_signature('itinerary-coherence',dependencies('itinerary-coherence',state['trip'].days)),0,0,observability={'repair_result':'editable_replan'})
        store.save_valid('destination-profile',profile,store.expected_signature('destination-profile',dependencies('destination-profile',state['trip'].days)),0,0,validation_warnings=profile['fact_verification']['warnings'],observability={'repair_result':'editable_replan'},source_payload=profile)
        final='done_with_warnings' if profile['generation']['warnings'] else 'done'
        with connect() as db:
            db.execute('DELETE FROM trip_user_places WHERE job_id=?',(job_id,))
            for place in state['user_places']:
                owner=state['allocation'].get('place_assignments',{}).get(place['id'],{})
                db.execute('INSERT INTO trip_user_places(job_id,place_id,day_number,place_json,created_at,updated_at) VALUES(?,?,?,?,?,?)',(job_id,place['id'],int(owner.get('day',day_no)),json.dumps(place,ensure_ascii=False),completed,completed))
            revision_ids=[];versions={}
            for number in affected_days:
                edit=state['edits'][number]
                db.execute("INSERT INTO trip_day_edit_state(job_id,day_number,stop_overrides_json,day_transport,segment_overrides_json,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(job_id,day_number) DO UPDATE SET stop_overrides_json=excluded.stop_overrides_json,day_transport=excluded.day_transport,segment_overrides_json=excluded.segment_overrides_json,updated_at=excluded.updated_at",
                    (job_id,number,json.dumps(edit['stop_overrides'],ensure_ascii=False),edit.get('day_transport'),json.dumps(edit['segment_overrides'],ensure_ascii=False),completed))
                version=db.execute('SELECT COALESCE(MAX(version),0)+1 value FROM trip_day_revisions WHERE job_id=? AND day_number=?',(job_id,number)).fetchone()['value'];versions[number]=version
                revision_id=str(uuid.uuid4());revision_ids.append(revision_id)
                day_before={'day':before['days'][str(number)],'allocation':before['allocation'],'user_places':before['user_places'],'edit':before['edits'][str(number)]}
                day_after={'day':after['days'][str(number)],'allocation':after['allocation'],'user_places':after['user_places'],'edit':after['edits'][str(number)]}
                day_diff=next(item for item in diff['days'] if item['day']==number)
                stored_before=before if len(affected_days)>1 else day_before;stored_after=after if len(affected_days)>1 else day_after
                db.execute("INSERT INTO trip_day_revisions(revision_id,job_id,day_number,version,action,initiated_by,instruction_text,parsed_constraints_json,before_state_json,after_state_json,diff_json,scheduler_version,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (revision_id,job_id,number,version,action,initiated_by,instruction,json.dumps(parsed,ensure_ascii=False) if parsed else None,json.dumps(stored_before,ensure_ascii=False),json.dumps(stored_after,ensure_ascii=False),json.dumps(day_diff,ensure_ascii=False),SCHEDULER_VERSION,'active',completed))
                db.execute("UPDATE trip_day_revisions SET status='superseded' WHERE job_id=? AND day_number=? AND revision_id<>? AND status='active'",(job_id,number,revision_id))
            db.execute("UPDATE jobs SET status=?,stage='done',progress=100,result=?,packs=?,error=NULL,run_id=NULL,lock_owner=NULL,lease_expires_at=NULL,current_operation='completed',updated_at=? WHERE id=? AND run_id=?",
                (final,json.dumps(profile,ensure_ascii=False),json.dumps(packs,ensure_ascii=False),completed,job_id,run_id))
            db.execute("UPDATE trip_replan_runs SET status='completed',after_result=?,completed_at=? WHERE replan_id=?",(json.dumps(profile,ensure_ascii=False),completed,run_id))
        return {'job_id':job_id,'day':day_no,'affected_day_ids':affected_days,'revision_id':revision_ids[0],
                'revision_ids':revision_ids,'day_schedule_version':versions[day_no],'status':final,'diff':diff,'replan_version':REPLAN_VERSION}
    except Exception as exc:
        _restore_claim(connect,job_id,run_id,prior,exc);raise


async def search_add(connect,job_id,day_id,request:SearchAddRequest):
    day_no=_day_number(day_id,job_id);state=_load(connect,job_id,day_no);city=state['itinerary'][day_no-1]['city']
    if request.city!=city:raise ReplanError('CITY_MISMATCH','新增地点必须属于当天城市')
    try:client=AmapClient(connect=connect)
    except AmapConfigurationError as exc:raise ReplanError('POI_PROVIDER_ERROR','高德 Web Service 尚未配置',503) from exc
    candidates,_=await search_pois(client,request.query,request.city,limit=5)
    if not request.provider_place_id:
        return {'requires_confirmation':True,'candidates':[item.model_dump(mode='json') for item in candidates]}
    selected=next((item for item in candidates if item.provider_place_id==request.provider_place_id),None)
    if not selected:
        selected,_=await get_poi_detail(client,request.provider_place_id)
    if not selected:raise ReplanError('POI_NOT_FOUND','没有找到所选高德地点')
    place=_place_from_candidate(selected,city,request.query);before=_snapshot(state,day_no);_assign(state,day_no,place)
    state['edit']['stop_overrides'][place['id']]={'stay_minutes':request.stay_minutes or 90,'preferred_start_time':request.preferred_time,
        'locked':False,'must_keep':False,'force_include':True,'note':'用户新增地点','priority':4,'transport_to_next':None}
    state['connect']=connect
    action=AddPlaceAction(query=request.query,preferred_start_time=request.preferred_time,
        requested_start_time=request.preferred_time,time_constraint_strength='hard' if request.preferred_time else 'preferred',
        stay_minutes=request.stay_minutes or 90,preserve_existing_stops=True)
    day,places,trace=await _insert_place_preserving_schedule(state,job_id,day_no,place,action)
    proposed=_snapshot(state,day_no);diff=_diff(before['day'],day,places)
    saved_request={**request.model_dump(mode='json'),'route_feasibility_trace':trace}
    preview_warnings=[warning for warning in day.get('schedule_warnings',[])
                      if warning.get('place_id')==place['id'] and warning.get('code') in
                         {'FACT_NEEDS_RECHECK','ROUTE_ESTIMATED_FOR_INSERTION'}]
    result=_save_preview(connect,job_id,day_no,'search_add',saved_request,proposed,diff,warnings=preview_warnings)
    result['route_feasibility']=trace
    return result


async def custom_add(connect,job_id,day_id,request:CustomActivityRequest):
    day_no=_day_number(day_id,job_id);state=_load(connect,job_id,day_no);before=_snapshot(state,day_no)
    place=_custom_place(request,state['itinerary'][day_no-1]['city']);_assign(state,day_no,place)
    state['edit']['stop_overrides'][place['id']]={'stay_minutes':request.stay_minutes,'preferred_start_time':request.start_time_preference,
        'locked':False,'must_keep':False,'force_include':True,'note':request.note,'priority':3,'transport_to_next':None}
    decision,_=_decision(state,day_no);decision['optional_stop_order'].append(place['id']);state['sources'][day_no]=decision
    state['connect']=connect;day,places=await _schedule(state,job_id,day_no);proposed=_snapshot(state,day_no);diff=_diff(before['day'],day,places)
    return _save_preview(connect,job_id,day_no,'custom_activity',request.model_dump(mode='json'),proposed,diff)


def _save_preview(connect,job_id,day_no,action,request,proposed,diff,*,scope='current_day',affected_day_ids=None,
                  unplaced_place_ids=None,warnings=None):
    preview_id=str(uuid.uuid4());created=datetime.now(timezone.utc);expires=created+timedelta(minutes=30)
    with connect() as db:db.execute("INSERT INTO trip_replan_previews VALUES(?,?,?,?,?,?,?,'pending',?,?,NULL)",(preview_id,job_id,day_no,action,json.dumps(request,ensure_ascii=False),json.dumps(proposed,ensure_ascii=False),json.dumps(diff,ensure_ascii=False),created.isoformat(),expires.isoformat()))
    return {'preview_id':preview_id,'day':day_no,'action':action,'scope':scope,
            'affected_day_ids':affected_day_ids or [day_no],'unplaced_place_ids':unplaced_place_ids or [],
            'diff':diff,'warnings':warnings or [],'expires_at':expires.isoformat()}


async def confirm_preview(connect,job_id,preview_id):
    with connect() as db:row=db.execute("SELECT * FROM trip_replan_previews WHERE preview_id=? AND job_id=?",(preview_id,job_id)).fetchone()
    if not row:raise ReplanError('PREVIEW_NOT_FOUND','重规划预览不存在',404)
    if row['status']!='pending':raise ReplanError('PREVIEW_ALREADY_USED','该预览已经确认或失效',409)
    if row['expires_at']<_now():raise ReplanError('PREVIEW_EXPIRED','重规划预览已过期，请重新预览',409)
    day_no=row['day_number'];state=_load(connect,job_id,day_no);proposed=json.loads(row['proposed_state_json'])
    request=json.loads(row['request_json']);affected=request.get('affected_day_ids') or [day_no]
    before=_scope_snapshot(state,affected)
    if 'days' in proposed:
        for number,payload in proposed['days'].items():state['itinerary'][int(number)-1]=payload
        state['allocation']=proposed['allocation'];state['user_places']=proposed['user_places']
        for number,edit in proposed.get('edits',{}).items():state['edits'][int(number)]=edit
        if proposed.get('plan'):state['plan']=proposed['plan']
        for number,source in proposed.get('sources',{}).items():state['sources'][int(number)]=source
        state['edit']=state['edits'][day_no]
    else:
        state['itinerary'][day_no-1]=proposed['day'];state['allocation']=proposed['allocation'];state['user_places']=proposed['user_places'];state['edit']=proposed['edit'];state['edits'][day_no]=proposed['edit']
    instruction=request.get('instruction') if row['action']=='instruction_replan' else None
    parsed=request.get('parsed') if row['action']=='instruction_replan' else None
    result=await _commit(connect,job_id,day_no,state,before,row['action'],request,scheduled=False,instruction=instruction,parsed=parsed,affected_days=affected)
    with connect() as db:db.execute("UPDATE trip_replan_previews SET status='confirmed',confirmed_at=? WHERE preview_id=?",(_now(),preview_id))
    return result


async def edit_stop(connect,job_id,day_id,place_id,request:StopEditRequest):
    day_no=_day_number(day_id,job_id);state=_load(connect,job_id,day_no);before=_snapshot(state,day_no)
    current=next((s for s in state['itinerary'][day_no-1]['stops'] if s['place_id']==place_id),None)
    if not current:raise ReplanError('STOP_NOT_IN_DAY','指定地点不在当天日程中')
    override=state['edit']['stop_overrides'].setdefault(place_id,{})
    for key,value in request.model_dump(exclude_unset=True).items():
        if key!='transport_to_next':override[key]=value
    if request.locked is True and not override.get('hard_start_time'):override['hard_start_time']=current['arrival_time']
    return await _commit(connect,job_id,day_no,state,before,'edit_stop',request.model_dump(mode='json'))


async def delete_stop(connect,job_id,day_id,place_id):
    day_no=_day_number(day_id,job_id);state=_load(connect,job_id,day_no);before=_snapshot(state,day_no)
    current=next((s for s in state['itinerary'][day_no-1]['stops'] if s['place_id']==place_id),None)
    if not current:raise ReplanError('STOP_NOT_IN_DAY','指定地点不在当天日程中')
    override=state['edit']['stop_overrides'].get(place_id,{})
    allocated=next(d for d in state['allocation']['days'] if d['day']==day_no)
    if place_id in (allocated['primary_anchor_id'],allocated.get('fixed_meal_stop_id')) or override.get('must_keep'):
        raise ReplanError('IMMUTABLE_STOP','主景点、固定用餐和必留地点不能删除')
    for key in ('preferred_candidate_ids','backup_candidate_ids'):allocated[key]=[pid for pid in allocated[key] if pid!=place_id]
    release_place(state['allocation'],place_id,day=day_no);state['edit']['stop_overrides'].pop(place_id,None)
    state['edit']['segment_overrides']={key:value for key,value in state['edit']['segment_overrides'].items() if place_id not in key.split('->')}
    state['user_places']=[p for p in state['user_places'] if p['id']!=place_id]
    with connect() as db:db.execute('DELETE FROM routes WHERE origin_place_id=? OR destination_place_id=?',(place_id,place_id))
    return await _commit(connect,job_id,day_no,state,before,'delete_stop',{'place_id':place_id})


async def set_day_transport(connect,job_id,day_id,request:DayTransportRequest):
    day_no=_day_number(day_id,job_id);state=_load(connect,job_id,day_no);before=_snapshot(state,day_no);state['edit']['day_transport']=request.transport_mode
    return await _commit(connect,job_id,day_no,state,before,'day_transport',request.model_dump(mode='json'))


async def set_segment_transport(connect,job_id,day_id,request:SegmentTransportRequest):
    day_no=_day_number(day_id,job_id);state=_load(connect,job_id,day_no);before=_snapshot(state,day_no)
    ids=[s['place_id'] for s in state['itinerary'][day_no-1]['stops']]
    if not any(a==request.origin_place_id and b==request.destination_place_id for a,b in zip(ids,ids[1:])):
        raise ReplanError('SEGMENT_NOT_IN_DAY','交通段必须连接当天两个相邻地点')
    state['edit']['segment_overrides'][f'{request.origin_place_id}->{request.destination_place_id}']=request.transport_mode
    return await _commit(connect,job_id,day_no,state,before,'segment_transport',request.model_dump(mode='json'))


async def _parser_completion(client,messages):
    return await client.chat.completions.create(model=os.getenv('ZHIPU_MODEL','glm-4-plus'),messages=messages,
                                                response_format={'type':'json_object'},temperature=0)


def _action_identity(action):
    kind=getattr(action,'type',None)
    if kind=='add_place':return kind,_normalized_place_name(getattr(action,'query',''))
    if kind in {'remove_place','keep_place','move_place'}:
        return kind,getattr(action,'place_ref',None),getattr(action,'selector',None)
    if kind=='set_transport':return kind,getattr(action,'level',None),getattr(action,'from_place_ref',None),getattr(action,'to_place_ref',None)
    return kind,json.dumps(action.model_dump(mode='json'),ensure_ascii=False,sort_keys=True)


async def _parse_instruction(instruction,day,places,total_days=1,current_day=1,selected_place_id=None):
    catalog=[{'place_id':s['place_id'],'name':next((p['display_name'] for p in places if p['id']==s['place_id']),s['place_id']),
              'arrival_time':s.get('arrival_time')} for s in day['stops']]
    key=os.getenv('ZHIPU_API_KEY','').strip()
    if not key:
        result=parse_replan_deterministic(instruction,catalog,current_day,total_days,selected_place_id)
        return resolve_scope(instruction,result,current_day,total_days),{'parser_model':'deterministic','raw_parser_output':None,
            'normalized_constraints':result.model_dump(mode='json'),'validation_errors':[],'repair_attempt_count':0}
    schema=StructuredReplanRequest.model_json_schema()
    prompt=f'''解析用户在“告诉 Agent 这一天怎么调”入口提交的修改意图，严格按唯一 JSON Schema 输出。
UI scope context=current_day，当前 Day={current_day}，总天数={total_days}，selected_place_id={selected_place_id}。
当前日程：{json.dumps(catalog,ensure_ascii=False)}
用户指令：{instruction}
JSON Schema：{json.dumps(schema,ensure_ascii=False)}
规则：actions 可同时包含多个意图，不得遗漏。“今天/这一天/其余/剩下”默认只指当前 Day。只有明确跨日才用 affected_days，明确整趟才用 whole_trip。地点只输出 query/place_ref，禁止创造 ID。数组必须是 [] 而非 null。'''
    timeout=httpx.Timeout(connect=15,read=90,write=15,pool=15)
    validation_errors=[];raw_text='';repair_count=0
    try:
        async with AsyncOpenAI(api_key=key,base_url=os.getenv('ZHIPU_BASE_URL','https://open.bigmodel.cn/api/paas/v4'),timeout=timeout,max_retries=0) as client:
            messages=[{'role':'system','content':'你是旅行行程编辑意图解析器，只依据用户指令、目录与给定 Schema 输出结构化约束。'}, {'role':'user','content':prompt}]
            for attempt in range(2):
                response=await _parser_completion(client,messages);raw_text=response.choices[0].message.content or '{}'
                try:
                    raw=json.loads(raw_text);normalized=normalize_replan_request(raw,catalog)
                    parsed=StructuredReplanRequest.model_validate(normalized)
                    local=parse_replan_deterministic(instruction,catalog,current_day,total_days,selected_place_id)
                    positions={_action_identity(action):index for index,action in enumerate(parsed.actions)}
                    for action in local.actions:
                        identity=_action_identity(action)
                        if identity in positions:
                            # Deterministic extraction wins for exact time and
                            # relative references because it is grounded in UI context.
                            parsed.actions[positions[identity]]=action
                        else:
                            positions[identity]=len(parsed.actions);parsed.actions.append(action)
                    parsed=resolve_scope(instruction,parsed,current_day,total_days)
                    return parsed,{'parser_model':os.getenv('ZHIPU_MODEL','glm-4-plus'),'raw_parser_output':raw,
                                   'normalized_constraints':parsed.model_dump(mode='json'),'validation_errors':validation_errors,
                                   'repair_attempt_count':repair_count}
                except (json.JSONDecodeError,ValidationError,ValueError) as exc:
                    validation_errors.append(str(exc));repair_count+=1
                    if attempt:
                        raise ReplanError('REPLAN_INSTRUCTION_PARSE_FAILED','暂时无法理解这项调整，请换一种说法',422) from exc
                    messages=[{'role':'system','content':'只修复 JSON 结构以满足 Schema，不改变用户意图。'},
                              {'role':'user','content':f'原始输出：{raw_text}\n校验错误：{exc}\nSchema：{json.dumps(schema,ensure_ascii=False)}'}]
    except ReplanError:raise
    except (httpx.HTTPError,TimeoutError,APIConnectionError,APITimeoutError,APIError) as exc:
        raise ReplanError('REPLAN_PROVIDER_UNAVAILABLE','智能调整服务暂时不可用',503) from exc
    except Exception as exc:
        raise ReplanError('REPLAN_INSTRUCTION_PARSE_FAILED','暂时无法理解这项调整，请换一种说法',422) from exc


def _match_current_place(ref,catalog,selected_place_id=None):
    if not ref:return selected_place_id
    if ref in {item['place_id'] for item in catalog}:return ref
    needle=re.sub(r'[\s·•（）()\-_—]+','',str(ref)).lower()
    matches=[item['place_id'] for item in catalog if needle and (needle==re.sub(r'[\s·•（）()\-_—]+','',item['name']).lower()
             or needle in re.sub(r'[\s·•（）()\-_—]+','',item['name']).lower())]
    if len(matches)>1:raise ReplanError('REPLAN_PLACE_AMBIGUOUS',f'“{ref}”对应多个当天地点，请写明具体名称',422)
    return matches[0] if matches else None


def _compile_execution_plan(request:StructuredReplanRequest,catalog,selected_place_id=None):
    plan=_ExecutionPlan(scope=request.scope,affected_day_ids=list(request.affected_day_ids))
    all_ids=[item['place_id'] for item in catalog]
    for action in request.actions:
        if isinstance(action,KeepPlaceAction):
            pid=_match_current_place(action.place_ref,catalog,selected_place_id)
            if not pid:raise ReplanError('REPLAN_PLACE_NOT_FOUND',f'当天行程中没有找到“{action.place_ref}”',422)
            plan.keep_place_ids.append(pid);plan.keep_locks[pid]=action.lock
        elif isinstance(action,RemovePlaceAction):
            if action.selector=='current_day_remaining':plan.remove_place_ids.extend(pid for pid in all_ids if pid not in plan.keep_place_ids)
            elif action.selector=='current_day_afternoon':plan.remove_place_ids.extend(item['place_id'] for item in catalog if (item.get('arrival_time') or '00:00')>='12:00')
            else:
                pid=_match_current_place(action.place_ref,catalog,selected_place_id)
                if not pid:raise ReplanError('REPLAN_PLACE_NOT_FOUND',f'当天行程中没有找到“{action.place_ref or "所选地点"}”',422)
                plan.remove_place_ids.append(pid)
        elif isinstance(action,DedicateDayAction):
            plan.dedicated_place_id=_match_current_place(action.place_ref,catalog,selected_place_id) if action.place_ref else None
            plan.dedicated_place_query=action.place_query or (None if plan.dedicated_place_id else action.place_ref)
            plan.redistribute_removed_stops=action.redistribute_removed_stops
        elif isinstance(action,(AddPlaceAction,MustIncludeAction)):plan.add_actions.append(action)
        elif isinstance(action,AvoidAction):plan.avoid_actions.append(action)
        elif isinstance(action,ReplacePlaceAction):plan.replace_actions.append(action)
        elif isinstance(action,MovePlaceAction):plan.move_actions.append(action)
        elif isinstance(action,SetTransportAction):plan.transport_actions.append(action)
        elif isinstance(action,SetTimeWindowAction):plan.time_actions.append(action)
        elif isinstance(action,SetMealPreferenceAction):plan.meal_actions.append(action)
        elif isinstance(action,SetActivityPreferenceAction):plan.activity_actions.append(action)
        elif isinstance(action,SetCityDayAction):plan.city_actions.append(action)
        elif isinstance(action,ReflowAction):plan.reflow_actions.append(action)
        elif isinstance(action,ReplanDayAction):plan.replan_day=True
        elif isinstance(action,RedistributeStopsAction):
            plan.redistribute_removed_stops=True
            if action.selector=='current_day_afternoon':plan.remove_place_ids.extend(item['place_id'] for item in catalog if (item.get('arrival_time') or '00:00')>='12:00')
            else:plan.remove_place_ids.extend(item['place_id'] for item in catalog if item['place_id'] not in plan.keep_place_ids)
        elif isinstance(action,AddFreeTimeAction):plan.free_time_actions.append(action)
        elif isinstance(action,SetPaceAction):
            if action.pace:plan.pace=action.pace
            if action.max_walking_load:plan.max_walking_load=action.max_walking_load
    plan.keep_place_ids=list(dict.fromkeys(plan.keep_place_ids));plan.remove_place_ids=[pid for pid in dict.fromkeys(plan.remove_place_ids) if pid not in plan.keep_place_ids]
    return plan


def _remove_from_allocation(state,day_no,place_id):
    allocated=next(item for item in state['allocation']['days'] if item['day']==day_no)
    for key in ('preferred_candidate_ids','backup_candidate_ids'):
        allocated[key]=[value for value in allocated.get(key,[]) if value!=place_id]
    owner=state['allocation'].get('place_assignments',{}).get(place_id)
    if owner and owner.get('day')==day_no:state['allocation']['place_assignments'].pop(place_id,None)
    state['edits'][day_no]['stop_overrides'].pop(place_id,None)
    state['edits'][day_no]['segment_overrides']={key:value for key,value in state['edits'][day_no]['segment_overrides'].items() if place_id not in key.split('->')}


def _move_place_to_day(state,place_id,source_day,target_day):
    _remove_from_allocation(state,source_day,place_id)
    target=next(item for item in state['allocation']['days'] if item['day']==target_day)
    if place_id not in target['preferred_candidate_ids']:target['preferred_candidate_ids'].append(place_id)
    assign_place(state['allocation'],place_id,target_day,'preferred')
    state['edits'][target_day]['stop_overrides'].setdefault(place_id,{}).update(
        stay_minutes=90,locked=False,must_keep=False,force_include=True,note='由跨天重规划重新分配',priority=4,transport_to_next=None)
    state['sources'][target_day]=None


def _redistribute_places(state,source_day,place_ids,preferred_days):
    places={place['id']:place for place in collect_places(state['packs'])};moved={};unplaced=[]
    for place_id in place_ids:
        place=places.get(place_id);candidates=[]
        if not place:unplaced.append(place_id);continue
        pace=PACE_CONFIG[state['trip'].preferences.pace]
        for number in preferred_days or range(1,state['trip'].days+1):
            if number==source_day:continue
            day=state['itinerary'][number-1]
            if day.get('city')!=place.get('city'):continue
            major=sum(1 for stop in day.get('stops',[]) if places.get(stop['place_id'],{}).get('type')!='restaurant')
            locked=sum(1 for value in state['edits'][number]['stop_overrides'].values() if value.get('locked'))
            density=day.get('density_evaluation') or {};available=int(density.get('available_minutes',660));scheduled=int(density.get('scheduled_minutes',major*120))
            required=pace.sight_minutes+pace.route_buffer_minutes+30
            if major>=pace.max_major_stops or available-scheduled<required:continue
            # Existing utilization, locked content and remaining capacity form the deterministic capacity score.
            candidates.append((major+locked*.25-(available-scheduled)/1000,number))
        if not candidates:unplaced.append(place_id);_remove_from_allocation(state,source_day,place_id);continue
        target=min(candidates)[1];_move_place_to_day(state,place_id,source_day,target);moved[place_id]=target
    return moved,unplaced


def _normalized_place_name(value):
    return re.sub(r'[\s·•（）()\-—_]+','',str(value or '')).lower()


def _supporting_activity(place,stop=None):
    return bool(place and (place.get('type')=='restaurant' or place.get('place_type') in {'restaurant','hotel','transport'}
                           or place.get('user_created') or (stop or {}).get('meal_role')))


async def _resolve_dedicated_place(state,connect,day_no,parsed,places):
    query=parsed.dedicated_place_query
    if parsed.dedicated_place_id:
        match=next((place for place in places if place['id']==parsed.dedicated_place_id),None)
        if match:return match,{'query':match.get('display_name'),'resolution_status':'current_catalog_id','selected_place_id':match['id']}
    if not query:raise ReplanError('REPLAN_PLACE_NOT_FOUND','没有识别出需要单独安排的地点',422)
    city=state['itinerary'][day_no-1]['city'];needle=_normalized_place_name(query)
    matches=[place for place in places if place.get('city')==city and (needle==_normalized_place_name(place.get('display_name'))
             or needle==_normalized_place_name(place.get('local_name')) or needle in _normalized_place_name(place.get('display_name')))]
    if len(matches)==1:
        place=matches[0];return place,{'query':query,'resolution_status':'canonical_match','selected_place_id':place['id']}
    if len(matches)>1:raise ReplanError('REPLAN_PLACE_AMBIGUOUS',f'“{query}”匹配到多个地点，请写明具体名称或分店',422)
    try:client=AmapClient(connect=connect)
    except AmapConfigurationError as exc:raise ReplanError('REPLAN_PROVIDER_UNAVAILABLE','地点核验服务暂时不可用',503) from exc
    try:candidates,_=await search_pois(client,query,city,limit=5)
    except AmapProviderError as exc:raise ReplanError('REPLAN_PROVIDER_UNAVAILABLE','地点核验服务暂时不可用',503) from exc
    seed={'type':'sight','city':city,'local_name':query,'display_name':query,'semantic_tags':['theme_park'] if '迪士尼' in query else []}
    scored=sorted(((score_candidate(seed,item),item) for item in candidates),key=lambda pair:pair[0].score,reverse=True)
    if not scored or scored[0][0].score<.70:raise ReplanError('REPLAN_PLACE_NOT_FOUND',f'没有找到可可靠核验的“{query}”',422)
    if len(scored)>1 and scored[0][0].score-scored[1][0].score<.04:
        raise ReplanError('REPLAN_PLACE_AMBIGUOUS',f'“{query}”存在多个相近地点，请补充区域或正式名称',422)
    candidate=scored[0][1];place=_place_from_candidate(candidate,city,query);_assign(state,day_no,place)
    trace={'query':query,'resolution_status':'amap_resolved','score':scored[0][0].score,'selected_place_id':place['id'],
           'provider':'amap','provider_place_id':place['provider_place_id'],'coordinates':[place['latitude'],place['longitude']],
           'was_new_place':True,'current_ownership_day':None}
    return place,trace


def _replace_source_anchor(state,source_day,place_id,places):
    plan_day=state['plan']['days'][source_day-1];allocated=next(item for item in state['allocation']['days'] if item['day']==source_day)
    if allocated['primary_anchor_id']!=place_id:return
    candidates=[stop['place_id'] for stop in state['itinerary'][source_day-1]['stops'] if stop['place_id']!=place_id
                and not _supporting_activity(places.get(stop['place_id']),stop)]
    if not candidates:
        candidates=[pid for pid in [*allocated.get('preferred_candidate_ids',[]),*allocated.get('backup_candidate_ids',[])] if pid!=place_id]
    if not candidates:raise ReplanError('REPLAN_NO_FEASIBLE_SCHEDULE','原日期移出该地点后没有可用主活动',422)
    replacement=candidates[0];plan_day['primary_anchor_id']=replacement;allocated['primary_anchor_id']=replacement
    for key in ('preferred_candidate_ids','backup_candidate_ids'):allocated[key]=[pid for pid in allocated.get(key,[]) if pid!=replacement]
    state['allocation']['place_assignments'].pop(replacement,None);assign_place(state['allocation'],replacement,source_day,'primary',fixed=True)


def _promote_dedicated_place(state,current_day,place,places):
    place_id=place['id'];owner=state['allocation'].get('place_assignments',{}).get(place_id);source_day=owner.get('day') if owner else None
    affected={current_day}
    if source_day and source_day!=current_day:
        _replace_source_anchor(state,source_day,place_id,{item['id']:item for item in places});_remove_from_allocation(state,source_day,place_id);affected.add(source_day)
    current=next(item for item in state['allocation']['days'] if item['day']==current_day);old=current['primary_anchor_id']
    if old!=place_id:
        state['allocation']['place_assignments'].pop(old,None)
        for key in ('preferred_candidate_ids','backup_candidate_ids'):current[key]=[pid for pid in current.get(key,[]) if pid!=place_id]
        current['primary_anchor_id']=place_id;state['plan']['days'][current_day-1]['primary_anchor_id']=place_id
    state['allocation']['place_assignments'].pop(place_id,None);assign_place(state['allocation'],place_id,current_day,'primary',fixed=True)
    default_stay=480 if any(word in place.get('display_name','') for word in ('迪士尼','环球影城','乐园')) else 360
    state['edits'][current_day]['stop_overrides'].setdefault(place_id,{}).update(
        stay_minutes=max(default_stay,state['edits'][current_day]['stop_overrides'].get(place_id,{}).get('stay_minutes',0)),
        locked=False,must_keep=True,force_include=True,note='用户指定为当天主要游玩活动',priority=5,transport_to_next=None)
    return sorted(affected),source_day


def _apply_city_day_action(state,action,places,current_day):
    target=action.target_day_id or current_day
    if action.day_delta is not None and action.target_day_id is None:
        raise ReplanError('REPLAN_SCOPE_CONFIRMATION_REQUIRED',f'“{action.city}多安排一天”需要先选择从哪个城市日程调整',409)
    if not 1<=target<=state['trip'].days:raise ReplanError('REPLAN_DAY_CAPACITY_EXCEEDED','目标日期不在行程范围内',422)
    known_cities={day.get('city') for day in state['itinerary']}|set(state['trip'].destinations)
    if action.city not in known_cities:raise ReplanError('REPLAN_CROSS_CITY_CONFLICT',f'“{action.city}”不在本次旅行城市中',422)
    protected=[pid for pid,value in state['edits'][target]['stop_overrides'].items() if value.get('locked')]
    if protected:raise ReplanError('REPLAN_LOCKED_STOP_CONFLICT','目标日期存在已锁定地点，不能直接更换城市',422)
    owned=state['allocation'].get('place_assignments',{})
    available=[place for place in places if place.get('city')==action.city and place.get('verification_status')!='generated'
               and (place['id'] not in owned or owned[place['id']].get('day')==target)]
    sights=[place for place in available if place.get('type')!='restaurant' and place.get('place_type') not in {'restaurant','hotel','transport'}]
    restaurants=[place for place in available if place.get('type')=='restaurant' or place.get('place_type')=='restaurant']
    if not sights:raise ReplanError('REPLAN_NO_FEASIBLE_SCHEDULE',f'“{action.city}”没有可用且未被其他日期占用的真实地点',422)
    allocation_day=next(item for item in state['allocation']['days'] if item['day']==target)
    old_ids=[allocation_day.get('primary_anchor_id'),allocation_day.get('fixed_meal_stop_id'),
             *allocation_day.get('preferred_candidate_ids',[]),*allocation_day.get('backup_candidate_ids',[])]
    for pid in filter(None,old_ids):_remove_from_allocation(state,target,pid)
    anchor=sights[0];preferred=[place['id'] for place in sights[1:4]];backup=[place['id'] for place in sights[4:7]]
    meal=restaurants[0]['id'] if restaurants else None
    allocation_day.update(city=action.city,area_labels=[],intent=f'用户指定 {action.city} 日程',primary_anchor_id=anchor['id'],
                          preferred_candidate_ids=preferred,backup_candidate_ids=backup,fixed_meal_stop_id=meal)
    plan_day=state['plan']['days'][target-1];plan_day.update(city=action.city,area_labels=[],intent=f'用户指定 {action.city} 日程',
        primary_anchor_id=anchor['id'],candidate_place_ids=preferred,backup_candidate_ids=backup,fixed_meal_stop_id=meal)
    for pid,role in [(anchor['id'],'primary'),*[(pid,'preferred') for pid in preferred],*[(pid,'backup') for pid in backup]]:
        state['allocation']['place_assignments'].pop(pid,None);assign_place(state['allocation'],pid,target,role,fixed=role=='primary')
    if meal:
        state['allocation']['place_assignments'].pop(meal,None);assign_place(state['allocation'],meal,target,'meal',fixed=True)
    state['itinerary'][target-1]['city']=action.city;state['sources'][target]=None
    return target


async def instruction_preview(connect,job_id,day_id,request:InstructionRequest):
    day_no=_day_number(day_id,job_id);state=_load(connect,job_id,day_no)
    places=collect_places(state['packs']);parsed_request,parser_meta=await _parse_instruction(
        request.instruction,state['itinerary'][day_no-1],places,state['trip'].days,day_no,request.selected_place_id)
    if request.ui_scope_context=='trip' and parsed_request.scope=='current_day':
        mentioned_cities={day['city'] for day in state['itinerary'] if day.get('city') and day['city'] in request.instruction}
        affected=[index for index,day in enumerate(state['itinerary'],1) if day.get('city') in mentioned_cities]
        parsed_request.scope='affected_days' if affected and len(affected)<state['trip'].days else 'whole_trip'
        parsed_request.affected_day_ids=affected or list(range(1,state['trip'].days+1))
    original_stops=copy.deepcopy(state['itinerary'][day_no-1]['stops']);actual={s['place_id'] for s in original_stops}
    catalog=[{'place_id':s['place_id'],'name':next((p['display_name'] for p in places if p['id']==s['place_id']),s['place_id']),
              'arrival_time':s.get('arrival_time')} for s in original_stops]
    parsed=_compile_execution_plan(parsed_request,catalog,request.selected_place_id)
    dedicated_id=None;resolution_trace=None;ownership_day=None;non_destructive_additions=[]
    if parsed.dedicated_place_id or parsed.dedicated_place_query:
        dedicated,resolution_trace=await _resolve_dedicated_place(state,connect,day_no,parsed,places)
        dedicated_id=dedicated['id'];places=collect_places({**state['packs'],'user-places':{'places':state['user_places']}})
        ownership=state['allocation'].get('place_assignments',{}).get(dedicated_id)
        ownership_day=(resolution_trace.get('current_ownership_day') if 'current_ownership_day' in resolution_trace
                       else (ownership.get('day') if ownership else None))
        resolution_trace.update({'canonical_place_id':dedicated_id,'provider':dedicated.get('provider'),
            'provider_place_id':dedicated.get('provider_place_id'),'coordinates':[dedicated.get('latitude'),dedicated.get('longitude')],
            'current_ownership_day':ownership_day})
        owner_days,_=_promote_dedicated_place(state,day_no,dedicated,places)
        parsed.dedicated_place_id=dedicated_id;parsed.dedicated_place_query=parsed.dedicated_place_query or dedicated.get('display_name')
        for action in parsed_request.actions:
            if isinstance(action,DedicateDayAction):
                action.place_ref=dedicated_id;action.place_query=action.place_query or dedicated.get('display_name')
        parsed.keep_place_ids=list(dict.fromkeys([dedicated_id,*parsed.keep_place_ids]))
        # A dedicated day means one principal attraction. Meals, hotel/transport and protected custom activities remain.
        deterministic_removals=[]
        place_map={item['id']:item for item in places}
        for stop in state['itinerary'][day_no-1]['stops']:
            pid=stop['place_id']
            if pid==dedicated_id or _supporting_activity(place_map.get(pid),stop):continue
            override=state['edits'][day_no]['stop_overrides'].get(pid,{})
            if override.get('locked') or override.get('must_keep'):
                raise ReplanError('REPLAN_LOCKED_STOP_CONFLICT',f'“{place_map.get(pid,{}).get("display_name",pid)}”已锁定，不能从当天移除',422)
            deterministic_removals.append(pid)
        parsed.remove_place_ids=list(dict.fromkeys([*deterministic_removals,*[pid for pid in parsed.remove_place_ids if pid in actual and pid!=dedicated_id]]))
        current_allocation=next(item for item in state['allocation']['days'] if item['day']==day_no)
        for pid in [*current_allocation.get('preferred_candidate_ids',[]),*current_allocation.get('backup_candidate_ids',[])]:
            if pid!=dedicated_id and pid not in actual and not _supporting_activity(place_map.get(pid)):
                _remove_from_allocation(state,day_no,pid)
        if ownership_day and ownership_day!=day_no:
            parsed.scope='affected_days';parsed.affected_day_ids=sorted({day_no,ownership_day,*owner_days})
    names={place['id']:place.get('display_name','') for place in places}
    for query in parsed.avoid_actions:
        matches=[pid for pid in actual if query.query in names.get(pid,'') or names.get(pid,'') in query.query]
        parsed.remove_place_ids.extend(matches)
    for action in parsed.replace_actions:
        old=_match_current_place(action.old_place_ref,catalog,request.selected_place_id)
        if not old:raise ReplanError('REPLAN_PLACE_NOT_FOUND',f'当天没有找到需要替换的“{action.old_place_ref}”',422)
        parsed.remove_place_ids.append(old)
        if action.new_query:parsed.add_actions.append(AddPlaceAction(query=action.new_query))
        elif action.replacement_preference:
            candidates=[pid for pid in next(item for item in state['allocation']['days'] if item['day']==day_no).get('backup_candidate_ids',[])
                        if action.replacement_preference in ' '.join((next((p for p in places if p['id']==pid),{}).get('semantic_tags') or []))]
            if not candidates:raise ReplanError('REPLAN_NO_FEASIBLE_SLOT',f'没有找到符合“{action.replacement_preference}”的可替换地点',422)
            candidate=next(p for p in places if p['id']==candidates[0]);_assign(state,day_no,candidate)
    for action in parsed.move_actions:
        move_ids=([item['place_id'] for item in catalog if (item.get('arrival_time') or '00:00')>='12:00']
                  if action.selector=='current_day_afternoon' else [_match_current_place(action.place_ref,catalog,request.selected_place_id)])
        if not all(move_ids):raise ReplanError('REPLAN_PLACE_NOT_FOUND',f'当天没有找到“{action.place_ref or "所选地点"}”',422)
        for pid in move_ids:
            if action.target_day_id and action.target_day_id!=day_no:
                if not 1<=action.target_day_id<=state['trip'].days:raise ReplanError('REPLAN_DAY_CAPACITY_EXCEEDED','目标日期不在行程范围内',422)
                if state['itinerary'][action.target_day_id-1]['city']!=state['itinerary'][day_no-1]['city']:
                    raise ReplanError('REPLAN_CROSS_CITY_CONFLICT','目标日期与该地点不在同一城市',422)
                override=state['edits'][day_no]['stop_overrides'].get(pid,{})
                if override.get('locked'):raise ReplanError('REPLAN_LOCKED_STOP_CONFLICT',f'“{names.get(pid,pid)}”已锁定，不能跨天移动',422)
                _move_place_to_day(state,pid,day_no,action.target_day_id);parsed.scope='affected_days'
                parsed.affected_day_ids=sorted({day_no,action.target_day_id,*parsed.affected_day_ids})
            else:
                preferred=action.preferred_start_time or {'morning':'09:00','afternoon':'14:00','evening':'18:00','any':None}[action.preferred_period]
                state['edits'][day_no]['stop_overrides'].setdefault(pid,{}).update(preferred_start_time=preferred)
    if parsed.city_actions:
        for action in parsed.city_actions:
            target=_apply_city_day_action(state,action,places,day_no)
            parsed.scope='affected_days';parsed.affected_day_ids=sorted({day_no,target,*parsed.affected_day_ids})
    for action in parsed.reflow_actions:
        target_city=action.city or state['itinerary'][day_no-1]['city']
        days=[index for index,day in enumerate(state['itinerary'],1) if day.get('city')==target_city]
        if not days:raise ReplanError('REPLAN_CROSS_CITY_CONFLICT',f'行程中没有“{target_city}”日程',422)
        parsed.scope='affected_days';parsed.affected_day_ids=sorted(set(days))
    parsed.remove_place_ids=list(dict.fromkeys(parsed.remove_place_ids))
    invalid=[pid for pid in [*parsed.keep_place_ids,*parsed.remove_place_ids] if pid not in actual and pid!=dedicated_id]
    if invalid:raise ReplanError('INVALID_PLACE_REFERENCE','自然语言约束引用了当天不存在的地点')
    allocated=next(d for d in state['allocation']['days'] if d['day']==day_no)
    # "X 一整天" may promote X to anchor; the old anchor can then be redistributed.
    exclusive=bool(not dedicated_id and parsed.keep_place_ids and re.search(r'排一天|一整天|只保留',request.instruction))
    if exclusive and allocated['primary_anchor_id'] not in parsed.keep_place_ids:
        old=allocated['primary_anchor_id'];new=parsed.keep_place_ids[0]
        state['plan']['days'][day_no-1]['primary_anchor_id']=new;allocated['primary_anchor_id']=new
        state['allocation']['place_assignments'].pop(old,None);state['allocation']['place_assignments'].pop(new,None)
        assign_place(state['allocation'],new,day_no,'primary',fixed=True)
        if old not in parsed.remove_place_ids:parsed.remove_place_ids.append(old)
    movable=[]
    for pid in parsed.remove_place_ids:
        override=state['edits'][day_no]['stop_overrides'].get(pid,{})
        if override.get('locked') or override.get('must_keep'):
            raise ReplanError('REPLAN_LOCKED_STOP_CONFLICT',f'“{names.get(pid,pid)}”已锁定或标记为必留，不会被删除',422)
        if pid==allocated.get('fixed_meal_stop_id'):
            continue
        movable.append(pid)
        if not parsed.redistribute_removed_stops:_remove_from_allocation(state,day_no,pid)
    for pid in parsed.keep_place_ids:
        current=next((s for s in state['itinerary'][day_no-1]['stops'] if s['place_id']==pid),None)
        update={'locked':False if pid==dedicated_id else parsed.keep_locks.get(pid,False),'must_keep':True}
        if current and parsed.keep_locks.get(pid,False) and pid!=dedicated_id:update['hard_start_time']=current['arrival_time']
        state['edits'][day_no]['stop_overrides'].setdefault(pid,{}).update(update)
    moved={};unplaced=[]
    if parsed.redistribute_removed_stops:
        targets=[value for value in parsed.affected_day_ids if value!=day_no]
        moved,unplaced=_redistribute_places(state,day_no,movable,targets)
        parsed.affected_day_ids=sorted({day_no,*moved.values(),*targets})
        if movable and not moved:
            source_city=state['itinerary'][day_no-1]['city']
            same_city=[index for index,day in enumerate(state['itinerary'],1) if index!=day_no and day['city']==source_city]
            reasons=['地点位于不同城市'] if not same_city else ['受影响日期没有可用容量','其他日期没有足够时间容纳这些地点']
            raise ReplanError('REPLAN_NO_FEASIBLE_SCHEDULE','；'.join(reasons),422)
    try:client=AmapClient(connect=connect)
    except AmapConfigurationError:client=None
    for query in parsed.add_actions:
        needle=_normalized_place_name(query.query)
        existing=[place for place in places if needle and (needle==_normalized_place_name(place.get('display_name')) or needle in _normalized_place_name(place.get('display_name')))]
        if len(existing)>1:raise ReplanError('REPLAN_PLACE_AMBIGUOUS',f'“{query.query}”匹配到多个地点，请写明区域或分店',422)
        if len(existing)==1:
            place=existing[0];owner=state['allocation'].get('place_assignments',{}).get(place['id'],{}).get('day')
            preferred=query.preferred_start_time or {'morning':'09:00','afternoon':'14:00','evening':'18:00','any':None}[query.preferred_period]
            if owner==day_no or place['id'] in actual:
                state['edits'][day_no]['stop_overrides'].setdefault(place['id'],{}).update(preferred_start_time=preferred,force_include=True)
                continue
            if owner and owner!=day_no:
                if state['itinerary'][owner-1]['city']!=state['itinerary'][day_no-1]['city']:
                    raise ReplanError('REPLAN_CROSS_CITY_CONFLICT',f'“{query.query}”已安排在其他城市日程',422)
                _move_place_to_day(state,place['id'],owner,day_no);parsed.scope='affected_days';parsed.affected_day_ids=sorted({day_no,owner,*parsed.affected_day_ids})
                continue
        if not client:raise ReplanError('REPLAN_PROVIDER_UNAVAILABLE','地点核验服务暂时不可用',503)
        candidates,_=await search_pois(client,query.query,state['itinerary'][day_no-1]['city'],limit=5)
        seed={'type':'sight','city':state['itinerary'][day_no-1]['city'],'local_name':query.query,'display_name':query.query,'semantic_tags':[]}
        scored=sorted(((score_candidate(seed,item),item) for item in candidates),key=lambda pair:pair[0].score,reverse=True)
        if not scored or scored[0][0].score<.70 or (len(scored)>1 and scored[0][0].score-scored[1][0].score<.04):
            code='REPLAN_PLACE_NOT_FOUND' if not scored or scored[0][0].score<.70 else 'REPLAN_PLACE_AMBIGUOUS'
            raise ReplanError(code,f'“{query.query}”没有找到足够明确的高德地点，请补充区域或正式名称',422)
        place=_place_from_candidate(scored[0][1],state['itinerary'][day_no-1]['city'],query.query);_assign(state,day_no,place)
        preferred=query.requested_start_time or query.preferred_start_time or {'morning':'09:00','afternoon':'14:00','evening':'18:00','any':None}[query.preferred_period]
        state['edits'][day_no]['stop_overrides'][place['id']]={'stay_minutes':getattr(query,'stay_minutes',None) or 90,'preferred_start_time':preferred,'locked':False,'must_keep':False,'force_include':True,'note':'由用户自然语言指令新增','priority':4,'transport_to_next':None}
        state['sources'][day_no]=None;non_destructive_additions.append((place,query))
    affected=list(range(1,state['trip'].days+1)) if parsed.scope=='whole_trip' else sorted(set(parsed.affected_day_ids or [day_no]))
    before=_scope_snapshot(_load(connect,job_id,day_no),affected)
    for block in parsed.free_time_actions:
        preferred=block.preferred_start_time or {'morning':'09:00','afternoon':'14:00','evening':'18:00','any':None}[block.preferred_period]
        place=_custom_place(CustomActivityRequest(title=block.title,note=block.note,start_time_preference=preferred,stay_minutes=block.duration_minutes),state['itinerary'][day_no-1]['city']);_assign(state,day_no,place)
        state['edits'][day_no]['stop_overrides'][place['id']]={'stay_minutes':block.duration_minutes,'preferred_start_time':preferred,'locked':False,'must_keep':False,'force_include':True,'note':block.note,'priority':3,'transport_to_next':None};state['sources'][day_no]=None
    for value in parsed.time_actions:
        pid=_match_current_place(value.place_ref,catalog,request.selected_place_id)
        if pid in actual:state['edits'][day_no]['stop_overrides'].setdefault(pid,{}).update(preferred_start_time=value.preferred_start_time,hard_start_time=value.hard_start_time)
    for value in parsed.transport_actions:
        if value.level=='day':
            for number in affected:state['edits'][number]['day_transport']=value.mode
        else:
            origin=_match_current_place(value.from_place_ref,catalog,request.selected_place_id);destination=_match_current_place(value.to_place_ref,catalog)
            if not origin or not destination:raise ReplanError('REPLAN_PLACE_NOT_FOUND','没有找到需要修改交通的完整起止地点',422)
            state['edits'][day_no]['segment_overrides'][f'{origin}->{destination}']=value.mode
    place_map={place['id']:place for place in collect_places({**state['packs'],'user-places':{'places':state['user_places']}})}
    for meal in parsed.meal_actions:
        meal_stops=[stop for stop in state['itinerary'][day_no-1]['stops'] if stop.get('meal_role')==meal.meal_role
                    or (meal.meal_role in {'lunch','dinner'} and place_map.get(stop['place_id'],{}).get('type')=='restaurant')]
        if not meal_stops:raise ReplanError('REPLAN_NO_FEASIBLE_SLOT',f'当天没有可调整的{meal.meal_role}餐饮安排',422)
        stop=meal_stops[0];note=' / '.join([*meal.cuisine_preferences,*[f'避免 {value}' for value in meal.avoid_cuisines]])
        state['edits'][day_no]['stop_overrides'].setdefault(stop['place_id'],{}).update(
            preferred_start_time=meal.preferred_time,note=note or '用户调整餐饮偏好')
    for preference in parsed.activity_actions:
        matching=[]
        for stop in state['itinerary'][day_no-1]['stops']:
            place=place_map.get(stop['place_id'],{});tags=set(place.get('semantic_tags') or [])
            indoor=bool(tags & {'museum','indoor','gallery','shopping_mall','historic_building'})
            if (preference.environment=='indoor' and indoor) or (preference.environment=='outdoor' and not indoor):matching.append(stop['place_id'])
        if not matching:raise ReplanError('REPLAN_NO_FEASIBLE_SLOT',f'当天没有符合{preference.environment}要求的候选活动',422)
        preferred={'morning':'09:00','afternoon':'14:00','evening':'18:00','any':None}[preference.preferred_period]
        if preferred:state['edits'][day_no]['stop_overrides'].setdefault(matching[0],{}).update(preferred_start_time=preferred)
    state['connect']=connect
    only_non_destructive_add=(parsed.scope=='current_day' and bool(non_destructive_additions)
        and not any((parsed.remove_place_ids,parsed.keep_place_ids,parsed.replace_actions,parsed.move_actions,
                     parsed.transport_actions,parsed.time_actions,parsed.meal_actions,parsed.activity_actions,
                     parsed.city_actions,parsed.reflow_actions,parsed.free_time_actions,parsed.avoid_actions,
                     parsed.redistribute_removed_stops,parsed.dedicated_place_id,parsed.dedicated_place_query,
                     parsed.pace,parsed.replan_day)))
    if only_non_destructive_add:
        route_traces=[]
        for place,action in non_destructive_additions:
            _,_,trace=await _insert_place_preserving_schedule(state,job_id,day_no,place,action)
            route_traces.append(trace)
        all_places=collect_places({**state['packs'],'user-places':{'places':state['user_places']}})
        day=state['itinerary'][day_no-1]
        strengths=derive_day_interests(day,all_places,state['trip'].preferences.interests)
        day['derived_interests']=list(strengths);day['day_interest_strength']=strengths
        outline=allocation_outline(state['allocation'],state['plan']['days'][day_no-1])
        errors=validate_day(day,all_places,expected_date=outline['date'],expected_city=outline['city'],
            required_ids=(outline['primary_anchor_id'],outline.get('fixed_meal_stop_id')))
        if errors:raise ReplanError('REPLAN_NO_FEASIBLE_SCHEDULE',json.dumps(errors[:4],ensure_ascii=False),422)
    else:
        for number in affected:await _schedule(state,job_id,number,pace_override=parsed.pace)
    parsed_request.scope=parsed.scope;parsed_request.affected_day_ids=affected
    all_places=collect_places({**state['packs'],'user-places':{'places':state['user_places']}});proposed=_scope_snapshot(state,affected);diff=_scope_diff(before,proposed,all_places)
    payload={'instruction':request.instruction,'parsed':parsed_request.model_dump(mode='json'),'affected_day_ids':affected,
             'ui_scope_context':request.ui_scope_context,'current_day_id':day_id,
             'current_day_stops':[{'place_id':stop['place_id'],'name':names.get(stop['place_id'],stop['place_id'])} for stop in original_stops],
             'parser_model':parser_meta.get('parser_model'),'raw_parser_output':parser_meta.get('raw_parser_output'),
             'normalized_constraints':parser_meta.get('normalized_constraints',parsed_request.model_dump(mode='json')),
             'validation_errors':parser_meta.get('validation_errors',[]),'repair_attempt_count':parser_meta.get('repair_attempt_count',0),
             'changed_place_ids':sorted({*movable,*moved,*([dedicated_id] if dedicated_id else [])}),'moved_place_ids':moved,
             'removed_place_ids':movable,'dedicated_place_resolution':resolution_trace}
    if only_non_destructive_add:payload['route_feasibility_traces']=route_traces
    preview_warnings=[]
    if only_non_destructive_add:
        added_ids={place['id'] for place,_ in non_destructive_additions}
        preview_warnings=[warning for warning in state['itinerary'][day_no-1].get('schedule_warnings',[])
                          if warning.get('place_id') in added_ids and warning.get('code') in
                             {'FACT_NEEDS_RECHECK','ROUTE_ESTIMATED_FOR_INSERTION'}]
    result=_save_preview(connect,job_id,day_no,'instruction_replan',payload,proposed,diff,scope=parsed.scope,
        affected_day_ids=affected,unplaced_place_ids=unplaced,warnings=preview_warnings)
    result['parsed_constraints']=parsed_request.model_dump(mode='json');result['parser_diagnostics']=parser_meta
    if only_non_destructive_add:result['route_feasibility']=route_traces
    if parsed.scope in ('affected_days','whole_trip'):
        cross=parsed.scope=='affected_days'
        return {'requires_scope_confirmation':True,
                'code':'REPLAN_SCOPE_AFFECTED_DAYS' if cross else 'REPLAN_SCOPE_WHOLE_TRIP',
                'user_title':'这项调整会影响其他日期' if cross else '这项调整会影响整趟行程',
                'user_message':('为了满足你的要求，需要将当前日期的部分地点重新安排到其他日期。系统会尽量只调整受影响的日期，其余行程保持不变。'
                                if cross else '你的要求需要重新调整多个日期。已锁定的地点会保留，系统会先生成调整预览，确认后才修改原行程。'),
                'scope':parsed.scope,'affected_day_ids':affected,
                'details':{'preview':result,'unplaced_place_ids':unplaced}}
    return result


async def undo_day(connect,job_id,day_id):
    day_no=_day_number(day_id,job_id)
    with connect() as db:row=db.execute("SELECT * FROM trip_day_revisions WHERE job_id=? AND day_number=? AND status='active' ORDER BY version DESC LIMIT 1",(job_id,day_no)).fetchone()
    if not row:raise ReplanError('NO_REVISION_TO_UNDO','当前日期没有可撤销的编辑',409)
    state=_load(connect,job_id,day_no);target=json.loads(row['before_state_json'])
    if 'days' in target:
        affected=sorted(map(int,target['days']));before=_scope_snapshot(state,affected)
        for number,payload in target['days'].items():state['itinerary'][int(number)-1]=payload
        state['allocation']=target['allocation'];state['user_places']=target['user_places']
        for number,edit in target['edits'].items():state['edits'][int(number)]=edit
        if target.get('plan'):state['plan']=target['plan']
        for number,source in target.get('sources',{}).items():state['sources'][int(number)]=source
        state['edit']=state['edits'][day_no]
    else:
        affected=[day_no];before=_snapshot(state,day_no)
        state['itinerary'][day_no-1]=target['day'];state['allocation']=target['allocation'];state['user_places']=target['user_places'];state['edit']=target['edit'];state['edits'][day_no]=target['edit']
    result=await _commit(connect,job_id,day_no,state,before,'undo',{'revision_id':row['revision_id'],'affected_day_ids':affected},scheduled=False,affected_days=affected)
    with connect() as db:db.execute("UPDATE trip_day_revisions SET status='undone' WHERE job_id=? AND status='active' AND before_state_json=?",(job_id,row['before_state_json']))
    return result
