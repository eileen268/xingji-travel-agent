"""Deterministic route scheduling with soft day-density evaluation and fill pass."""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime,timezone

from .interests import normalize_interests,place_interest_score
from .models import Day
from .scheduler_config import (FILL_SCORE_WEIGHTS,MAX_FILL_ITERATIONS,MEAL_TIMING_PENALTY_OUTSIDE_ACCEPTABLE,
                               MEAL_WINDOWS,PACE_CONFIG,UNRESOLVED_ROUTE_SCHEDULE_MINUTES,
                               ARRIVAL_LOCAL_TRANSFER_MINUTES,ARRIVAL_CHECKIN_BUFFER_MINUTES,
                               ARRIVAL_ACTIVITY_BUFFER_MINUTES,DEPARTURE_LOCAL_TRANSFER_MINUTES,
                               DEPARTURE_TERMINAL_BUFFER_MINUTES)
from .services.amap.route import AmapRouteService,estimate_route
from .versions import SCHEDULER_VERSION
from .warnings import dedupe_warnings
from .experience_policy import is_schedulable_experience


def _now():return datetime.now(timezone.utc).isoformat()


def _constraint_for(values,day,city):
    return next((value for value in values if str(value.get('date'))==str(day) and value.get('city')==city),None)


def _start_minute(trip,index,trip_constraints=None):
    default=({'morning':9*60,'afternoon':13*60,'evening':17*60,'flexible':9*60}[trip.outboundPeriod]
             if index==0 else 9*60)
    values=(trip_constraints or {}).get('arrival_constraints',[])
    constraint=_constraint_for(values,trip.startDate.fromordinal(trip.startDate.toordinal()+index),None)
    # city is authoritative in the constraint and date uniquely identifies the relevant day.
    constraint=next((value for value in values if str(value.get('date'))==str(trip.startDate.fromordinal(trip.startDate.toordinal()+index))),constraint)
    if not constraint:return default
    hour,minute=map(int,constraint['arrival_time'].split(':'))
    return max(default,hour*60+minute+ARRIVAL_LOCAL_TRANSFER_MINUTES+ARRIVAL_CHECKIN_BUFFER_MINUTES+ARRIVAL_ACTIVITY_BUFFER_MINUTES)


def _end_minute(trip,index,trip_constraints=None):
    default=(20*60 if index!=trip.days-1 else
             {'morning':11*60+30,'afternoon':16*60+30,'evening':20*60,'flexible':19*60}[trip.returnPeriod])
    day=trip.startDate.fromordinal(trip.startDate.toordinal()+index)
    constraint=next((value for value in (trip_constraints or {}).get('departure_constraints',[])
                     if str(value.get('date'))==str(day)),None)
    if not constraint:return default
    hour,minute=map(int,constraint['departure_time'].split(':'))
    return min(default,hour*60+minute-DEPARTURE_LOCAL_TRANSFER_MINUTES-DEPARTURE_TERMINAL_BUFFER_MINUTES)


def _reduced_window(trip,index,trip_constraints=None):
    texts=' '.join(item.get('text','') for key in ('day_specific_constraints','pace_constraints')
                   for item in (trip_constraints or {}).get(key,[]))
    explicit=bool(re.search(r'半天|休息日|自由活动|放空',texts))
    day=trip.startDate.fromordinal(trip.startDate.toordinal()+index)
    constrained=any(str(value.get('date'))==str(day) for key in ('arrival_constraints','departure_constraints')
                    for value in (trip_constraints or {}).get(key,[]))
    return explicit or constrained or (index==0 and trip.outboundPeriod in ('afternoon','evening')) or (index==trip.days-1 and trip.returnPeriod=='morning')


def _straight_distance(a,b):
    if any(p.get(k) is None for p in (a,b) for k in ('latitude','longitude')):return float('inf')
    lat1,lng1,lat2,lng2=map(math.radians,(a['latitude'],a['longitude'],b['latitude'],b['longitude']))
    value=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lng2-lng1)/2)**2
    return 6371000*2*math.atan2(math.sqrt(value),math.sqrt(1-value))


def _nearest_order(start_id,ids,by_id):
    remaining=list(dict.fromkeys(ids));ordered=[];current=start_id
    while remaining:
        index=min(range(len(remaining)),key=lambda i:(_straight_distance(by_id[current],by_id[remaining[i]]),i))
        current=remaining.pop(index);ordered.append(current)
    return ordered


