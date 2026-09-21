"""Trip-level POI ownership for resumable itinerary generation."""
from __future__ import annotations

import copy
import re
from .interests import normalize_interests, place_interest_score

CLOSED_MARKERS=('永久关闭','停止开放','暂停营业','已停业')
OWNED_TYPES={'sight','experience'}
HARD_ROLES={'primary','preferred','selected'}


class AllocationError(ValueError):
    def __init__(self, errors):
        self.errors=errors
        super().__init__('POI allocation failed')


def build_canonical_place_pool(places: list[dict]) -> dict[str,dict]:
    pool={}; errors=[]
    for place in places:
        if place.get('type') not in OWNED_TYPES: continue
        if (place.get('entity_kind')=='experience_concept' and not place.get('linked_place_id')
                and not place.get('user_created')):continue
        place_id=place.get('id')
        if place_id in pool:
            errors.append({'pointer':f'/places/{place_id}','code':'DUPLICATE_CANONICAL_PLACE_ID',
                           'message':'canonical place_id 必须全局唯一','invalid_value':place_id})
        else: pool[place_id]=place
    if errors: raise AllocationError(errors)
    return pool


def rebuild_available_pool(canonical_pool: dict[str,dict], place_ownership: dict[str,dict]) -> set[str]:
    hard={place_id for place_id,owner in place_ownership.items() if owner.get('reservation')=='hard'}
    return set(canonical_pool)-hard


def assign_place(state: dict, place_id: str, day: int, role: str, *, fixed=False) -> bool:
    existing=state['place_assignments'].get(place_id)
    if existing and existing['day']!=day:
        if existing.get('reservation')=='soft' and role in HARD_ROLES:
            del state['place_assignments'][place_id]
        else:
            raise AllocationError([{'pointer':f'/place_assignments/{place_id}','code':'PLACE_ALREADY_OWNED',
                'message':f'地点已归属于第 {existing["day"]} 天','invalid_value':place_id,
                'owner':existing,'requested_owner':{'day':day,'role':role}}])
    reservation='soft' if role=='backup' else 'hard'
    state['place_assignments'][place_id]={'place_id':place_id,'day':day,'role':role,
        'reservation':reservation,'status':'reserved','fixed':bool(fixed)}
    return True


def soft_reserve_place(state: dict, place_id: str, day: int) -> bool:
    existing=state['place_assignments'].get(place_id)
    if existing and existing['day']!=day:return False
    return assign_place(state,place_id,day,'backup')


def release_place(state: dict, place_id: str, *, day: int|None=None) -> bool:
    owner=state['place_assignments'].get(place_id)
    if not owner or (day is not None and owner['day']!=day) or owner.get('fixed'):return False
    del state['place_assignments'][place_id]
    state.setdefault('released_place_ids',[]).append(place_id)
    state['released_place_ids']=list(dict.fromkeys(state['released_place_ids']))
    return True


def release_day_assignments(state: dict, day: int, canonical_pool: dict[str,dict]|None=None) -> list[str]:
    released=[]
    for place_id,owner in list(state['place_assignments'].items()):
        if owner['day']==day and release_place(state,place_id,day=day):released.append(place_id)
    canonical_pool=canonical_pool or state.get('_canonical_pool') or {place_id:{} for place_id in state['canonical_place_ids']}
    state['available_place_ids']=sorted(rebuild_available_pool(canonical_pool,state['place_assignments']))
    return released


def _tokens(day: dict) -> list[str]:
    text=' '.join([*day.get('area_labels',[]),day.get('intent','')])
    return [part for part in re.split(r'[、，, /与和的]+',text) if len(part)>=2]


def _fit(place: dict, day: dict, interest_ids=None, trip_interest_state=None) -> float:
    text=' '.join(str(place.get(key,'')) for key in ('display_name','description','experience_type','cuisine'))
    score=sum(12 for label in day.get('area_labels',[]) if label and label in text)
    score+=sum(3 for token in _tokens(day) if token in text)
    interest_ids=interest_ids or []
    score+=35*place_interest_score(place,interest_ids)
    state=trip_interest_state or {}
    score+=sum(12*float(place.get('interest_affinity',{}).get(interest,0))
               for interest in interest_ids if state.get(interest,{}).get('strength',0)<.4)
    if place.get('type')=='sight':score+=1
    return score


def build_day_eligible_pool(day: dict, canonical_pool: dict[str,dict], available_ids: set[str],
                            place_ownership: dict[str,dict], candidate_ids=None, interests=None) -> list[str]:
    source=list(candidate_ids) if candidate_ids is not None else list(canonical_pool)
    eligible=[]
    for place_id in source:
        place=canonical_pool.get(place_id); owner=place_ownership.get(place_id)
        if not place or place.get('city')!=day.get('city'):continue
        if place_id not in available_ids:continue
        if owner and owner.get('day')!=day.get('day'):continue
        text=' '.join(str(place.get(key,'')) for key in ('display_name','description','hours_note'))
        if any(marker in text for marker in CLOSED_MARKERS):continue
        eligible.append(place_id)
    order={place_id:i for i,place_id in enumerate(source)}
    interest_ids=normalize_interests(interests or [])
    return sorted(dict.fromkeys(eligible),key=lambda pid:(-_fit(canonical_pool[pid],day,interest_ids),order.get(pid,10**6),pid))


