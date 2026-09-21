"""Canonical natural-language contract for editable replanning.

The LLM emits only this action-based request. Legacy flat fields are accepted by
``normalize_replan_request`` and immediately converted to actions.
"""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid')


Period=Literal['morning','afternoon','evening','any']
Scope=Literal['current_day','affected_days','whole_trip']
TransportMode=Literal['auto','walking','public_transit','driving']


class AddPlaceAction(StrictModel):
    type: Literal['add_place']='add_place'
    query: str
    preferred_period: Period='any'
    preferred_start_time: str|None=None
    requested_start_time: str|None=None
    time_tolerance_minutes: int=Field(default=0,ge=0,le=180)
    time_constraint_strength: Literal['preferred','strong','hard']='preferred'
    preserve_existing_stops: bool=True
    stay_minutes: int|None=Field(default=None,ge=15,le=720)
    must_include: bool=True


class RemovePlaceAction(StrictModel):
    type: Literal['remove_place']='remove_place'
    place_ref: str|None=None
    selector: Literal['current_day_remaining','current_day_afternoon','selected_stop']|None=None


class KeepPlaceAction(StrictModel):
    type: Literal['keep_place']='keep_place'
    place_ref: str
    lock: bool=False


class ReplacePlaceAction(StrictModel):
    type: Literal['replace_place']='replace_place'
    old_place_ref: str
    new_query: str|None=None
    replacement_preference: str|None=None


class MovePlaceAction(StrictModel):
    type: Literal['move_place']='move_place'
    place_ref: str|None=None
    target_day_id: int|None=None
    preferred_period: Period='any'
    preferred_start_time: str|None=None
    selector: Literal['current_day_afternoon','selected_stop']|None=None


class DedicateDayAction(StrictModel):
    type: Literal['dedicate_day']='dedicate_day'
    place_ref: str|None=None
    place_query: str|None=None
    target_day_id: int|None=None
    remove_other_major_stops: bool=True
    redistribute_removed_stops: bool=False


class SetTransportAction(StrictModel):
    type: Literal['set_transport']='set_transport'
    level: Literal['day','segment']='day'
    mode: TransportMode='auto'
    from_place_ref: str|None=None
    to_place_ref: str|None=None


class SetPaceAction(StrictModel):
    type: Literal['set_pace']='set_pace'
    pace: Literal['relaxed','balanced','intensive']|None=None
    max_walking_load: Literal['low','medium','high']|None=None


class SetTimeWindowAction(StrictModel):
    type: Literal['set_time_window']='set_time_window'
    place_ref: str
    preferred_period: Period='any'
    preferred_start_time: str|None=None
    hard_start_time: str|None=None


class AddFreeTimeAction(StrictModel):
    type: Literal['add_free_time']='add_free_time'
    title: str='自由活动'
    preferred_period: Period='any'
    preferred_start_time: str|None=None
    duration_minutes: int=Field(default=60,ge=15,le=480)
    note: str=''


class SetMealPreferenceAction(StrictModel):
    type: Literal['set_meal_preference']='set_meal_preference'
    meal_role: Literal['breakfast','lunch','dinner','snack','cafe','flexible']='lunch'
    cuisine_preferences: list[str]=Field(default_factory=list)
    avoid_cuisines: list[str]=Field(default_factory=list)
    preferred_time: str|None=None


class SetActivityPreferenceAction(StrictModel):
    type: Literal['set_activity_preference']='set_activity_preference'
    preferred_period: Period='any'
    environment: Literal['indoor','outdoor','either']='either'
    weather_condition: str|None=None


class MustIncludeAction(StrictModel):
    type: Literal['must_include']='must_include'
    query: str
    preferred_period: Period='any'


class AvoidAction(StrictModel):
    type: Literal['avoid']='avoid'
    query: str
    match_kind: Literal['place','category','auto']='auto'


class SetCityDayAction(StrictModel):
    type: Literal['set_city_day']='set_city_day'
    city: str
    target_day_id: int|None=None
    day_delta: int|None=None


