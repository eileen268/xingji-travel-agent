"""Compile user input into the single structured planning-constraint contract."""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

from .models import (ArrivalConstraint,BuildInput,CityStayConstraint,ConstraintProvenance,
                     ConstraintFallbackDecision,DepartureConstraint,StructuredTripConstraints,TextTripConstraint)

PARSER_VERSION='trip-constraints-v1'
_CN={'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}


def _number(value: str) -> int | None:
    value=value.strip()
    if value.isdigit():return int(value)
    if value in _CN:return _CN[value]
    if value.startswith('十') and len(value)==2:return 10+_CN.get(value[1],0)
    if value.endswith('十') and len(value)==2:return _CN.get(value[0],0)*10
    if '十' in value and len(value)==3:return _CN.get(value[0],0)*10+_CN.get(value[2],0)
    return None


def _provenance(text: str,source_type='notes',parser='deterministic_zh_v1',confidence=.99):
    return ConstraintProvenance(source_text=text,source_type=source_type,parser=parser,confidence=confidence)


def _date_for(trip: BuildInput,month_text: str|None,day_text: str) -> date | None:
    day=_number(day_text)
    if day is None:return None
    month=_number(month_text) if month_text else None
    candidates=[]
    for year in range(trip.startDate.year,trip.endDate.year+1):
        months=[month] if month else list(range(1,13))
        for current_month in months:
            if current_month and day<=monthrange(year,current_month)[1]:
                candidate=date(year,current_month,day)
                if trip.startDate<=candidate<=trip.endDate:candidates.append(candidate)
    return min(candidates) if candidates else None


def _time(period: str|None,hour_text: str,minute_text: str|None=None) -> str | None:
    hour=_number(hour_text);minute=_number(minute_text) if minute_text else 0
    if hour is None or minute is None or hour>23 or minute>59:return None
    if period in ('下午','晚上','傍晚') and hour<12:hour+=12
    if period=='中午' and hour<11:hour+=12
    if period in ('凌晨',) and hour==12:hour=0
    if period=='上午' and hour==12:hour=0
    return f'{hour:02d}:{minute:02d}'


def _clock_from(match) -> str | None:
    explicit=match.groupdict().get('clock')
    if explicit:
        hour,minute=explicit.split(':');return _time(None,hour,minute)
    minute=match.groupdict().get('minute')
    if match.groupdict().get('half'):minute='30'
    return _time(match.groupdict().get('period'),match.group('hour'),minute)


def _merge_unique(items,key):
    result={}
    for item in items:result[key(item)]=item
    return list(result.values())


def compile_trip_constraints(trip: BuildInput) -> StructuredTripConstraints:
    """Parse explicit Chinese constraints once. UI constraints have highest precedence."""
    base=(trip.structuredConstraints.model_copy(deep=True) if trip.structuredConstraints
          else StructuredTripConstraints(parser_version=PARSER_VERSION))
    notes=trip.notes.strip();provenance=_provenance(notes)
    stay=list(base.city_stay_constraints);arrivals=list(base.arrival_constraints);departures=list(base.departure_constraints)
    spans=[]
    for city in trip.destinations:
        for match in re.finditer(rf'{re.escape(city)}(?:市)?(?:玩|游玩|停留|待|住)?\s*(?P<days>\d+|[一二两三四五六七八九十]{{1,3}})\s*(?:天|日)',notes):
            days=_number(match.group('days'))
            if days:stay.append(CityStayConstraint(city=city,days=days,provenance=provenance));spans.append(match.span())
    date_part=r'(?:(?P<month>\d{1,2}|[一二三四五六七八九十]{1,3})月)?(?P<day>\d{1,2}|[一二三四五六七八九十]{1,3})(?:号|日)'
    time_part=r'(?:(?P<period>上午|下午|晚上|傍晚|中午|凌晨)\s*)?(?:(?P<clock>\d{1,2}:\d{2})|(?P<hour>\d{1,2}|[一二两三四五六七八九十]{1,3})点(?:(?P<minute>\d{1,2})分|(?P<half>半))?)'
    for city in trip.destinations:
        arrival_patterns=(rf'{date_part}.*?{time_part}.*?(?:到|抵达|到达|才到)(?:达)?{re.escape(city)}',
                          rf'{date_part}.*?(?:到|抵达|到达|才到)(?:达)?{re.escape(city)}.*?{time_part}')
        departure_patterns=(rf'{date_part}.*?{time_part}.*?(?:离开|出发离开|从){re.escape(city)}',
                            rf'{date_part}.*?(?:离开|从){re.escape(city)}.*?{time_part}')
        for pattern,target,kind in ((*[(p,arrivals,'arrival') for p in arrival_patterns],
                                     *[(p,departures,'departure') for p in departure_patterns])):
            for match in re.finditer(pattern,notes):
                parsed_date=_date_for(trip,match.group('month'),match.group('day'));parsed_time=_clock_from(match)
                if not parsed_date or not parsed_time:continue
                if kind=='arrival':target.append(ArrivalConstraint(date=parsed_date,city=city,arrival_time=parsed_time,provenance=provenance))
                else:target.append(DepartureConstraint(date=parsed_date,city=city,departure_time=parsed_time,provenance=provenance))
                spans.append(match.span())
    def ui_wins(items,key):
        ordered=sorted(items,key=lambda item:0 if item.provenance.source_type!='ui' else 1)
        return _merge_unique(ordered,key)
    base.city_stay_constraints=ui_wins(stay,lambda x:x.city)
    base.arrival_constraints=ui_wins(arrivals,lambda x:(x.date,x.city))
    base.departure_constraints=ui_wins(departures,lambda x:(x.date,x.city))
    if trip.preferences.mustGo and not base.must_visit_constraints:
        base.must_visit_constraints=[TextTripConstraint(text=trip.preferences.mustGo,provenance=_provenance(trip.preferences.mustGo,'ui','structured_ui',1))]
    if trip.preferences.avoid and not base.avoid_constraints:
        base.avoid_constraints=[TextTripConstraint(text=trip.preferences.avoid,provenance=_provenance(trip.preferences.avoid,'ui','structured_ui',1))]
    # First-version typed pass-through constraints. These remain soft until a
    # deterministic consumer explicitly supports their semantics.
    if notes and re.search(r'早餐|午饭|午餐|晚饭|晚餐|下午茶|咖啡|吃饭|用餐',notes) and not base.meal_constraints:
        base.meal_constraints=[TextTripConstraint(text=notes,provenance=provenance)]
    if notes and re.search(r'轻松|紧凑|慢一点|快一点|休息',notes) and not base.pace_constraints:
        base.pace_constraints=[TextTripConstraint(text=notes,provenance=provenance)]
    if notes and re.search(r'公交|地铁|打车|出租车|自驾|步行|少走路|少换乘',notes) and not base.transport_constraints:
        base.transport_constraints=[TextTripConstraint(text=notes,provenance=provenance)]
    if notes and re.search(r'第[一二三四五六七八九十\d]+天|上午|下午|晚上|半天|自由活动',notes) and not base.day_specific_constraints:
        base.day_specific_constraints=[TextTripConstraint(text=notes,provenance=provenance)]
    # Only planning-like text that deterministic rules could not consume is eligible for an LLM fallback.
    residual=notes
    for start,end in sorted(spans,reverse=True):residual=residual[:start]+' '+residual[end:]
    residual=residual.strip(' ，,。;；')
    cue=bool(re.search(r'玩|停留|待|住|到达|抵达|才到|离开|出发',residual))
    base.unparsed_notes=[residual] if residual and cue else []
    base.parser_version=PARSER_VERSION
    return base


def city_day_counts(trip: BuildInput,constraints: StructuredTripConstraints) -> dict[str,int]:
    explicit={item.city:item.days for item in constraints.city_stay_constraints}
    if any(city not in trip.destinations for city in explicit):raise ValueError('城市停留约束包含非目的地城市')
    unspecified=[city for city in trip.destinations if city not in explicit]
    if sum(explicit.values())+len(unspecified)>trip.days:
        raise ValueError('城市停留天数超过总行程天数')
    counts={city:explicit.get(city,1) for city in trip.destinations}
    remaining=trip.days-sum(counts.values())
    if remaining:
        # Parsed/UI counts are exact. Defaults may distribute leftover days
        # only to a city whose duration was not explicitly locked.
        if not unspecified:raise ValueError('已指定的城市停留天数未完整覆盖总行程天数')
        recipient=trip.mainDestination if trip.mainDestination in unspecified else unspecified[0]
        counts[recipient]+=remaining
    return counts


def city_for_day(trip: BuildInput,index: int,constraints: StructuredTripConstraints) -> str:
    counts=city_day_counts(trip,constraints);cursor=0
    for city in trip.destinations:
        cursor+=counts[city]
        if index<cursor:return city
    return trip.destinations[-1]


def merge_llm_fallback(trip: BuildInput,constraints: StructuredTripConstraints,
                       decision: ConstraintFallbackDecision) -> StructuredTripConstraints:
    """Accept only bounded, in-trip fallback values; deterministic/UI values keep precedence."""
    result=constraints.model_copy(deep=True)
    text='；'.join(result.unparsed_notes);provenance=_provenance(text,'llm_fallback','structured_llm_fallback_v1',.75)
    known_stays={item.city for item in result.city_stay_constraints}
    for item in decision.city_stay_constraints:
        if item.city in trip.destinations and item.city not in known_stays:
            result.city_stay_constraints.append(CityStayConstraint(city=item.city,days=item.days,provenance=provenance))
    known_arrivals={(item.date,item.city) for item in result.arrival_constraints}
    for item in decision.arrival_constraints:
        if (trip.startDate<=item.date<=trip.endDate and item.city in trip.destinations
                and (item.date,item.city) not in known_arrivals):
            result.arrival_constraints.append(ArrivalConstraint(**item.model_dump(),provenance=provenance))
    known_departures={(item.date,item.city) for item in result.departure_constraints}
    for item in decision.departure_constraints:
        if (trip.startDate<=item.date<=trip.endDate and item.city in trip.destinations
                and (item.date,item.city) not in known_departures):
            result.departure_constraints.append(DepartureConstraint(**item.model_dump(),provenance=provenance))
    city_day_counts(trip,result)
    result.unparsed_notes=[]
    return result