def _allocate_role(state: dict, plan_days: list[dict], canonical: dict[str,dict], role: str,
                   target_days: set[int]|None=None, interest_ids=None):
    field='candidate_place_ids' if role=='preferred' else 'backup_candidate_ids'
    capacities={day['day']:min(4,max(2,len(day.get(field,[])))) for day in plan_days
                if target_days is None or day['day'] in target_days}
    chosen={day:[place_id for place_id,owner in state['place_assignments'].items()
                 if owner['day']==day and owner['role']==role] for day in capacities}
    hard_available=rebuild_available_pool(canonical,state['place_assignments'])
    edges=[]
    for day in plan_days:
        if day['day'] not in capacities:continue
        proposed=day.get(field,[])
        for place_id in canonical:
            place=canonical[place_id]
            if place['city']!=day['city']:continue
            bonus=100 if place_id in proposed else 0
            edges.append((bonus+_fit(place,day,interest_ids,state.get('trip_interest_state')),-day['day'],place_id,day))
    for _,__,place_id,day in sorted(edges,key=lambda item:(-item[0],-item[1],item[2])):
        day_no=day['day']
        if len(chosen[day_no])>=capacities[day_no]:continue
        owner=state['place_assignments'].get(place_id)
        if owner and owner['day']!=day_no:continue
        if role=='preferred':
            if place_id not in hard_available:continue
            try:assign_place(state,place_id,day_no,role)
            except AllocationError:continue
            hard_available.discard(place_id)
        else:
            if place_id not in rebuild_available_pool(canonical,state['place_assignments']):continue
            if not soft_reserve_place(state,place_id,day_no):continue
        chosen[day_no].append(place_id)
    return chosen


def _refresh_interest_state(state,canonical,interest_ids):
    result={interest:{'coverage':0,'strength':0.0} for interest in interest_ids}
    for place_id,owner in state['place_assignments'].items():
        if owner.get('reservation')!='hard':continue
        for interest,value in canonical[place_id].get('interest_affinity',{}).items():
            if interest in result and value>=.3:
                result[interest]['coverage']+=1;result[interest]['strength']=round(result[interest]['strength']+value,2)
    state['trip_interest_state']=result


def allocate_trip(plan: dict, places: list[dict], *, must_go='', interests=None) -> dict:
    canonical=build_canonical_place_pool(places)
    interest_ids=normalize_interests(interests or [])
    state={'canonical_place_ids':sorted(canonical),'place_assignments':{},'days':[],
           'available_place_ids':sorted(canonical),'released_place_ids':[],'_canonical_pool':canonical,
           'selected_interest_ids':interest_ids,'trip_interest_state':{}}
    for day in plan['days']:
        anchor=day['primary_anchor_id']; place=canonical.get(anchor)
        if not place or place['city']!=day['city']:
            raise AllocationError([{'pointer':f'/days/{day["day"]-1}/primary_anchor_id','code':'INVALID_ALLOCATION_PRIMARY',
                                    'message':'主锚点不属于当天 canonical pool','invalid_value':anchor}])
        fixed=bool(must_go and (must_go in place['display_name'] or place['display_name'] in must_go))
        assign_place(state,anchor,day['day'],'primary',fixed=fixed)
    _refresh_interest_state(state,canonical,interest_ids)
    if must_go:
        for place_id,place in canonical.items():
            if not (must_go in place['display_name'] or place['display_name'] in must_go):continue
            if place_id in state['place_assignments']:continue
            candidates=[day for day in plan['days'] if day['city']==place['city']]
            if not candidates:continue
            claimed=[day for day in candidates if place_id in [*day.get('candidate_place_ids',[]),*day.get('backup_candidate_ids',[])]]
            owner=max(claimed or candidates,key=lambda day:_fit(place,day,interest_ids,state['trip_interest_state']))
            assign_place(state,place_id,owner['day'],'preferred',fixed=True)
    _refresh_interest_state(state,canonical,interest_ids)
    preferred=_allocate_role(state,plan['days'],canonical,'preferred',interest_ids=interest_ids)
    _refresh_interest_state(state,canonical,interest_ids)
    backups=_allocate_role(state,plan['days'],canonical,'backup',interest_ids=interest_ids)
    for day in plan['days']:
        state['days'].append({'day':day['day'],'date':day['date'],'city':day['city'],
            'area_labels':day.get('area_labels',[]),'intent':day.get('intent',''),
            'primary_anchor_id':day['primary_anchor_id'],
            'preferred_candidate_ids':preferred.get(day['day'],[]),
            'backup_candidate_ids':backups.get(day['day'],[]),
            'fixed_meal_stop_id':day.get('fixed_meal_stop_id')})
    state['available_place_ids']=sorted(rebuild_available_pool(canonical,state['place_assignments']))
    _refresh_interest_state(state,canonical,interest_ids)
    del state['_canonical_pool']
    errors=validate_cross_day_ownership(state,places)
    if errors:raise AllocationError(errors)
    return state


