"""Adapted data-only equivalents of MIT upstream validation gates."""
import json
import re
from datetime import timedelta
from urllib.parse import quote
from pydantic import ValidationError
from jsonschema import Draft202012Validator, FormatChecker
from .models import DestinationProfile, ItineraryPack, pack_model
from .contracts import adapt_pack_payload, collect_places, split_place_packs
from .budgets import sights_for_city
from .place_names import has_chinese

TRUSTED_HOURS_STATUSES = {'verified', 'provider_verified', 'conflicting'}
_CLOCK_TIME_RE = re.compile(
    r'(?:[01]?\d|2[0-3])\s*[:：]\s*[0-5]\d'
    r'|(?:上午|下午|晚上|早上|凌晨)?\s*(?:[一二三四五六七八九十两]|1?\d|2[0-4])\s*[点時时]'
    r'|(?:24|二十四)\s*小时'
)


def contains_specific_opening_time(value: str) -> bool:
    """Detect clock-style opening hours without rejecting unrelated numbers."""
    return bool(_CLOCK_TIME_RE.search(value or ''))


def _valid_hours_sources(fact) -> list:
    if fact is None:
        return []
    return [source for source in fact.sources
            if source.source_type in ('official_website', 'official_booking', 'amap', 'trusted_third_party')
            and bool(source.source_url)]


def validate_place_hours(place) -> list[dict]:
    """Gate concrete hours by structured verification state and provenance."""
    fact = place.official_facts.get('opening_hours')
    status = fact.status if fact is not None else 'unknown'
    sources = _valid_hours_sources(fact)
    context = {'hours_status': status, 'source_count': len(sources)}
    issue = {
        'pointer': f'/places/{place.id}/hours_note',
        'validator': 'verified_hours_contract',
        'depends_on_map_api': False,
        'invalid_value': place.hours_note,
        'context': context,
    }
    if status in TRUSTED_HOURS_STATUSES and not sources:
        return [{**issue, 'code': 'VERIFIED_FACT_MISSING_SOURCE',
                 'message': '营业时间标记为已核验，但缺少有效来源证据'}]
    if status == 'conflicting' and len(sources) < 2:
        return [{**issue, 'code': 'VERIFIED_FACT_MISSING_SOURCE',
                 'message': '营业时间标记为来源冲突，但未保留至少两条来源证据'}]
    if contains_specific_opening_time(place.hours_note):
        if status not in TRUSTED_HOURS_STATUSES:
            return [{**issue, 'code': 'UNVERIFIED_DYNAMIC_TIME',
                     'message': '未经验证的动态营业时间不得包含具体数字，请使用保守措辞并提示出发前复核'}]
    elif status not in TRUSTED_HOURS_STATUSES and '出发前' not in place.hours_note:
        return [{**issue, 'code': 'UNVERIFIED_DYNAMIC_TIME',
                 'message': '未经验证的营业时间必须使用保守措辞并提示出发前复核'}]
    return []

def _json_pointer(path):
    parts=[str(part).replace('~','~0').replace('/','~1') for part in path]
    return '/' + '/'.join(parts) if parts else '/'

def schema_issues(errors):
    """Flatten jsonschema combinator contexts into actionable leaf errors."""
    result=[]
    def visit(error):
        if error.context:
            for child in error.context:
                visit(child)
            return
        result.append({
            'pointer':_json_pointer(error.absolute_path),
            'instance_path':_json_pointer(error.absolute_path),
            'schema_path':_json_pointer(error.absolute_schema_path),
            'code':f'json_schema.{error.validator}',
            'validator':f'json_schema.{error.validator}',
            'validator_value':error.validator_value,
            'message':error.message,
            'invalid_value':error.instance,
            'depends_on_map_api':False,
        })
    for error in errors:
        visit(error)
    return result

def issues(exc):
    return [{'pointer': '/' + '/'.join(map(str, e['loc'])), 'code': e['type'], 'message': e['msg']}
            for e in exc.errors(include_url=False, include_input=False)]

def map_url(city, name):
    return 'https://uri.amap.com/search?keyword=' + quote(city + ' ' + name) + '&city=' + quote(city)