class ReflowAction(StrictModel):
    type: Literal['reflow']='reflow'
    city: str|None=None


class ReplanDayAction(StrictModel):
    type: Literal['replan_day']='replan_day'


class RedistributeStopsAction(StrictModel):
    type: Literal['redistribute_stops']='redistribute_stops'
    selector: Literal['current_day_remaining','current_day_afternoon']='current_day_remaining'
    preferred_day_ids: list[int]=Field(default_factory=list)


ReplanAction=Annotated[
    AddPlaceAction|RemovePlaceAction|KeepPlaceAction|ReplacePlaceAction|MovePlaceAction|DedicateDayAction|
    SetTransportAction|SetPaceAction|SetTimeWindowAction|AddFreeTimeAction|SetMealPreferenceAction|
    SetActivityPreferenceAction|MustIncludeAction|AvoidAction|SetCityDayAction|ReflowAction|ReplanDayAction|RedistributeStopsAction,
    Field(discriminator='type'),
]


class StructuredReplanRequest(StrictModel):
    scope: Scope='current_day'
    actions: list[ReplanAction]=Field(default_factory=list)
    affected_day_ids: list[int]=Field(default_factory=list)
    intent_summary: str|None=None


CANONICAL_FIELDS={'scope','actions','affected_day_ids','intent_summary'}
LEGACY_FIELDS={
    'keep','remove','keep_place_ids','remove_place_ids','must_include_queries','must_exclude_queries',
    'dedicated_place_id','dedicated_place_query','redistribute_removed_stops','pace','preferred_areas','avoid_areas',
    'transport_preference','transportation_preference','transportation_constraints','max_walking_minutes',
    'time_preferences','time_constraints','meal_preferences','free_time_blocks','notes','budget_constraints',
}


def _period(value):
    value=str(value or '').lower()
    return {'上午':'morning','早上':'morning','下午':'afternoon','晚上':'evening','夜间':'evening'}.get(value,value if value in {'morning','afternoon','evening'} else 'any')


def _mode(value):
    value=str(value or '').lower()
    return {'打车':'driving','出租车':'driving','驾车':'driving','开车':'driving','taxi':'driving','car':'driving',
            '公交':'public_transit','公共交通':'public_transit','地铁':'public_transit','transit':'public_transit',
            '步行':'walking'}.get(value,value if value in {'auto','walking','public_transit','driving'} else 'auto')