def _mode(trip,origin,destination,override=None):
    explicit={'walking':'walking','public_transit':'transit','transit':'transit','driving':'driving','taxi':'driving'}
    if override and override!='auto':return explicit.get(override,'walking')
    selected=trip.preferences.localTransport;distance=_straight_distance(origin,destination)
    if distance<=1400:return 'walking'
    if 'transit' in selected:return 'transit'
    if 'self_drive' in selected or 'taxi' in selected:return 'driving'
    return 'walking'


def _dwell(place,pace,override=None):
    if override is not None:return max(15,min(480,int(override)))
    return {'restaurant':pace.restaurant_minutes,'chain':pace.restaurant_minutes,
            'experience':pace.experience_minutes}.get(place.get('type'),pace.sight_minutes)


def _clock(minute):
    return f'{minute//60:02d}:{minute%60:02d}'


def _meal_role(trip,index,start,end,constraints,trip_constraints=None):
    explicit=(constraints or {}).get('meal_role')
    if explicit in MEAL_WINDOWS:return explicit
    text=' '.join([*(item.get('text','') for item in (trip_constraints or {}).get('meal_constraints',[])),
                   *map(str,(constraints or {}).get('meal_preferences',[]))])
    if any(token in text for token in ('不安排餐厅','不想专门安排餐厅','不吃正餐')):return 'flexible'
    if any(token in text for token in ('下午茶','咖啡时间')):return 'cafe'
    if start>=16*60 and end>=17*60:return 'dinner'
    if end<=10*60+30:return 'breakfast'
    return 'lunch'


def _meal_window(trip,role,constraints,trip_constraints=None):
    window=MEAL_WINDOWS[role]
    text=' '.join([*(item.get('text','') for item in (trip_constraints or {}).get('meal_constraints',[])),
                   *map(str,(constraints or {}).get('meal_preferences',[]))])
    # An explicit user preference overrides the default clock window. Keep a
    # bounded acceptance band around it so requests such as "14:30 再吃午饭"
    # do not get rejected by the default lunch guardrail.
    match=(re.search(r'(?:午饭|午餐|晚饭|晚餐|早餐).*?([01]?\d|2[0-3])[:：]([0-5]\d)',text) or
           re.search(r'([01]?\d|2[0-3])[:：]([0-5]\d).*?(?:午饭|午餐|晚饭|晚餐|早餐)',text))
    if match:
        minute=int(match.group(1))*60+int(match.group(2))
        from .scheduler_config import MealWindow
        return MealWindow(minute,min(23*60+59,minute+60),
                          max(0,min(window.acceptable_start,minute-30)),
                          min(23*60+59,max(window.acceptable_end,minute+90)))
    return window


def _meal_timing(minute,window):
    if window.preferred_start<=minute<=window.preferred_end:
        return 'preferred',0,'位于首选用餐时段。'
    if window.acceptable_start<=minute<=window.acceptable_end:
        distance=min(abs(minute-window.preferred_start),abs(minute-window.preferred_end))
        return 'acceptable',distance,'超出首选时段，但仍在可接受用餐窗口内。'
    distance=(window.acceptable_start-minute if minute<window.acceptable_start else minute-window.acceptable_end)
    return 'violation',MEAL_TIMING_PENALTY_OUTSIDE_ACCEPTABLE+distance,'超出可接受用餐窗口，需要调整顺序或由用户确认。'


def _meal_stop(stops,meal):
    return next((stop for stop in stops if stop['place_id']==meal),None)


def _meal_schedule_issues(stops,index):
    """Return soft issues for formal meals; never blocks a usable day."""
    formal=[stop for stop in stops if stop.get('meal_role') in ('breakfast','lunch','dinner')]
    issues=[]
    for first,second in zip(formal,formal[1:]):
        first_minute=sum(value*scale for value,scale in zip(map(int,first['arrival_time'].split(':')),(60,1)))
        second_minute=sum(value*scale for value,scale in zip(map(int,second['arrival_time'].split(':')),(60,1)))
        if second_minute-first_minute<150:
            issues.append({'code':'MEALS_TOO_CLOSE','day':index+1,'place_id':second['place_id'],
                'message':f'第 {index+1} 天两次正餐间隔较短，建议确认是否需要同时保留。',
                'metadata':{'category':'schedule_quality','first_role':first['meal_role'],
                            'second_role':second['meal_role'],'gap_minutes':second_minute-first_minute}})
    return issues