def validate_day(data, places, *, expected_date=None, expected_city=None, required_ids=()):
    """Fast per-day semantic gate used before a day pack is persisted."""
    errors=[]; by_id={p['id']:p for p in places}; end=-1; ids=[]
    def fail(pointer,message,code='day',invalid_value=None):
        item={'pointer':pointer,'code':code,'message':message}
        if invalid_value is not None:item['invalid_value']=invalid_value
        errors.append(item)
    raw_stops=data.get('stops',[]) if isinstance(data,dict) else []
    raw_segments=data.get('segments',[]) if isinstance(data,dict) else []
    # Legacy profiles stored the incoming edge on the destination stop. Keep
    # precise error reporting while the canonical adapter migrates them.
    if not raw_segments:
        for index,stop in enumerate(raw_stops[1:],1):
            if stop.get('transfer_minutes') is not None and stop.get('transfer_minutes')<=0:
                fail(f'/stops/{index}/transfer_minutes','不同地点之间的路线时长必须大于零','INVALID_ROUTE_DURATION',stop.get('transfer_minutes'))
            if stop.get('distance_km') is not None and stop.get('distance_km')<=0:
                fail(f'/stops/{index}/distance_km','不同地点之间的路线距离必须大于零','INVALID_ROUTE_DISTANCE',stop.get('distance_km'))
    for index,segment in enumerate(raw_segments):
        if segment.get('duration_minutes') is not None and segment.get('duration_minutes')<=0:
            fail(f'/segments/{index}/duration_minutes','不同地点之间的路线时长必须大于零',
                 'INVALID_ROUTE_DURATION',segment.get('duration_minutes'))
        if segment.get('distance_meters') is not None and segment.get('distance_meters')<=0:
            fail(f'/segments/{index}/distance_meters','不同地点之间的路线距离必须大于零',
                 'INVALID_ROUTE_DISTANCE',segment.get('distance_meters'))
    try:
        from .models import Day
        Day.model_validate(data)
    except ValidationError as exc:
        return errors+issues(exc)
    if expected_date and str(data['date']) != str(expected_date): fail('/date','日期与行程骨架不一致')
    if expected_city and data['city'] != expected_city: fail('/city','城市与行程骨架不一致')
    restaurant_count=0
    for i,stop in enumerate(data['stops']):
        pid=stop['place_id']; ids.append(pid); place=by_id.get(pid)
        if not place: fail(f'/stops/{i}/place_id','引用了未知地点')
        elif place['city']!=data['city']: fail(f'/stops/{i}/place_id','地点与当天城市不一致')
        elif place['type']=='restaurant': restaurant_count+=1
        h,m=map(int,stop['arrival_time'].split(':')); start=h*60+m
        if start<end or start+stop['dwell_minutes']>1440: fail(f'/stops/{i}/arrival_time','时间重叠或跨日')
        end=start+stop['dwell_minutes']
    if len(ids)!=len(set(ids)): fail('/stops','同一天存在重复停靠点')
    if len(raw_segments)!=max(0,len(raw_stops)-1):
        fail('/segments','交通段数量必须等于相邻停靠点数量','INVALID_SEGMENT_COUNT',len(raw_segments))
    for i,segment in enumerate(raw_segments):
        if segment.get('from_place_id')!=raw_stops[i].get('place_id') or segment.get('to_place_id')!=raw_stops[i+1].get('place_id'):
            fail(f'/segments/{i}','交通段必须准确连接相邻停靠点','INVALID_SEGMENT_ENDPOINTS',segment)
        if segment.get('route_status') in ('verified','estimated_by_distance'):
            if segment.get('duration_minutes') is None or segment.get('distance_meters') is None:
                fail(f'/segments/{i}/route_status','已核验或估算路线必须包含交通时间与距离')
            if segment.get('route_status')=='verified' and segment.get('route_provider')!='amap':
                fail(f'/segments/{i}/route_provider','已核验路线必须来自高德')
    for pid in required_ids:
        if pid and pid not in ids: fail('/stops',f'缺少行程骨架要求的地点 {pid}')
    if restaurant_count<1: fail('/stops','当天至少需要一个用餐停靠点')
    if len(data['stops'])>6: fail('/stops','单日停靠点过多，路线强度不可行')
    density=data.get('density_evaluation')
    if density:
        meal_minutes=sum(stop['dwell_minutes'] for stop in data['stops'] if by_id.get(stop['place_id'],{}).get('type') in ('restaurant','chain'))
        visit_minutes=sum(stop['dwell_minutes'] for stop in data['stops'] if by_id.get(stop['place_id'],{}).get('type') not in ('restaurant','chain'))
        route_minutes=sum(segment.get('duration_minutes') or 0 for segment in raw_segments)
        fallback_minutes=sum(segment.get('fallback_schedule_minutes') or 0 for segment in raw_segments)
        if density['stop_count']!=len(data['stops']) or density['meal_minutes']!=meal_minutes or density['visit_minutes']!=visit_minutes:
            fail('/density_evaluation','密度统计必须与最终停靠点一致')
        if density['total_route_minutes']!=route_minutes:
            fail('/density_evaluation/total_route_minutes','密度统计的路线时间必须与最终停靠点一致')
        if density.get('fallback_schedule_minutes',0)!=fallback_minutes:
            fail('/density_evaluation/fallback_schedule_minutes','fallback 排程时间统计必须与最终停靠点一致')
        expected=(density['visit_minutes']+density['meal_minutes']+density['total_route_minutes']+
                  density.get('fallback_schedule_minutes',0)+density['configured_buffer_minutes'])
        if density['scheduled_minutes']!=expected:
            fail('/density_evaluation/scheduled_minutes','scheduled_minutes 计算口径不一致')
    return errors

