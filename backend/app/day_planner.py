"""Constrained Day LLM contract and deterministic day scheduler."""
from __future__ import annotations

import copy

from .models import Day, DayDecision


def day_catalog(outline: dict, places: list[dict]) -> dict:
    """Expose only POIs assigned to this day; unrelated city POIs never enter the prompt."""
    by_id={place['id']:place for place in places}
    def compact(place_id):
        place=by_id[place_id]
        return {key:place.get(key) for key in ('id','type','display_name','description','cuisine','experience_type')}
    preferred=outline.get('candidate_place_ids',[])
    backups=outline.get('backup_candidate_ids',[])
    meal=outline.get('fixed_meal_stop_id')
    return {
        'fixed_primary_anchor':compact(outline['primary_anchor_id']),
        'preferred_candidate_places':[compact(pid) for pid in preferred],
        'backup_candidate_places':[compact(pid) for pid in backups],
        'fixed_meal_stop':compact(meal) if meal else None,
        'area_labels':outline.get('area_labels',[]),
    }


def day_decision_schema(outline: dict) -> dict:
    schema=copy.deepcopy(DayDecision.model_json_schema())
    allowed=[*outline.get('candidate_place_ids',[]),*outline.get('backup_candidate_ids',[])]
    field=schema['properties']['optional_stop_order']
    field['items']={'type':'string','enum':allowed} if allowed else {'not':{}}
    field['uniqueItems']=True
    field['maxItems']=min(4,len(allowed))
    schema['additionalProperties']=False
    return schema


def canonicalize_day_decision(data):
    if not isinstance(data,dict): return data
    values=data.get('optional_stop_order')
    if isinstance(values,list): data['optional_stop_order']=list(dict.fromkeys(values))
    return data


def validate_day_decision(data: dict, outline: dict) -> list[dict]:
    allowed=[*outline.get('candidate_place_ids',[]),*outline.get('backup_candidate_ids',[])]
    errors=[]
    for i,place_id in enumerate(data.get('optional_stop_order',[])):
        if place_id not in allowed:
            errors.append({'pointer':f'/optional_stop_order/{i}','code':'DAY_STOP_OUTSIDE_PLAN_POOL',
                'message':'可选停靠点必须来自当天优先或备用候选池。','invalid_value':place_id,
                'allowed_values':allowed,'validator':'day_candidate_pool','depends_on_map_api':False})
    return errors


def _start_minute(trip, index: int) -> int:
    if index==0:
        return {'morning':9*60,'afternoon':13*60,'evening':17*60,'flexible':9*60}[trip.outboundPeriod]
    return 9*60


def schedule_day(decision: dict, outline: dict, places: list[dict], trip, index: int) -> dict:
    """Hydrate authoritative fields and advisory times without any map API dependency."""
    by_id={place['id']:place for place in places}
    preferred=set(outline.get('candidate_place_ids',[])); backups=set(outline.get('backup_candidate_ids',[]))
    requested=list(dict.fromkeys(decision.get('optional_stop_order',[])))
    rank={place_id:i for i,place_id in enumerate(requested)}
    area_labels=outline.get('area_labels',[])
    def area_rank(place_id):
        place=by_id.get(place_id,{})
        text=' '.join(str(place.get(key,'')) for key in ('display_name','description'))
        return next((i for i,label in enumerate(area_labels) if label in text),len(area_labels))
    optional=sorted(requested,key=lambda pid:(area_rank(pid),0 if pid in preferred else 1,
                    0 if by_id.get(pid,{}).get('type')=='sight' else 1,rank[pid]))
    optional=optional[:{'relaxed':2,'balanced':3,'intensive':4}[trip.preferences.pace]]
    anchor=outline['primary_anchor_id']; meal=outline.get('fixed_meal_stop_id')
    ordered=[anchor]
    if optional: ordered.append(optional.pop(0))
    if meal: ordered.append(meal)
    ordered.extend(optional)
    ordered=list(dict.fromkeys(ordered))[:6]

    notes=decision.get('practical_notes') or ['按现场开放、体力和天气灵活调整。']
    minute=_start_minute(trip,index); stops=[]
    for position,place_id in enumerate(ordered):
        place=by_id[place_id]; is_meal=place.get('type')=='restaurant'
        dwell=60 if is_meal else 90
        note=notes[position%len(notes)]
        if len(note)<18: note=f'{place["display_name"]}：{note}，开放与接待条件出发前请复核。'
        stops.append({'place_id':place_id,'arrival_time':f'{minute//60:02d}:{minute%60:02d}',
            'dwell_minutes':dwell,'transport_mode':'步行或公共交通，具体路线出发前请复核',
            'transfer_minutes':None,'distance_km':None,'estimated_cost':None,
            'cost_note':'费用与适用条件尚未核实，出发前请复核。','practical_note':note,
            'time_guard':'如现场不可进入或时间不足，优先保留主锚点并缩短可选停靠。'})
        minute += dwell+30

    names=[by_id[pid]['display_name'] for pid in ordered]
    intent=decision['day_intent']; route='、'.join(names)
    result={'date':outline['date'],'city':outline['city'],'theme':intent,
        'summary':f'围绕{intent}安排当天节奏，依次考虑{route}；现场开放、用餐与交通条件出发前请复核。',
        'periods':{
            'morning':{'title':'上午安排','description':f'以主锚点为核心开始当天行程，根据抵达时间和现场情况调整停留。'},
            'afternoon':{'title':'午后安排','description':'固定用餐后衔接同日候选地点，优先减少折返并保留休息缓冲。'},
            'evening':{'title':'晚间安排','description':'根据体力与返程要求灵活收尾，不额外承诺未经核实的夜间开放。'}},
        'stops':stops,
        'photo_advice':[{'title':'光线与取景','note':'根据现场光线与人流选择安全位置，遵守场所拍摄规定。'},
                        {'title':'设备与礼仪','note':'轻装拍摄并避免影响通行，室内是否允许摄影请现场确认。'}]}
    return Day.model_validate(result).model_dump(mode='json')