def _record_start(connect,job_id,pack_id,index,signature):
    run_id=str(uuid.uuid4())
    if connect:
        with connect() as db:
            db.execute("""INSERT INTO schedule_runs(schedule_run_id,job_id,pack_id,day_number,scheduler_version,
              input_signature,status,warnings,started_at) VALUES(?,?,?,?,?,?,'running','[]',?)""",
              (run_id,job_id,pack_id,index+1,SCHEDULER_VERSION,signature,_now()))
    return run_id


def _record_finish(connect,run_id,status,warnings,payload=None):
    if connect:
        with connect() as db:
            db.execute("""UPDATE schedule_runs SET status=?,warnings=?,payload=?,finished_at=? WHERE schedule_run_id=?""",
              (status,json.dumps(warnings,ensure_ascii=False),json.dumps(payload,ensure_ascii=False) if payload else None,_now(),run_id))


def _density(stops,segments,by_id,start,end,pace,fill_attempts,added,remaining,reduced,issues=()):
    route=sum(segment.get('duration_minutes') or 0 for segment in segments)
    fallback=sum(segment.get('fallback_schedule_minutes') or 0 for segment in segments)
    visit=sum(stop['dwell_minutes'] for stop in stops if by_id[stop['place_id']].get('type') not in ('restaurant','chain'))
    meal=sum(stop['dwell_minutes'] for stop in stops if by_id[stop['place_id']].get('type') in ('restaurant','chain'))
    buffers=[];idle=[]
    for previous,current,segment in zip(stops,stops[1:],segments):
        ph,pm=map(int,previous['arrival_time'].split(':'));ch,cm=map(int,current['arrival_time'].split(':'))
        gap=max(0,ch*60+cm-(ph*60+pm+previous['dwell_minutes']))
        travel=segment.get('duration_minutes') or 0
        fallback_minutes=segment.get('fallback_schedule_minutes') or 0
        configured=segment.get('buffer_minutes')
        if configured is None:configured=min(max(pace.route_buffer_minutes,math.ceil((travel or fallback_minutes)*.15)),max(0,gap-travel-fallback_minutes))
        buffers.append(configured);idle.append(max(0,gap-travel-fallback_minutes-configured))
    if stops:
        lh,lm=map(int,stops[-1]['arrival_time'].split(':'));idle.append(max(0,end-(lh*60+lm+stops[-1]['dwell_minutes'])))
    else:idle.append(max(0,end-start))
    available=max(1,end-start);scheduled=visit+meal+route+fallback+sum(buffers)
    return {'available_minutes':available,'scheduled_minutes':scheduled,'visit_minutes':visit,'meal_minutes':meal,
        'total_route_minutes':route,'fallback_schedule_minutes':fallback,'configured_buffer_minutes':sum(buffers),
        'utilization':round(min(1,scheduled/available),3),
        'target_range':{'minimum':pace.target_utilization_min,'maximum':pace.target_utilization_max},
        'stop_count':len(stops),'attraction_count':sum(by_id[s['place_id']].get('type') not in ('restaurant','chain') for s in stops),
        'meal_count':sum(by_id[s['place_id']].get('type') in ('restaurant','chain') for s in stops),
        'idle_gap_minutes':sum(idle),'longest_idle_gap_minutes':max(idle,default=0),
        'fill_attempt_count':fill_attempts,'added_place_ids':added,'remaining_candidate_count':remaining,
        'reduced_window':reduced,'issues':list(dict.fromkeys(issues))}


def _opening_feasible(place):
    text=' '.join(str(place.get(key,'')) for key in ('hours_note','description'))
    return not any(marker in text for marker in ('永久关闭','停止开放','暂停营业','已停业'))


def _trusted_opening_ranges(place):
    fact=(place.get('official_facts') or {}).get('opening_hours') or {}
    if fact.get('status') not in {'verified','provider_verified','conflicting'} or not fact.get('sources'):
        return None
    value=fact.get('value')
    text=((value.get('weekly') or value.get('today') or '') if isinstance(value,dict) else str(value or ''))
    ranges=[]
    for start_hour,start_minute,end_hour,end_minute in re.findall(
            r'([01]?\d|2[0-3])[:：]([0-5]\d)\s*[-~～—至]\s*([01]?\d|2[0-3])[:：]([0-5]\d)',text):
        ranges.append((int(start_hour)*60+int(start_minute),int(end_hour)*60+int(end_minute)))
    return ranges or None


def _open_for_visit(place,arrival,dwell):
    ranges=_trusted_opening_ranges(place)
    if ranges is None:return None
    return any(start<=arrival and arrival+dwell<=end for start,end in ranges)