def validate_trip_coherence(itinerary, plan, places, preferences):
    """Whole-trip gate. Errors name only the day packs that need regeneration."""
    errors=[]; by_id={p['id']:p for p in places}; seen={}
    for index,day in enumerate(itinerary,1):
        for stop in day['stops']:
            pid=stop['place_id']
            if by_id.get(pid,{}).get('type') in ('sight','experience'): seen.setdefault(pid,[]).append(index)
    for pid,days in seen.items():
        if len(days)>1:
            for day in days[1:]: errors.append({'pointer':f'/itinerary/{day-1}/stops','code':'CROSS_DAY_DUPLICATE',
                'pack_id':f'itinerary-day-{day}','message':f'sight / experience {pid} 跨日重复',
                'invalid_value':pid,'validator':'ownership_safety_net','depends_on_map_api':False})
    avoid=(preferences.get('avoid') or '').strip()
    if avoid:
        for index,day in enumerate(itinerary,1):
            text=json.dumps(day,ensure_ascii=False)
            if avoid in text: errors.append({'pointer':f'/itinerary/{index-1}','code':'coherence','pack_id':f'itinerary-day-{index}','message':'包含用户明确避开的内容'})
    # Every skeleton anchor must remain assigned to its own day.
    for index,(day,outline) in enumerate(zip(itinerary,plan['days']),1):
        ids={s['place_id'] for s in day['stops']}
        if outline['primary_anchor_id'] not in ids:
            errors.append({'pointer':f'/itinerary/{index-1}/stops','code':'coherence','pack_id':f'itinerary-day-{index}','message':'全程骨架主锚点缺失'})
    return errors

def validate_research_pack(pack_id, data):
    model=pack_model(pack_id)
    if model is None:
        return [{'pointer': '/', 'code': 'pack_id', 'message': '未知研究包'}]
    try:
        data=adapt_pack_payload(pack_id,data)
        model.model_validate(data)
        return schema_issues(Draft202012Validator(model.model_json_schema(),format_checker=FormatChecker()).iter_errors(data))
    except ValidationError as exc:
        return issues(exc)