def reallocate_days(allocation: dict, plan: dict, places: list[dict], target_days: set[int], *, must_go='', interests=None) -> dict:
    """Release and rebuild only requested days; all other ownership remains untouched."""
    result=copy.deepcopy(allocation); canonical=build_canonical_place_pool(places); result['_canonical_pool']=canonical
    plan_days=[day for day in plan['days'] if day['day'] in target_days]
    for day_no in sorted(target_days):release_day_assignments(result,day_no,canonical)
    for day in plan_days:
        anchor=day['primary_anchor_id']; place=canonical[anchor]
        fixed=bool(must_go and (must_go in place['display_name'] or place['display_name'] in must_go))
        assign_place(result,anchor,day['day'],'primary',fixed=fixed)
    interest_ids=normalize_interests(interests or allocation.get('selected_interest_ids',[]))
    _refresh_interest_state(result,canonical,interest_ids)
    preferred=_allocate_role(result,plan_days,canonical,'preferred',target_days,interest_ids)
    _refresh_interest_state(result,canonical,interest_ids)
    backups=_allocate_role(result,plan_days,canonical,'backup',target_days,interest_ids)
    for day in plan_days:
        replacement={'day':day['day'],'date':day['date'],'city':day['city'],
            'area_labels':day.get('area_labels',[]),'intent':day.get('intent',''),
            'primary_anchor_id':day['primary_anchor_id'],
            'preferred_candidate_ids':preferred.get(day['day'],[]),'backup_candidate_ids':backups.get(day['day'],[]),
            'fixed_meal_stop_id':day.get('fixed_meal_stop_id')}
        result['days']=[replacement if old['day']==day['day'] else old for old in result['days']]
    result['available_place_ids']=sorted(rebuild_available_pool(canonical,result['place_assignments']))
    _refresh_interest_state(result,canonical,interest_ids)
    del result['_canonical_pool']
    errors=validate_cross_day_ownership(result,places)
    if errors:raise AllocationError(errors)
    return result


def allocation_outline(allocation: dict, plan_day: dict) -> dict:
    allocated=next(day for day in allocation['days'] if day['day']==plan_day['day'])
    return {**plan_day,'primary_anchor_id':allocated['primary_anchor_id'],
            'candidate_place_ids':allocated['preferred_candidate_ids'],
            'backup_candidate_ids':allocated['backup_candidate_ids'],
            'fixed_meal_stop_id':allocated.get('fixed_meal_stop_id')}


def finalize_selected(allocation: dict, itinerary: list[dict], places: list[dict]) -> dict:
    result=copy.deepcopy(allocation); owned=build_canonical_place_pool(places)
    for day_no,day in enumerate(itinerary,1):
        for stop in day['stops']:
            place_id=stop['place_id']
            if place_id not in owned:continue
            owner=result['place_assignments'].get(place_id)
            if not owner or owner['day']!=day_no:
                raise AllocationError([{'pointer':f'/itinerary/{day_no-1}/stops','code':'UNOWNED_SELECTED_PLACE',
                    'message':'Day 使用了未归属于自己的地点','invalid_value':place_id}])
            owner['status']='selected'
            if owner['role']=='backup':owner['reservation']='hard'
    result['available_place_ids']=sorted(rebuild_available_pool(owned,result['place_assignments']))
    return result


def validate_cross_day_ownership(allocation: dict, places: list[dict], itinerary=None) -> list[dict]:
    errors=[]; canonical=build_canonical_place_pool(places); seen={}
    for day in allocation.get('days',[]):
        roles={'primary':[day.get('primary_anchor_id')],
               'preferred':day.get('preferred_candidate_ids',[]),'backup':day.get('backup_candidate_ids',[])}
        for role,ids in roles.items():
            for place_id in filter(None,ids):
                if place_id not in canonical:
                    errors.append({'pointer':f'/days/{day["day"]-1}/{role}','code':'NON_CANONICAL_ASSIGNMENT','message':'分配引用不存在的 canonical ID','invalid_value':place_id})
                if place_id in seen and seen[place_id]!=day['day']:
                    errors.append({'pointer':f'/days/{day["day"]-1}/{role}','code':'CROSS_DAY_OWNERSHIP','message':'同一地点不能归属多个 Day','invalid_value':place_id})
                seen[place_id]=day['day']
                owner=allocation.get('place_assignments',{}).get(place_id)
                if not owner or owner['day']!=day['day']:
                    errors.append({'pointer':f'/place_assignments/{place_id}','code':'OWNERSHIP_RECORD_MISMATCH','message':'Day pool 与 ownership 记录不一致','invalid_value':place_id})
    if itinerary:
        used={}
        for day_no,day in enumerate(itinerary,1):
            for stop in day['stops']:
                place_id=stop['place_id']
                if place_id not in canonical:continue
                if place_id in used and used[place_id]!=day_no:
                    errors.append({'pointer':f'/itinerary/{day_no-1}/stops','code':'CROSS_DAY_DUPLICATE','message':'sight / experience 跨日重复','invalid_value':place_id})
                used[place_id]=day_no
    return errors