def _best_insertion(candidate_id,ordered,by_id):
    best=None
    for position in range(1,len(ordered)+1):
        previous=by_id[ordered[position-1]];candidate=by_id[candidate_id]
        before=_straight_distance(previous,candidate)
        if position<len(ordered):
            following=by_id[ordered[position]]
            detour=before+_straight_distance(candidate,following)-_straight_distance(previous,following)
        else:detour=before
        if math.isfinite(detour) and (best is None or detour<best[0]):best=(max(0,detour),position)
    return best


def _fill_candidate(ordered,candidate_ids,by_id,trip,outline,pace,density):
    selected_interests=normalize_interests(trip.preferences.interests)
    scheduled_places=[by_id[pid] for pid in ordered];current_strength={}
    for interest in selected_interests:
        current_strength[interest]=max((p.get('interest_affinity',{}).get(interest,0) for p in scheduled_places),default=0)
    type_counts={kind:sum(p.get('type')==kind for p in scheduled_places) for kind in ('sight','experience')}
    choices=[]
    for source_rank,place_id in enumerate(candidate_ids):
        place=by_id.get(place_id)
        if not place or place.get('city')!=outline['city'] or not _opening_feasible(place):continue
        insertion=_best_insertion(place_id,ordered,by_id)
        if not insertion:continue
        detour,position=insertion
        if detour>pace.max_fill_detour_meters:continue
        interest=place_interest_score(place,selected_interests)
        marginal=max((max(0,place.get('interest_affinity',{}).get(i,0)-current_strength[i]) for i in selected_interests),default=0)
        area_text=' '.join(str(place.get(k,'')) for k in ('district','display_name','description'))
        area=1.0 if any(label and label in area_text for label in outline.get('area_labels',[])) else .25
        signature=1.0 if place.get('type')=='sight' and place.get('verification_status')=='verified' else .4
        added_minutes=_dwell(place,pace)+round(detour/250)+pace.route_buffer_minutes
        gap=max(1,density['longest_idle_gap_minutes']);gap_fit=max(0,1-abs(gap-added_minutes)/gap)
        repetition=1.0 if type_counts.get(place.get('type'),0)>=2 else 0.0;w=FILL_SCORE_WEIGHTS
        score=(w['interest_match']*interest+w['marginal_preference_gain']*marginal+w['area_fit']*area+
               w['signature_bonus']*signature+w['gap_fit']*gap_fit-
               w['detour_penalty']*(detour/max(1,pace.max_fill_detour_meters))-w['repetition_penalty']*repetition)
        choices.append((score,-source_rank,place_id,position,detour))
    return max(choices,default=None)