def validate_destination_data(data):
    try:
        profile = DestinationProfile.model_validate(data)
    except ValidationError as exc:
        return issues(exc)
    errors = []
    def fail(path, message, *, code='content', invalid_value=None, validator='semantic_validation', context=None):
        item={'pointer': path, 'code': code, 'message': message, 'invalid_value': invalid_value,
              'validator': validator, 'depends_on_map_api': False}
        if context is not None:item['context']=context
        errors.append(item)
    schema = DestinationProfile.model_json_schema()
    errors.extend(schema_issues(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data)))
    t, places, groups = profile.trip, profile.places, profile.module_groups
    visible_places=[{key:place.get(key) for key in ('display_name','description','cuisine','signature_dishes','experience_title')}
                    for place in data['places']]
    # Provider taxonomy/provenance may legitimately contain a commerce category.
    # The no-shopping product rule applies to user-visible recommendations and modules.
    generated_text=json.dumps({'places':visible_places,'itinerary':data['itinerary'],'modules':data['module_groups']},ensure_ascii=False)
    if re.search(r'购物|伴手礼|纪念品|退税|souvenir|shopping',generated_text,re.I): fail('/', '生成内容不能包含购物模块或购物推荐')
    by_id = {p.id: p for p in places}
    if len(by_id) != len(places): fail('/places', '地点ID重复')
    if len({(p.type, p.city, p.display_name) for p in places}) != len(places): fail('/places', '地点名称重复')
    if profile.destination != t.mainDestination: fail('/destination', '与主要目的地不一致')
    if [d.date for d in profile.itinerary] != [t.startDate + timedelta(days=i) for i in range(t.days)]:
        fail('/itinerary', '日期须连续且完整覆盖入参日期')
    if set(d.city for d in profile.itinerary) != set(t.destinations): fail('/itinerary', '城市覆盖不完整')
    order = [t.destinations.index(d.city) for d in profile.itinerary if d.city in t.destinations]
    if order != sorted(order): fail('/itinerary', '城市顺序与用户输入不一致')
    for city in t.destinations:
        city_days=sum(day.city==city for day in profile.itinerary)
        if sum(p.type == 'sight' and p.city == city for p in places) < sights_for_city(city_days):
            fail('/places', city + '景点数量低于按停留天数计算的研究预算')
    for p in places:
        if p.city not in t.destinations: fail('/places/' + p.id, '地点不在目标城市')
        if p.provider=='amap' and (not p.provider_place_id or p.latitude is None or p.longitude is None):
            fail('/places/' + p.id, '已绑定高德的地点必须包含 provider_place_id 和坐标')
        if p.provider!='amap' and p.verification_status=='verified':
            fail('/places/' + p.id + '/verification_status', '未绑定真实数据源的地点不能标记为已核验')
        if has_chinese(p.local_name) and p.display_name.strip()!=p.local_name.strip():
            fail('/places/' + p.id + '/display_name', '中文界面必须使用已有的中文本地名称',
                 code='DISPLAY_NAME_NOT_LOCAL',invalid_value=p.display_name,
                 context={'local_name':p.local_name,'display_name':p.display_name})
        if p.type=='experience' and p.entity_kind=='experience_concept' and not p.linked_place_id:
            if p.booking_status!='unverified':
                fail('/places/' + p.id + '/booking_status', '未关联地点的体验构想不得标记为已核验可预约')
        errors.extend(validate_place_hours(p))
        for key in ('ticket_note', 'reservation_note'):
            value = getattr(p, key)
            if '出发前请复核' not in value or re.search(r'\d|[零一二三四五六七八九十百]+[元时点分折]', value):
                fail('/places/' + p.id + '/' + key,
                     '未经验证的动态信息不得包含具体数字，并须标注出发前请复核',
                     code='UNVERIFIED_DYNAMIC_FACT',invalid_value=value)
        if p.entity_kind=='experience_concept' and not p.linked_place_id:
            if p.map_url is not None: fail('/places/' + p.id + '/map_url', '未关联真实地点的体验概念不得生成地图链接')
        elif p.map_url != map_url(p.city, p.display_name): fail('/places/' + p.id + '/map_url', '地图链接必须由中文展示名称生成')
    scheduled = set()
    for day_index,d in enumerate(profile.itinerary):
        end = -1
        for index,stop in enumerate(d.stops):
            p = by_id.get(stop.place_id)
            if p is None:
                fail(f'/itinerary/{day_index}/stops/{index}/place_id','停靠点引用不在 canonical places 中',
                     code='MISSING_CANONICAL_PLACE_REFERENCE',invalid_value=stop.place_id,
                     context={'pack':f'itinerary-day-{day_index+1}','day':day_index+1,'date':str(d.date),
                              'field':'place_id','old_place_id':stop.place_id})
            elif p.city != d.city:
                fail(f'/itinerary/{day_index}/stops/{index}/place_id','停靠点城市与当天城市不一致',
                     code='PLACE_CITY_MISMATCH',invalid_value=stop.place_id,
                     context={'pack':f'itinerary-day-{day_index+1}','day':day_index+1,'date':str(d.date),
                              'expected_city':d.city,'actual_city':p.city,'field':'place_id'})
            scheduled.add(stop.place_id)
            h, m = map(int, stop.arrival_time.split(':')); start = h*60+m
            if start < end or start + stop.dwell_minutes > 1440: fail('/itinerary', '时间顺序重叠或跨日')
            end = start + stop.dwell_minutes
        if len(d.segments) != max(0, len(d.stops)-1):
            fail('/itinerary', '交通段数量与相邻停靠点不一致')
        for index,segment in enumerate(d.segments):
            if segment.from_place_id != d.stops[index].place_id or segment.to_place_id != d.stops[index+1].place_id:
                fail('/itinerary', '交通段端点与相邻停靠点不一致')
            if segment.route_status in ('verified','estimated_by_distance'):
                if segment.duration_minutes is None or segment.distance_meters is None:
                    fail('/itinerary', '路线状态与交通时间或距离不一致')
                if segment.route_status=='verified' and segment.route_provider!='amap':
                    fail('/itinerary', '已核验路线必须来自高德')
        if len({s.place_id for s in d.stops}) != len(d.stops): fail('/itinerary', '同日重复停靠点')
    def refs(values, types, path):
        ids = [r.place_id for r in values]
        if len(ids) != len(set(ids)): fail(path, '引用重复')
        for index,place_id in enumerate(ids):
            place=by_id.get(place_id)
            if place is None:
                fail(f'{path}/{index}/place_id','地点引用不在 canonical places 中',
                     code='MISSING_CANONICAL_PLACE_REFERENCE',invalid_value=place_id,
                     context={'pack':'modules-practical','field':f'{path}/{index}/place_id','old_place_id':place_id})
            elif place.type not in types:
                fail(f'{path}/{index}/place_id','地点引用类型不符',code='PLACE_REFERENCE_TYPE_MISMATCH',
                     invalid_value=place_id,context={'pack':'modules-practical','expected_types':sorted(types),'actual_type':place.type})
        return set(ids)
    sg = refs(groups.sights.scheduled, {'sight'}, '/module_groups/sights/scheduled')
    opt = refs(groups.sights.optional, {'sight'}, '/module_groups/sights/optional')
    all_sights = {p.id for p in places if p.type == 'sight'}
    if sg != scheduled & all_sights or sg & opt or sg | opt != all_sights: fail('/module_groups/sights', '景点安排和备选必须完整对应行程')
    exp_ids = []
    for g in groups.experiences:
        exp_ids.extend(refs(g.items, {'experience'}, '/module_groups/experiences'))
        if len({by_id[r.place_id].experience_type for r in g.items if r.place_id in by_id}) != 1: fail('/module_groups/experiences', '同组体验类型必须一致')
    if len(set(exp_ids)) != len(exp_ids): fail('/module_groups/experiences', '体验选项不能重复')
    dedicated = refs(groups.food.dedicated_trip, {'restaurant'}, '/module_groups/food/dedicated_trip')
    refs(groups.food.reliable_chains, {'chain'}, '/module_groups/food/reliable_chains')
    if len({n.category for n in groups.travel_notes}) != len(groups.travel_notes): fail('/module_groups/travel_notes', '贴士分类不能重复')
    return errors

def check_handoff(profile, packs):
    errors = []
    packs=split_place_packs(packs)
    for name in ('places-core','places-experiences','places-food','modules-practical','modules-language-notes'):
        errors.extend(validate_research_pack(name,packs.get(name)))
    try:ItineraryPack.model_validate(packs.get('itinerary'))
    except ValidationError as exc:errors.extend(issues(exc))
    errors.extend(validate_destination_data(profile))
    if not errors:
        if collect_places(packs) != profile['places'] or packs['itinerary']['itinerary'] != profile['itinerary']:
            errors.append({'pointer': '/', 'code': 'provenance', 'message': '档案与研究包不一致'})
        for key, val in {**packs['modules-practical'], **packs['modules-language-notes']}.items():
            if profile['module_groups'][key] != val:
                errors.append({'pointer': '/module_groups/' + key, 'code': 'provenance', 'message': '模块与研究包不一致'})
    return errors