def normalize_replan_request(raw,catalog=None):
    """Convert all known historical shapes into the canonical action union."""
    if not isinstance(raw,dict):raise ValueError('parser output must be a JSON object')
    if 'actions' in raw:
        unknown=set(raw)-CANONICAL_FIELDS
        if unknown:raise ValueError(f'unknown canonical fields: {sorted(unknown)}')
        actions=[]
        for item in raw.get('actions') or []:
            if not isinstance(item,dict):raise ValueError('each action must be an object')
            action=dict(item);aliases={'add':'add_place','remove':'remove_place','keep':'keep_place','transport':'set_transport','pace':'set_pace'}
            action['type']=aliases.get(action.get('type'),action.get('type'))
            if action.get('mode') is not None:action['mode']=_mode(action['mode'])
            if action.get('preferred_period') is not None:action['preferred_period']=_period(action['preferred_period'])
            for key in ('cuisine_preferences','avoid_cuisines'):
                if action.get(key) is None:action[key]=[]
            actions.append(action)
        return {'scope':raw.get('scope') or 'current_day','actions':actions,
                'affected_day_ids':raw.get('affected_day_ids') or [],'intent_summary':raw.get('intent_summary')}
    unknown=set(raw)-LEGACY_FIELDS-{'scope','affected_day_ids','intent_summary'}
    if unknown:raise ValueError(f'unknown legacy fields: {sorted(unknown)}')
    actions=[];catalog=catalog or [];by_name={item.get('name'):item.get('place_id') for item in catalog}
    for value in raw.get('keep_place_ids') or raw.get('keep') or []:
        actions.append({'type':'keep_place','place_ref':by_name.get(value,value),'lock':False})
    for value in raw.get('remove_place_ids') or raw.get('remove') or []:
        actions.append({'type':'remove_place','place_ref':by_name.get(value,value)})
    for item in raw.get('must_include_queries') or []:
        item={'query':item} if isinstance(item,str) else item
        actions.append({'type':'add_place','query':item['query'],'preferred_period':_period(item.get('preferred_period'))})
    for item in raw.get('must_exclude_queries') or []:
        item={'query':item} if isinstance(item,str) else item;actions.append({'type':'avoid','query':item['query']})
    dedicated_id=raw.get('dedicated_place_id');dedicated_query=raw.get('dedicated_place_query')
    if dedicated_id or dedicated_query:
        actions.append({'type':'dedicate_day','place_ref':dedicated_id,'place_query':dedicated_query,
                        'redistribute_removed_stops':bool(raw.get('redistribute_removed_stops'))})
    if raw.get('pace'):actions.append({'type':'set_pace','pace':raw['pace']})
    transport=raw.get('transport_preference') or raw.get('transportation_preference')
    if transport:actions.append({'type':'set_transport','level':'day','mode':_mode(transport)})
    if raw.get('max_walking_minutes') is not None:actions.append({'type':'set_pace','max_walking_load':'low'})
    for item in raw.get('time_preferences') or []:
        actions.append({'type':'set_time_window','place_ref':item.get('place_id') or item.get('place_ref'),
                        'preferred_start_time':item.get('preferred_start_time'),'hard_start_time':item.get('hard_start_time')})
    for item in raw.get('free_time_blocks') or []:
        actions.append({'type':'add_free_time','title':item.get('title','自由活动'),'preferred_start_time':item.get('preferred_start_time'),
                        'duration_minutes':item.get('stay_minutes',60),'note':item.get('note','')})
    for item in raw.get('meal_preferences') or []:
        actions.append({'type':'set_meal_preference','cuisine_preferences':[str(item)]})
    for area in raw.get('preferred_areas') or []:actions.append({'type':'set_activity_preference','environment':'either','preferred_period':'any'})
    summary=raw.get('intent_summary')
    legacy_notes=[str(item) for item in raw.get('notes') or []]
    for key in ('time_constraints','transportation_constraints','budget_constraints'):
        if raw.get(key):legacy_notes.append(f'{key}:{raw[key]}')
    if legacy_notes:summary='；'.join(filter(None,[summary,*legacy_notes]))
    return {'scope':raw.get('scope') or 'current_day','actions':actions,
            'affected_day_ids':raw.get('affected_day_ids') or [],'intent_summary':summary}


def _time(text):
    match=re.search(r'(?:(上午|早上|中午|下午|晚上|夜里))?\s*(\d{1,2})(?::(\d{2})|点(?:(\d{1,2})分)?)',text)
    if not match:return None
    period,hour,minute,cnminute=match.groups();hour=int(hour);minute=int(minute or cnminute or 0)
    if period in ('下午','晚上','夜里') and hour<12:hour+=12
    if period=='中午' and hour<11:hour+=12
    return f'{hour:02d}:{minute:02d}' if hour<24 and minute<60 else None


def _preferred_period(text):
    if re.search(r'上午|早上',text):return 'morning'
    if '下午' in text:return 'afternoon'
    if re.search(r'晚上|夜里|夜间',text):return 'evening'
    return 'any'


def _catalog_ref(fragment,catalog):
    fragment=str(fragment or '').strip(' ，。；把将这个那个')
    matches=[item for item in catalog if item.get('name') and (item['name'] in fragment or fragment in item['name'])]
    return matches[0]['place_id'] if len(matches)==1 else fragment