async def _build_stops(ordered,anchor,meal,by_id,trip,index,pace,notes,route_service,constraints=None,meal_context=None,window=None):
    constraints=constraints or {};overrides=constraints.get('stop_overrides',{});segments=constraints.get('segment_overrides',{})
    warnings=[];minute,end_limit=window or (_start_minute(trip,index),_end_minute(trip,index));stops=[];route_segments=[];dropped=[]
    for position,place_id in enumerate(ordered):
        place=by_id[place_id];override=overrides.get(place_id,{}) or {};route=None;arrival=minute
        planned_dwell=_dwell(place,pace,override.get('stay_minutes'))
        route_minutes=None;fallback_minutes=0;buffer_minutes=0;idle_minutes=0
        mode=None
        if stops:
            previous_id=stops[-1]['place_id'];previous=by_id[previous_id]
            mode_override=segments.get(f'{previous_id}->{place_id}') or constraints.get('day_transport')
            mode=_mode(trip,previous,place,mode_override)
            route=(await route_service.route(previous,place,mode,place.get('city')) if route_service
                   else estimate_route(previous,place,mode,'未配置路线服务，已按坐标距离保守估算。'))
            if (previous_id!=place_id and
                    ((route.duration_seconds is not None and route.duration_seconds<=0) or
                     (route.distance_meters is not None and route.distance_meters<=0))):
                code='INVALID_ROUTE_DURATION' if not route.duration_seconds or route.duration_seconds<=0 else 'INVALID_ROUTE_DISTANCE'
                route=estimate_route(previous,place,mode,f'{code}：路线结果无效，已改用保守估算。',route_error=code)
            if route.verification_status!='verified':warnings.append(route.warning or '部分路线使用距离估算。')
            route_minutes=math.ceil(route.duration_seconds/60) if route.duration_seconds is not None else None
            fallback_minutes=0 if route_minutes is not None else UNRESOLVED_ROUTE_SCHEDULE_MINUTES
            travel_for_schedule=route_minutes if route_minutes is not None else fallback_minutes
            buffer_minutes=max(pace.route_buffer_minutes,math.ceil(travel_for_schedule*.15))
            arrival=minute+travel_for_schedule+buffer_minutes
        if place_id==meal and meal_context:
            arrival=max(arrival,meal_context['window'].preferred_start)
            ranges=_trusted_opening_ranges(place)
            if ranges and not any(opened<=arrival and arrival+planned_dwell<=closed for opened,closed in ranges):
                next_open=next((opened for opened,closed in ranges if opened>=arrival and opened+planned_dwell<=closed),None)
                if next_open is not None:arrival=next_open
        preferred=override.get('preferred_start_time');hard=override.get('hard_start_time')
        def minute_of(value):
            if not value:return None
            hour,minute_value=map(int,str(value).split(':'));return hour*60+minute_value
        preferred_minute=minute_of(preferred);hard_minute=minute_of(hard)
        if preferred_minute is not None:arrival=max(arrival,preferred_minute)
        if hard_minute is not None:
            if arrival>hard_minute:
                if route and route.duration_seconds is None and minute<=hard_minute:
                    warnings.append(f'{place["display_name"]}前一段交通时间待计算；暂时保留用户锁定时间。')
                else:raise ValueError(f'SCHEDULE_INFEASIBLE: {place["display_name"]} 无法在指定硬时间到达')
            arrival=hard_minute
        if stops:
            idle_minutes=max(0,arrival-minute-(route_minutes or 0)-fallback_minutes-buffer_minutes)
        dwell=planned_dwell;required=(place_id in (anchor,meal) or override.get('locked')
            or override.get('must_keep') or override.get('force_include'))
        if arrival+dwell>end_limit and not required:
            dropped.append(place_id);warnings.append({'code':'ALTERNATIVE_NOT_SCHEDULED',
                'message':f'{place["display_name"]}因当天可用时间不足未排入最终日程。','day':index+1,
                'place_id':place_id,'metadata':{'category':'planning_note','actionable':False}});continue
        if arrival+dwell>end_limit:
            dwell=max(30,end_limit-arrival);warnings.append(f'{place["display_name"]}可用时间较紧，已缩短建议停留。')
        note=override.get('note') or notes[position%len(notes)]
        if len(note)<18:note=f'{place["display_name"]}：{note}，开放与接待条件出发前请复核。'
        labels={'walking':'步行','driving':'驾车或出租车','transit':'公共交通'}
        if stops:
            canonical_mode={'transit':'public_transit'}.get(mode,mode)
            route_segments.append({'segment_id':f'{stops[-1]["place_id"]}->{place_id}',
                'from_place_id':stops[-1]['place_id'],'to_place_id':place_id,'mode':canonical_mode,
                'distance_meters':route.distance_meters if route and route.distance_meters is not None else None,
                'duration_minutes':route_minutes,'buffer_minutes':buffer_minutes,
                'fallback_schedule_minutes':fallback_minutes,'idle_minutes':idle_minutes,
                'route_status':route.verification_status if route else 'unresolved',
                'route_provider':route.provider if route else None,'route_queried_at':route.queried_at if route else None,
                'route_input':({'origin_place_id':route.origin_place_id,'destination_place_id':route.destination_place_id,
                    'origin_latitude':route.origin_latitude,'origin_longitude':route.origin_longitude,
                    'destination_latitude':route.destination_latitude,'destination_longitude':route.destination_longitude,
                    'selected_mode':route.mode} if route else None),
                'route_error_code':route.route_error if route else None,'route_http_status':route.http_status if route else None,
                'route_provider_code':route.provider_response_code if route else None})
        meal_status='not_applicable';meal_penalty=0;meal_reason=''
        if place_id==meal and meal_context:
            meal_status,meal_penalty,meal_reason=_meal_timing(arrival,meal_context['window'])
        stops.append({'place_id':place_id,'arrival_time':f'{arrival//60:02d}:{arrival%60:02d}',
            'dwell_minutes':dwell,'transport_mode':labels[route.mode] if route else '当天首站',
            'transfer_minutes':math.ceil(route.duration_seconds/60) if route and route.duration_seconds is not None else None,
            'route_minutes':route_minutes,'buffer_minutes':buffer_minutes,
            'fallback_schedule_minutes':fallback_minutes,'idle_minutes':idle_minutes,
            'distance_km':round(route.distance_meters/1000,2) if route and route.distance_meters is not None else None,
            'estimated_cost':None,'route_status':route.verification_status if route else 'not_applicable',
            'route_provider':route.provider if route else None,'route_queried_at':route.queried_at if route else None,
            'route_input':({'origin_place_id':route.origin_place_id,'destination_place_id':route.destination_place_id,
                'origin_latitude':route.origin_latitude,'origin_longitude':route.origin_longitude,
                'destination_latitude':route.destination_latitude,'destination_longitude':route.destination_longitude,
                'selected_mode':route.mode} if route else None),
            'route_error_code':route.route_error if route else None,
            'route_http_status':route.http_status if route else None,
            'route_provider_code':route.provider_response_code if route else None,
            'cost_note':'费用与适用条件尚未核实，出发前请复核。','practical_note':note,
            'time_guard':'如现场不可进入或时间不足，优先保留主锚点并缩短可选停靠。',
            'preferred_start_time':preferred,'hard_start_time':hard,'locked':bool(override.get('locked')),
            'must_keep':bool(override.get('must_keep')),'user_created':bool(place.get('user_created')),
            'note':str(override.get('note') or ''),'priority':int(override.get('priority',3)),
            'transport_to_next':None,
            'meal_role':meal_context['role'] if place_id==meal and meal_context else None,
            'meal_window_preferred_start':_clock(meal_context['window'].preferred_start) if place_id==meal and meal_context else None,
            'meal_window_preferred_end':_clock(meal_context['window'].preferred_end) if place_id==meal and meal_context else None,
            'meal_window_acceptable_start':_clock(meal_context['window'].acceptable_start) if place_id==meal and meal_context else None,
            'meal_window_acceptable_end':_clock(meal_context['window'].acceptable_end) if place_id==meal and meal_context else None,
            'meal_window_status':meal_status,'meal_timing_penalty':meal_penalty,'meal_timing_reason':meal_reason})
        minute=arrival+dwell
    return stops,route_segments,warnings,dropped


