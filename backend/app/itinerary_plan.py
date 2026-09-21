"""Candidate-constrained itinerary skeleton helpers."""
from __future__ import annotations

import copy
from datetime import timedelta

from .models import ItineraryPlanDecision
from .interests import evaluate_trip_preferences, normalize_interests, place_interest_score

CLOSED_MARKERS = ('永久关闭','停止开放','暂停营业','已停业')
def anchor_eligible(place: dict, city: str) -> bool:
    text=' '.join(str(place.get(k,'')) for k in ('display_name','description','hours_note'))
    return (place.get('city')==city and place.get('type')=='sight'
            and place.get('knowledge_status') in ('model_knowledge','offline_reference')
            and bool(place.get('map_url')) and not any(marker in text for marker in CLOSED_MARKERS))


def build_day_pools(trip, places: list[dict], city_for_day) -> list[dict]:
    pools=[]
    interest_ids=normalize_interests(trip.preferences.interests)
    for i in range(trip.days):
        city=city_for_day(trip,i)
        anchors=[p['id'] for p in places if anchor_eligible(p,city)]
        candidate_places=[p for p in places if p.get('city')==city and p.get('type') in ('sight','experience')
                    and p.get('knowledge_status') in ('model_knowledge','offline_reference')
                    and not (p.get('entity_kind')=='experience_concept' and not p.get('linked_place_id'))]
        candidate_places.sort(key=lambda p:(-place_interest_score(p,interest_ids),p['id']))
        candidates=[p['id'] for p in candidate_places]
        restaurants=[p['id'] for p in places if p.get('city')==city and p.get('type')=='restaurant']
        pools.append({'day':i+1,'date':(trip.startDate+timedelta(days=i)).isoformat(),'city':city,
                      'allowed_primary_anchor_ids':anchors,'allowed_candidate_place_ids':candidates,
                      'meal_place_id':restaurants[i%len(restaurants)] if restaurants else None})
    return pools


def decision_schema(pools: list[dict], interests: list[str]) -> dict:
    schema=ItineraryPlanDecision.model_json_schema(); template=schema['$defs']['PlanDecisionDay']
    prefix=[]
    for pool in pools:
        item=copy.deepcopy(template); props=item['properties']
        props['primary_anchor_id']['enum']=pool['allowed_primary_anchor_ids']
        props['candidate_place_ids']['items']={'type':'string','enum':pool['allowed_candidate_place_ids']}
        props['candidate_place_ids']['uniqueItems']=True
        props['backup_candidate_ids']['items']={'type':'string','enum':pool['allowed_candidate_place_ids']}
        props['backup_candidate_ids']['uniqueItems']=True
        prefix.append(item)
    days=schema['properties']['days']; days.pop('items',None); days['prefixItems']=prefix; days['items']=False
    days['minItems']=days['maxItems']=len(pools)
    return schema


def canonicalize_decision(data):
    if not isinstance(data,dict) or not isinstance(data.get('days'),list): return data
    for day in data['days']:
        if not isinstance(day,dict): continue
        # Historical/model declarations are advisory and never authoritative.
        day.pop('covered_interests',None)
        for key in ('candidate_place_ids','backup_candidate_ids','covered_interests'):
            values=day.get(key)
            if isinstance(values,list): day[key]=list(dict.fromkeys(values))
    return data


def normalize_day_candidate_pools(data):
    """Apply role priority without inventing or replacing any POI."""
    if not isinstance(data,dict) or not isinstance(data.get('days'),list): return data
    for day in data['days']:
        if not isinstance(day,dict): continue
        primary=day.get('primary_anchor_id')
        preferred=[]; preferred_seen=set()
        for place_id in day.get('candidate_place_ids',[]):
            if place_id!=primary and place_id not in preferred_seen:
                preferred.append(place_id); preferred_seen.add(place_id)
        backups=[]; backup_seen=set()
        for place_id in day.get('backup_candidate_ids',[]):
            if place_id!=primary and place_id not in preferred_seen and place_id not in backup_seen:
                backups.append(place_id); backup_seen.add(place_id)
        day['candidate_place_ids']=preferred
        day['backup_candidate_ids']=backups
    return data


def hydrate_decision(data: dict, pools: list[dict]) -> dict:
    return {'days':[{'day':pool['day'],'date':pool['date'],'city':pool['city'],
                     'fixed_meal_stop_id':pool.get('meal_place_id'),**decision}
                    for pool,decision in zip(pools,data['days'])]}


def validate_decision(data: dict, pools: list[dict], places: list[dict], interests: list[str], must_go='', avoid=''):
    errors=[]; warnings=[]; by_id={p['id']:p for p in places}; used=set()
    def fail(pointer,code,message,**extra): errors.append({'pointer':pointer,'code':code,'message':message,**extra})
    if len(data.get('days',[]))!=len(pools):
        fail('/days','DAY_COUNT',f'必须正好生成{len(pools)}天骨架'); return errors,warnings
    for i,(day,pool) in enumerate(zip(data['days'],pools)):
        anchor=day.get('primary_anchor_id'); allowed=pool['allowed_primary_anchor_ids']
        if anchor not in allowed:
            fail(f'/days/{i}/primary_anchor_id','INVALID_PRIMARY_ANCHOR','主锚点必须从当天合法候选池选择',invalid_value=anchor,allowed_values=allowed)
        elif anchor in used:
            fail(f'/days/{i}/primary_anchor_id','DUPLICATE_PRIMARY_ANCHOR','主锚点不可跨日重复',invalid_value=anchor,allowed_values=[x for x in allowed if x not in used])
        used.add(anchor)
        candidates=day.get('candidate_place_ids',[])
        backups=day.get('backup_candidate_ids',[])
        for field,values in (('candidate_place_ids',candidates),('backup_candidate_ids',backups)):
          for j,pid in enumerate(values):
            if pid not in pool['allowed_candidate_place_ids']:
                fail(f'/days/{i}/{field}/{j}','INVALID_CANDIDATE','候选地点必须来自当天城市的合法地点池',invalid_value=pid)
        overlap=({anchor}&set(candidates+backups))|(set(candidates)&set(backups))
        if overlap:
            fail(f'/days/{i}','OVERLAPPING_DAY_POOLS','主锚点、优先候选和备用候选必须互不重复',invalid_value=sorted(overlap))
        evidence=[by_id[x] for x in [anchor,*candidates,*backups,pool.get('meal_place_id')] if x in by_id]
        selected_text=' '.join((p.get('display_name','')+' '+p.get('description','')) for p in evidence)
        if avoid and avoid in selected_text: fail(f'/days/{i}','AVOIDED_CONTENT','当天候选包含用户明确排除的内容')
    assigned={d.get('primary_anchor_id') for d in data['days']}|{
        x for d in data['days'] for x in [*d.get('candidate_place_ids',[]),*d.get('backup_candidate_ids',[])]}
    matched=[p['id'] for p in places if must_go and (must_go in p['display_name'] or p['display_name'] in must_go)]
    if matched and not assigned.intersection(matched): fail('/days','MUST_GO_MISSING','用户必去地点必须进入全程骨架')
    return errors,warnings


def validate_actual_interest_coverage(itinerary: list[dict], plan: dict, places: list[dict], interests: list[str]):
    """Soft evaluation only. LLM declarations are ignored; actual stops are authoritative."""
    evaluation=evaluate_trip_preferences(itinerary,places,interests)
    return [],evaluation['warnings']