def parse_replan_deterministic(instruction,catalog,current_day,total_days,selected_place_id=None):
    """High-confidence Chinese patterns; ambiguous remainder is left for the LLM."""
    actions=[];text=instruction.strip();period=_preferred_period(text);clock=_time(text)
    if re.search(r'(?:今天|这一天).*(?:重新排|重排)|(?:重新排|重排)(?:一下)?(?:今天|这一天)?',text):
        actions.append({'type':'replan_day'})
    # Dedicated-day expressions.
    dedicated=(re.search(r'(.{2,30}?)(?:单独)?(?:安排|排|玩)(?:一整天|一天)',text)
               or re.search(r'(.{2,30}?)(?:安排)?(?:一整天|一天)(?=[，。；]|$)',text)
               or re.search(r'(?:今天|这一天)?只(?:安排|去|保留)(.{2,30}?)(?:[，。；]|$)',text))
    if dedicated:
        raw=re.sub(r'^(?:把|将|今天|这一天)','',dedicated.group(1)).strip(' ，。；')
        ref=_catalog_ref(raw,catalog);known=any(item['place_id']==ref for item in catalog)
        redistribute=bool(re.search(r'放(?:到)?|挪(?:到)?|移(?:到)?|安排到',text) and re.search(r'其他天|别天|其他日期|明天|第.+天',text))
        actions.append({'type':'dedicate_day','place_ref':ref if known else None,'place_query':None if known else raw,
                        'target_day_id':current_day,'remove_other_major_stops':True,'redistribute_removed_stops':redistribute})
    # Replace must precede generic add/remove.
    replace=re.search(r'(?:把|将)?(.{2,24}?)(?:换成|替换成|改成)(.{2,24}?)(?:[，。；]|$)',text)
    if replace:actions.append({'type':'replace_place','old_place_ref':_catalog_ref(replace.group(1),catalog),'new_query':replace.group(2).strip()})
    # Cross-day and same-day moves.
    relative_day=re.search(r'今天下午(?:的)?(?:两个|几个)?(?:地方|景点|活动)(?:挪到|移到|放到)(?:第)?([一二三四五六七八九十\d]+)天',text)
    if relative_day:
        cn={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10};token=relative_day.group(1)
        actions.append({'type':'move_place','selector':'current_day_afternoon','target_day_id':cn.get(token,int(token) if token.isdigit() else current_day)})
    clauses=re.split(r'[，。；,;]',text)
    for item in catalog:
        name=item.get('name','');short=re.sub(r'^(?:上海|北京|杭州|苏州)','',name)
        clause=next((value for value in clauses if name and (name in value or (len(short)>=2 and short in value))),None)
        if clause:
            day_match=re.search(r'(?:挪到|移到|放到)(?:第)?([一二三四五六七八九十\d]+)天',clause)
            if day_match:
                cn={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10};token=day_match.group(1)
                actions.append({'type':'move_place','place_ref':item['place_id'],'target_day_id':cn.get(token,int(token) if token.isdigit() else current_day)})
            elif re.search(r'(?:挪到|移到|放到)明天',clause):
                actions.append({'type':'move_place','place_ref':item['place_id'],'target_day_id':min(total_days,current_day+1)})
            elif re.search(r'放上午|安排上午|放下午|安排下午|安排晚上|改到|上午|下午|晚上',clause):
                actions.append({'type':'move_place','place_ref':item['place_id'],'preferred_period':_preferred_period(clause),'preferred_start_time':_time(clause)})
    # Explicit keep/remove/lock.
    for item in catalog:
        name=item.get('name','');short=re.sub(r'^(?:上海|北京|杭州|苏州)','',name)
        clause=next((value for value in clauses if name and (name in value or (len(short)>=2 and short in value))),None)
        if not clause:continue
        if re.search(r'保留|别动|锁住|锁定',clause):
            actions.append({'type':'keep_place','place_ref':item['place_id'],'lock':bool(re.search(r'锁住|锁定|别动',text))})
        if re.search(r'删除|删掉|删|不去',clause) and not dedicated:
            actions.append({'type':'remove_place','place_ref':item['place_id']})
    if re.search(r'其余|剩余|其他(?:景点|地点|行程|都)?',text) and re.search(r'删除|删掉|不安排',text):
        actions.append({'type':'remove_place','selector':'current_day_remaining'})
    if not dedicated and re.search(r'这些|其余|剩余|其他',text) and re.search(r'放(?:到)?|挪(?:到)?|移(?:到)?',text) and re.search(r'其他天|别天|其他日期',text):
        actions.append({'type':'redistribute_stops','selector':'current_day_remaining'})
    if selected_place_id and re.search(r'这个|该景点|该地点',text) and re.search(r'锁住|锁定|别动',text):
        actions.append({'type':'keep_place','place_ref':selected_place_id,'lock':True})
    # Add explicit named place, excluding negative and move/replace expressions.
    add=re.search(r'(?:(?:晚上|下午|上午|午饭后|\d{1,2}(?::\d{2}|点))?\s*(?:加入|加上|添加|加|想去|去))\s*([^，。；]{2,30})',text)
    city_expression=bool(re.search(r'(?:第)?[一二三四五六七八九十\d]+天(?:改)?去',text))
    if add and not city_expression and not re.search(r'不要去|不想去|挪到|移到|换成',text):
        query=re.sub(r'^(?:一个)','',add.group(1)).strip()
        query=re.sub(r'(?:这个)?(?:行程|活动)$','',query).strip()
        approximate=bool(re.search(r'大概|大约|约|左右',text))
        actions.append({'type':'add_place','query':query,'preferred_period':period,
                        'preferred_start_time':clock,'requested_start_time':clock,
                        'time_tolerance_minutes':30 if approximate else 0,
                        'time_constraint_strength':'strong' if clock and approximate else ('hard' if clock else 'preferred'),
                        'preserve_existing_stops':True})
    # Must include / avoid may be a real place or category query.
    must=re.search(r'([^，。；]{2,20}?)(?:一定要去|一定去)|(?:必须安排|一定要安排)([^，。；]{2,20})',text)
    if must:actions.append({'type':'must_include','query':(must.group(1) or must.group(2)).strip(),'preferred_period':period})
    avoid=re.search(r'(?:不要|不想去)([^，。；]{2,20})',text)
    if avoid:actions.append({'type':'avoid','query':avoid.group(1).strip(),'match_kind':'auto'})
    # Segment transport before day transport.
    segment=re.search(r'(.{2,20}?)(?:到|→)(.{2,20}?)(?:打车|出租车|驾车|开车|步行|坐地铁|公共交通)',text)
    if segment:
        phrase=segment.group(0);mode='driving' if re.search(r'打车|出租车|驾车|开车',phrase) else ('public_transit' if re.search(r'地铁|公交|公共交通',phrase) else 'walking')
        actions.append({'type':'set_transport','level':'segment','from_place_ref':_catalog_ref(segment.group(1),catalog),
                        'to_place_ref':_catalog_ref(segment.group(2),catalog),'mode':mode})
    elif re.search(r'打车|出租车|驾车|开车|不坐公交|坐地铁|公共交通|步行',text):
        mode='driving' if re.search(r'打车|出租车|驾车|开车|不坐公交',text) else ('public_transit' if re.search(r'地铁|公交|公共交通',text) else 'walking')
        actions.append({'type':'set_transport','level':'day','mode':mode})
    if re.search(r'轻松|别排太满',text):actions.append({'type':'set_pace','pace':'relaxed'})
    elif re.search(r'紧凑|充实',text):actions.append({'type':'set_pace','pace':'intensive'})
    if '少走路' in text:actions.append({'type':'set_pace','max_walking_load':'low'})
    free=re.search(r'(上午|下午|晚上|午饭后)?.*?(\d+|一|两)\s*(?:个)?小时(?:自由活动|休息)|(?:自由活动|休息).*?(\d+|一|两)\s*(?:个)?小时',text)
    if free:
        token=free.group(2) or free.group(3);hours={'一':1,'两':2}.get(token,int(token) if token and token.isdigit() else 1)
        actions.append({'type':'add_free_time','preferred_period':period,'duration_minutes':hours*60,'title':'回酒店休息' if '回酒店' in text else ('休息' if '休息' in text else '自由活动')})
    meal_role='dinner' if re.search(r'晚饭|晚餐',text) else ('lunch' if re.search(r'午饭|午餐|中午',text) else None)
    if meal_role:
        cuisine=[];avoid_cuisines=[]
        match=re.search(r'(?:吃|想吃)([^，。；]{2,12})',text)
        if match:cuisine=[match.group(1).strip()]
        no=re.search(r'(?:不要|不吃)([^，。；]{2,12})',text)
        if no:avoid_cuisines=[no.group(1).strip()]
        if cuisine or avoid_cuisines or clock:actions.append({'type':'set_meal_preference','meal_role':meal_role,
            'cuisine_preferences':cuisine,'avoid_cuisines':avoid_cuisines,'preferred_time':clock})
    if '室内' in text or re.search(r'户外|室外',text):
        actions.append({'type':'set_activity_preference','preferred_period':period,
                        'environment':'indoor' if '室内' in text else 'outdoor','weather_condition':'rain' if '下雨' in text else None})
    city_day=re.search(r'(?:第)?([一二三四五六七八九十\d]+)天(?:改)?去([\u4e00-\u9fff]{2,10})',text)
    if city_day:
        cn={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10};token=city_day.group(1)
        actions.append({'type':'set_city_day','target_day_id':cn.get(token,int(token) if token.isdigit() else current_day),'city':city_day.group(2)})
    more_city=re.search(r'([\u4e00-\u9fff]{2,10})多安排一天',text)
    if more_city:actions.append({'type':'set_city_day','city':more_city.group(1),'day_delta':1})
    tomorrow_city=re.search(r'明天.*?去([\u4e00-\u9fff]{2,10})',text)
    if tomorrow_city:actions.append({'type':'set_city_day','city':tomorrow_city.group(1),'target_day_id':min(total_days,current_day+1)})
    city_reflow=re.search(r'(?:重新安排|重排)整个([\u4e00-\u9fff]{2,10})段',text)
    if city_reflow:actions.append({'type':'reflow','city':city_reflow.group(1)})
    scope='whole_trip' if re.search(r'整趟|全程|整个行程|所有天',text) else ('affected_days' if city_reflow else 'current_day')
    affected={current_day}
    for action in actions:
        if action.get('target_day_id') and action['target_day_id']!=current_day:scope='affected_days';affected.add(action['target_day_id'])
        if action['type']=='set_city_day':scope='affected_days'
        if action['type']=='dedicate_day' and action.get('redistribute_removed_stops'):scope='affected_days'
    if scope=='whole_trip':affected=set(range(1,total_days+1))
    return StructuredReplanRequest.model_validate({'scope':scope,'actions':actions,'affected_day_ids':sorted(affected),'intent_summary':instruction})


def resolve_scope(instruction,request,current_day,total_days):
    explicit_whole=bool(re.search(r'整趟|全程|整个行程|所有天|全部行程',instruction))
    action_cross=any(isinstance(action,(ReflowAction,SetCityDayAction,RedistributeStopsAction)) or (isinstance(action,MovePlaceAction) and getattr(action,'target_day_id',None) not in (None,current_day))
                     or (isinstance(action,DedicateDayAction) and action.redistribute_removed_stops) for action in request.actions)
    scope='whole_trip' if explicit_whole else ('affected_days' if action_cross else 'current_day')
    affected={current_day};affected.update(day for day in request.affected_day_ids if 1<=day<=total_days)
    if scope=='whole_trip':affected=set(range(1,total_days+1))
    elif scope=='current_day':affected={current_day}
    request.scope=scope;request.affected_day_ids=sorted(affected);return request


__all__=['StructuredReplanRequest','ReplanAction','normalize_replan_request','parse_replan_deterministic','resolve_scope']