async def schedule_day_with_routes(decision: dict,outline: dict,places: list[dict],trip,index: int,
                                   route_service: AmapRouteService|None=None,connect=None,job_id=None,constraints: dict|None=None,
                                   trip_constraints=None) -> dict:
    constraints=constraints or {}
    if trip_constraints is None:
        from .trip_constraints import compile_trip_constraints
        trip_constraints=compile_trip_constraints(trip)
    trip_constraints=(trip_constraints.model_dump(mode='json') if hasattr(trip_constraints,'model_dump') else trip_constraints)
    by_id={place['id']:place for place in places};pace=PACE_CONFIG[trip.preferences.pace]
    raw_pool=list(dict.fromkeys([*outline.get('candidate_place_ids',[]),*outline.get('backup_candidate_ids',[])]))
    suggested=[pid for pid in raw_pool if pid in by_id and not is_schedulable_experience(by_id[pid])]
    pool=[pid for pid in raw_pool if pid not in suggested]
    requested=[pid for pid in dict.fromkeys(decision.get('optional_stop_order',[])) if pid in pool][:pace.initial_optional_limit]
    forced=[pid for pid,value in constraints.get('stop_overrides',{}).items()
            if pid in pool and (value.get('locked') or value.get('must_keep') or value.get('force_include'))]
    requested=list(dict.fromkeys([*requested,*forced]))
    preferred=set(outline.get('candidate_place_ids',[]));rank={pid:i for i,pid in enumerate(requested)}
    requested.sort(key=lambda pid:(0 if pid in preferred else 1,rank[pid]))
    anchor=outline['primary_anchor_id'];meal=outline.get('fixed_meal_stop_id')
    start=_start_minute(trip,index,trip_constraints);end=_end_minute(trip,index,trip_constraints);reduced=_reduced_window(trip,index,trip_constraints)
    window=(start,end)
    meal_context=None
    if meal:
        role=_meal_role(trip,index,start,end,constraints,trip_constraints)
        meal_context={'role':role,'window':_meal_window(trip,role,constraints,trip_constraints)}
    nonmeal_order=[anchor,*_nearest_order(anchor,requested,by_id)]
    ordered=list(dict.fromkeys(nonmeal_order if not meal else [anchor,meal,*nonmeal_order[1:]]))
    material={'decision':decision,'outline':outline,'pool':pool,'pace':trip.preferences.pace,
              'transport':trip.preferences.localTransport,'constraints':constraints,'scheduler':SCHEDULER_VERSION}
    signature=hashlib.sha256(json.dumps(material,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
    pack_id=f'itinerary-day-{index+1}';run_id=_record_start(connect,job_id,pack_id,index,signature)
    warnings=[];notes=decision.get('practical_notes') or ['按现场开放、体力和天气灵活调整。']
    added=[];fill_attempts=0;issues=[]
    try:
        if meal:
            trials=[]
            for position in range(1,len(nonmeal_order)+1):
                candidate=[*nonmeal_order];candidate.insert(position,meal)
                try:
                    trial=await _build_stops(candidate,anchor,meal,by_id,trip,index,pace,notes,route_service,constraints,meal_context,window)
                except ValueError as exc:
                    # A locked/hard-time stop can make one insertion position
                    # impossible. Other meal positions must still be assessed.
                    if str(exc).startswith('SCHEDULE_INFEASIBLE:'):continue
                    raise
                trial_stops,trial_segments,_,trial_dropped=trial
                meal_item=_meal_stop(trial_stops,meal)
                if not meal_item:continue
                meal_minute=sum(value*scale for value,scale in zip(map(int,meal_item['arrival_time'].split(':')),(60,1)))
                opening_fit=_open_for_visit(by_id[meal],meal_minute,meal_item['dwell_minutes'])
                route_cost=sum(segment.get('duration_minutes') or segment.get('fallback_schedule_minutes') or 0
                               for segment in trial_segments)
                score=meal_item['meal_timing_penalty']*1000+route_cost+len(trial_dropped)*200+(2_000_000 if opening_fit is False else 0)
                trials.append((score,position,trial,opening_fit))
            if not trials:raise ValueError('SCHEDULE_INFEASIBLE: 固定用餐无法放入当天时间窗')
            _,selected_position,(stops,segments,route_warnings,dropped),selected_opening_fit=min(trials,key=lambda value:(value[0],value[1]))
            ordered=[stop['place_id'] for stop in stops]
            meal_item=_meal_stop(stops,meal)
            meal_item['meal_timing_reason']=(meal_item['meal_timing_reason']+
                f' 调度器比较了 {len(trials)} 个插入位置，在兼顾用餐窗口后选择第 {selected_position+1} 个停靠位置。')
            if selected_opening_fit is False:
                issues.append('MEAL_OPENING_HOURS_CONFLICT');warnings.append({'code':'MEAL_OPENING_HOURS_CONFLICT','day':index+1,
                    'place_id':meal,'message':f'第 {index+1} 天固定用餐地点与已核验营业时间存在冲突，建议更换餐厅或调整用餐时间。',
                    'metadata':{'category':'schedule_quality','actual_start':meal_item['arrival_time']}})
        else:
            stops,segments,route_warnings,dropped=await _build_stops(
                ordered,anchor,meal,by_id,trip,index,pace,notes,route_service,constraints,meal_context,window)
        warnings.extend(route_warnings);ordered=[stop['place_id'] for stop in stops]
        candidates=[pid for pid in pool if pid not in ordered and pid not in dropped]
        density=_density(stops,segments,by_id,start,end,pace,0,[],len(candidates),reduced)
        while fill_attempts<MAX_FILL_ITERATIONS:
            underfilled=density['utilization']<pace.target_utilization_min
            long_gap=density['longest_idle_gap_minutes']>pace.max_idle_gap_minutes
            if reduced or density['attraction_count']>=pace.max_major_stops or not candidates or not (underfilled or long_gap):break
            choice=_fill_candidate(ordered,candidates,by_id,trip,outline,pace,density);fill_attempts+=1
            if not choice:break
            _,__,place_id,position,_=choice;candidates.remove(place_id)
            trial_order=[*ordered];trial_order.insert(position,place_id)
            trial,trial_segments,trial_warnings,_=await _build_stops(
                trial_order,anchor,meal,by_id,trip,index,pace,notes,route_service,constraints,meal_context,window)
            if place_id in [stop['place_id'] for stop in trial]:
                current_meal=_meal_stop(stops,meal);trial_meal=_meal_stop(trial,meal)
                meal_safe=(not meal or (trial_meal and trial_meal['meal_window_status']!='violation' and
                    trial_meal['meal_timing_penalty']<=(current_meal['meal_timing_penalty'] if current_meal else 0)+30))
                if meal_safe:
                    ordered=[stop['place_id'] for stop in trial];stops=trial;segments=trial_segments;warnings.extend(trial_warnings);added.append(place_id)
            density=_density(stops,segments,by_id,start,end,pace,fill_attempts,added,len(candidates),reduced)
        meal_item=_meal_stop(stops,meal)
        if meal_item and meal_item['meal_window_status']=='violation':
            issues.append('MEAL_WINDOW_VIOLATION');warnings.append({'code':'MEAL_WINDOW_VIOLATION','day':index+1,
                'place_id':meal,'message':f'第 {index+1} 天{meal_item["meal_role"]}超出可接受用餐时段。',
                'metadata':{'category':'schedule_quality','actual_start':meal_item['arrival_time'],
                            'acceptable_end':meal_item['meal_window_acceptable_end']}})
        if not meal and not reduced and start<=12*60 and end>=13*60:
            issues.append('MEAL_MISSING');warnings.append({'code':'MEAL_MISSING','day':index+1,
                'message':f'第 {index+1} 天没有固定午餐地点，可在现场选择。',
                'metadata':{'category':'schedule_quality','meal_role':'lunch'}})
        for meal_warning in _meal_schedule_issues(stops,index):
            issues.append(meal_warning['code']);warnings.append(meal_warning)
        underfilled=density['utilization']<pace.target_utilization_min
        long_gap=density['longest_idle_gap_minutes']>pace.max_idle_gap_minutes
        if not reduced and (underfilled or long_gap):
            possible=_fill_candidate(ordered,candidates,by_id,trip,outline,pace,density) if candidates else None
            code='UNDERFILLED_DAY' if possible and density['attraction_count']<pace.max_major_stops else 'UNDERFILLED_DAY_NO_GOOD_CANDIDATE'
            issues.append(code)
            warnings.append({'code':code,'day':index+1,
                'message':f'第 {index+1} 天安排偏松，候选池中没有同时满足时间与绕路限制的更优地点。' if code.endswith('NO_GOOD_CANDIDATE') else f'第 {index+1} 天利用率仍低于{trip.preferences.pace}节奏目标。',
                'metadata':{'category':'schedule_quality','actionable':False}})
        if not reduced and long_gap:
            issues.append('LONG_IDLE_GAP');warnings.append({'code':'LONG_IDLE_GAP','day':index+1,
                'message':f'第 {index+1} 天存在较长机动时间，可用于休息或自由活动。',
                'metadata':{'category':'schedule_quality','actionable':False,'planned_free_time':True}})
        candidates=[pid for pid in pool if pid not in [stop['place_id'] for stop in stops]]
        density=_density(stops,segments,by_id,start,end,pace,fill_attempts,added,len(candidates),reduced,issues)
        names=[by_id[stop['place_id']]['display_name'] for stop in stops];intent=decision['day_intent']
        result={'date':outline['date'],'city':outline['city'],'theme':intent,
            'summary':f'围绕{intent}安排当天节奏，依次前往{"、".join(names)}；路线时间来自高德或坐标估算，开放信息出发前请复核。',
            'periods':{'morning':{'title':'上午安排','description':'从主锚点开始，按地点距离和当天时间窗安排。'},
              'afternoon':{'title':'午后安排','description':'结合固定用餐与路线结果衔接候选地点，并保留交通缓冲。'},
              'evening':{'title':'晚间安排','description':'在返程时间窗内收尾，时间不足时优先移除低优先级候选。'}},
            'stops':stops,'segments':segments,'photo_advice':[{'title':'光线与取景','note':'根据现场光线与人流选择安全位置，遵守场所拍摄规定。'},
              {'title':'设备与礼仪','note':'轻装拍摄并避免影响通行，室内是否允许摄影请现场确认。'}],
            'total_travel_minutes':density['total_route_minutes'],'total_visit_minutes':density['visit_minutes']+density['meal_minutes'],
            'schedule_warnings':dedupe_warnings(('scheduler', [w for w in warnings if not isinstance(w,dict) or
                (w.get('metadata') or {}).get('category')!='planning_note'])),
            'planning_notes':dedupe_warnings(('scheduler_planning_notes',[
                *[w for w in warnings if isinstance(w,dict) and (w.get('metadata') or {}).get('category')=='planning_note'],
                *[{'code':'SUGGESTED_EXPERIENCE_NOT_SCHEDULED','message':f'{by_id[pid]["display_name"]}暂无可靠真实提供方，保留为体验灵感。',
                   'day':index+1,'place_id':pid,'metadata':{'category':'planning_note','actionable':False}}
                  for pid in suggested]])),
            'density_evaluation':density}
        payload=Day.model_validate(result).model_dump(mode='json');_record_finish(connect,run_id,'completed',payload['schedule_warnings'],payload)
        return payload
    except BaseException:
        _record_finish(connect,run_id,'failed',warnings);raise
