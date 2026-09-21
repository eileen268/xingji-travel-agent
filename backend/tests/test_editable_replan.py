import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app, connect
from app.editable_replan import (_assign, _insert_place_preserving_schedule, _load, _parse_instruction,
                                 _clock, _minute, _place_from_candidate, _promote_dedicated_place, _resolve_dedicated_place)
from app.replan_intent import AddPlaceAction, StructuredReplanRequest, normalize_replan_request, parse_replan_deterministic, resolve_scope
from app.replan import ReplanError
from app.services.amap.poi_search import AmapPoiCandidate
from app.services.amap.route import RouteResult
from app.scheduler_config import PACE_CONFIG
from app.models import Place
from app.validators import validate_place_hours


PREFS=dict(origin='上海',destinations=['杭州'],mainDestination='杭州',startDate='2026-10-02',endDate='2026-10-04',
    adults=2,children=0,seniors=0,travelStyle='朋友旅行',budget={'total':6000},
    preferences={'pace':'balanced','interests':['历史文化'],'localTransport':['transit']},outboundPeriod='morning',returnPeriod='evening')


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'editable.db'));monkeypatch.setenv('ENABLE_DEV_MODES','1')
    monkeypatch.setenv('ZHIPU_API_KEY','');monkeypatch.setattr('app.editable_replan._route_service',lambda connect:None)
    with TestClient(app) as value:yield value


def job(client):
    job_id=client.post('/api/build',json=PREFS,headers={'X-Agent-Mode':'MOCK'}).json()['job_id']
    for _ in range(200):
        status=client.get(f'/api/build/{job_id}').json()['status']
        if status in ('done','done_with_warnings'):return job_id
        time.sleep(.02)
    pytest.fail('job did not finish')


def test_trip_level_instruction_uses_shared_preview_contract(client):
    job_id=job(client)
    response=client.post(f'/api/trips/{job_id}/replan-preview',json={
        'instruction':'整趟行程轻松一点，少走路',
        'anchor_day_id':f'{job_id}-day-1',
        'ui_scope_context':'trip',
    })
    assert response.status_code==200,response.text
    payload=response.json()
    assert payload['requires_scope_confirmation'] is True
    assert payload['scope']=='whole_trip'
    assert payload['details']['preview']['scope']=='whole_trip'


def test_custom_activity_preview_confirm_and_undo(client):
    job_id=job(client);before=client.get(f'/api/build/{job_id}/result').json()
    preview=client.post(f'/api/trips/{job_id}/days/{job_id}-day-1/custom-activities',json={
        'title':'回酒店休息','note':'午后休息','start_time_preference':'15:00','stay_minutes':60}).json()
    assert preview['diff']['added'][0]['name']=='回酒店休息'
    confirmed=client.post(f'/api/trips/{job_id}/replan-previews/{preview["preview_id"]}/confirm')
    assert confirmed.status_code==200,confirmed.text
    after=client.get(f'/api/build/{job_id}/result').json()
    custom=next(place for place in after['places'] if place['display_name']=='回酒店休息')
    assert custom['user_created'] is True and custom['custom_activity']['user_created'] is True
    assert custom['id'] in [stop['place_id'] for stop in after['itinerary'][0]['stops']]
    undo=client.post(f'/api/trips/{job_id}/days/1/undo')
    assert undo.status_code==200,undo.text
    restored=client.get(f'/api/build/{job_id}/result').json()
    assert restored['itinerary'][0]==before['itinerary'][0]
    assert custom['id'] not in {place['id'] for place in restored['places']}


def test_stop_edit_does_not_own_transport(client):
    job_id=job(client);profile=client.get(f'/api/build/{job_id}/result').json();stop=profile['itinerary'][0]['stops'][1]
    response=client.patch(f'/api/trips/{job_id}/days/1/stops/{stop["place_id"]}',json={
        'stay_minutes':75,'locked':True,'must_keep':True,'note':'用户指定保留','priority':5,'transport_to_next':'taxi'})
    assert response.status_code==200,response.text
    updated=client.get(f'/api/build/{job_id}/result').json()['itinerary'][0]
    edited=next(value for value in updated['stops'] if value['place_id']==stop['place_id'])
    assert edited['dwell_minutes']==75 and edited['locked'] and edited['must_keep']
    assert edited['note']=='用户指定保留' and edited['transport_to_next'] is None


def test_day_and_segment_transport_only_change_target_day(client):
    job_id=job(client);before=client.get(f'/api/build/{job_id}/result').json()
    changed=client.patch(f'/api/trips/{job_id}/days/1/transport',json={'transport_mode':'taxi'})
    assert changed.status_code==200,changed.text
    middle=client.get(f'/api/build/{job_id}/result').json();assert middle['itinerary'][1:]==before['itinerary'][1:]
    stops=middle['itinerary'][0]['stops']
    assert all(stop['transport_mode']=='驾车或出租车' for stop in stops[1:])
    assert all(stop['transfer_minutes'] is None or stop['transfer_minutes']>0 for stop in stops[1:])
    assert all(stop['distance_km'] is None or stop['distance_km']>0 for stop in stops[1:])
    segment=client.patch(f'/api/trips/{job_id}/days/1/segments/transport',json={
        'origin_place_id':stops[0]['place_id'],'destination_place_id':stops[1]['place_id'],'transport_mode':'walking'})
    assert segment.status_code==200,segment.text
    final=client.get(f'/api/build/{job_id}/result').json();assert final['itinerary'][1:]==before['itinerary'][1:]
    first_leg=final['itinerary'][0]['stops'][1]
    first_segment=final['itinerary'][0]['segments'][0]
    assert first_leg['transport_mode']=='步行'
    assert first_segment['from_place_id']==stops[0]['place_id'] and first_segment['to_place_id']==stops[1]['place_id']
    assert first_segment['mode']=='walking'
    assert first_leg['transfer_minutes'] is None or first_leg['transfer_minutes']>0
    assert first_leg['distance_km'] is None or first_leg['distance_km']>0


def test_natural_language_preview_uses_structured_constraints(client):
    job_id=job(client);profile=client.get(f'/api/build/{job_id}/result').json();name=profile['places'][0]['display_name']
    response=client.post(f'/api/trips/{job_id}/days/1/replan-preview',json={'instruction':f'轻松一点，少走路，保留{name}，晚上留两小时自由活动'})
    assert response.status_code==200,response.text
    payload=response.json();actions=payload['parsed_constraints']['actions']
    assert any(value['type']=='set_pace' and value.get('pace')=='relaxed' for value in actions)
    assert any(value['type']=='set_pace' and value.get('max_walking_load')=='low' for value in actions)
    assert any(value['type']=='add_free_time' for value in actions)
    assert payload['diff']['added']


def test_search_add_uses_user_confirmed_amap_candidate(client,monkeypatch):
    job_id=job(client)
    candidate=AmapPoiCandidate(provider_place_id='B001',name='武康大楼',city='杭州',district='西湖区',address='测试路1号',latitude=30.2,longitude=120.1,category='风景名胜')
    class Dummy:pass
    async def fake_search(client,keyword,city,limit=10):return [candidate],False
    class Routes:
        async def route(self,origin,destination,mode,city):
            return RouteResult(origin_place_id=origin['id'],destination_place_id=destination['id'],mode=mode,
                distance_meters=500,duration_seconds=60,provider='amap',verification_status='verified',
                queried_at='2026-01-01T00:00:00+00:00')
    monkeypatch.setattr('app.editable_replan.AmapClient',lambda connect:Dummy())
    monkeypatch.setattr('app.editable_replan.search_pois',fake_search)
    monkeypatch.setattr('app.editable_replan._route_service',lambda connect:Routes())
    choices=client.post(f'/api/trips/{job_id}/days/1/stops/search-add',json={'query':'武康大楼','city':'杭州'}).json()
    assert choices['requires_confirmation'] and choices['candidates'][0]['provider_place_id']=='B001'
    preview=client.post(f'/api/trips/{job_id}/days/1/stops/search-add',json={
        'query':'武康大楼','city':'杭州','provider_place_id':'B001','stay_minutes':15})
    assert preview.status_code==200,preview.text
    diff=preview.json()['diff'];assert diff['removed']==[] and diff['moved']==[]
    confirmed=client.post(f'/api/trips/{job_id}/replan-previews/{preview.json()["preview_id"]}/confirm')
    assert confirmed.status_code==200,confirmed.text
    result=client.get(f'/api/build/{job_id}/result').json()
    place=next(value for value in result['places'] if value['provider_place_id']=='B001')
    assert place['provider']=='amap' and place['user_created'] is True


def test_non_destructive_add_preserves_existing_schedule(client):
    job_id=job(client);state=_load(connect,job_id,1);before=list(state['itinerary'][0]['stops'])
    city=state['itinerary'][0]['city']
    candidate=AmapPoiCandidate(provider_place_id='B-NEW',name='新增晚间地点',city=city,district='测试区',address='测试路',
        latitude=30.2,longitude=120.1,category='风景名胜')
    place=_place_from_candidate(candidate,city,candidate.name);_assign(state,1,place);state['connect']=connect
    last=before[-1];hour,minute=map(int,last['arrival_time'].split(':'));requested=hour*60+minute+last['dwell_minutes']+30
    class Routes:
        async def route(self,origin,destination,mode,city):
            return RouteResult(origin_place_id=origin['id'],destination_place_id=destination['id'],mode=mode,
                distance_meters=800,duration_seconds=600,provider='amap',verification_status='verified',
                queried_at='2026-01-01T00:00:00+00:00')
    action=AddPlaceAction(query=candidate.name,requested_start_time=f'{requested//60:02d}:{requested%60:02d}',
        preferred_start_time=f'{requested//60:02d}:{requested%60:02d}',time_constraint_strength='hard',stay_minutes=30)
    day,_,trace=asyncio.run(_insert_place_preserving_schedule(state,job_id,1,place,action,Routes()))
    assert day['stops'][:-1]==before
    assert day['stops'][-1]['place_id']==place['id'] and day['stops'][-1]['arrival_time']==action.requested_start_time
    assert day['segments'][-1]['route_provider']=='amap' and day['segments'][-1]['duration_minutes']==10
    assert len(trace['route_legs'])==1 and trace['route_legs'][0]['to_place_id']==place['id']


def test_non_destructive_add_returns_conflict_without_mutation(client):
    job_id=job(client);state=_load(connect,job_id,1);before=list(state['itinerary'][0]['stops']);city=state['itinerary'][0]['city']
    candidate=AmapPoiCandidate(provider_place_id='B-LATE',name='冲突地点',city=city,district='测试区',address='测试路',
        latitude=30.2,longitude=120.1,category='风景名胜')
    place=_place_from_candidate(candidate,city,candidate.name);_assign(state,1,place);state['connect']=connect
    last=before[-1];hour,minute=map(int,last['arrival_time'].split(':'));requested=hour*60+minute+last['dwell_minutes']+20
    class SlowRoutes:
        async def route(self,origin,destination,mode,city):
            return RouteResult(origin_place_id=origin['id'],destination_place_id=destination['id'],mode=mode,
                distance_meters=30000,duration_seconds=7200,provider='amap',verification_status='verified',
                queried_at='2026-01-01T00:00:00+00:00')
    action=AddPlaceAction(query=candidate.name,requested_start_time=f'{requested//60:02d}:{requested%60:02d}',
        preferred_start_time=f'{requested//60:02d}:{requested%60:02d}',time_constraint_strength='hard',stay_minutes=30)
    with pytest.raises(ReplanError) as raised:
        asyncio.run(_insert_place_preserving_schedule(state,job_id,1,place,action,SlowRoutes()))
    assert raised.value.code=='REPLAN_NO_FEASIBLE_SLOT' and raised.value.details
    assert state['itinerary'][0]['stops']==before


def test_non_destructive_add_accepts_coordinate_estimate_with_safety_margin(client):
    job_id=job(client);state=_load(connect,job_id,1);before=list(state['itinerary'][0]['stops']);city=state['itinerary'][0]['city']
    candidate=AmapPoiCandidate(provider_place_id='B-EST',name='估算路线地点',city=city,district='测试区',address='测试路',
        latitude=30.21,longitude=120.11,category='风景名胜')
    place=_place_from_candidate(candidate,city,candidate.name);_assign(state,1,place);state['connect']=connect
    last=before[-1];last_end=_minute(last['arrival_time'])+last['dwell_minutes'];requested=last_end+70
    calls=[]
    class EstimatedRoutes:
        async def route(self,origin,destination,mode,city):
            calls.append((origin['id'],destination['id']))
            return RouteResult(origin_place_id=origin['id'],destination_place_id=destination['id'],mode=mode,
                distance_meters=5000,duration_seconds=1800,provider='deterministic_estimate',verification_status='estimated_by_distance',
                warning='fallback',route_error='ROUTE_NOT_FOUND',queried_at='2026-01-01T00:00:00+00:00')
    action=AddPlaceAction(query=candidate.name,requested_start_time=_clock(requested),preferred_start_time=_clock(requested),
        time_constraint_strength='hard',stay_minutes=30)
    day,_,trace=asyncio.run(_insert_place_preserving_schedule(state,job_id,1,place,action,EstimatedRoutes()))
    assert day['stops'][:-1]==before and day['stops'][-1]['arrival_time']==_clock(requested)
    assert trace['used_fallback_estimate'] is True and trace['safety_margin_minutes']==15
    assert trace['earliest_feasible_arrival']==_clock(last_end+30+max(PACE_CONFIG[state['trip'].preferences.pace].route_buffer_minutes,5)+15)
    assert len(trace['route_legs'])==1 and calls
    assert any(warning['code']=='ROUTE_ESTIMATED_FOR_INSERTION' for warning in day['schedule_warnings'])
    assert any(warning['code']=='FACT_NEEDS_RECHECK' for warning in day['schedule_warnings'])


def test_add_place_amap_hours_include_provider_provenance():
    candidate=AmapPoiCandidate(provider_place_id='B00150F6D6',name='东方明珠广播电视塔',city='上海',
        district='浦东新区',address='世纪大道1号',latitude=31.239703,longitude=121.499718,category='风景名胜',
        opening_hours='周一至周日 09:00-21:00 最晚进入21:00',opening_hours_today='09:00-21:00',
        opening_hours_weekly='周一至周日 09:00-21:00 最晚进入21:00',
        business={'opentime_today':'09:00-21:00','opentime_week':'周一至周日 09:00-21:00 最晚进入21:00'})
    place=_place_from_candidate(candidate,'上海',candidate.name)
    fact=place['official_facts']['opening_hours']
    assert fact['status']=='provider_verified' and fact['source_type']=='amap'
    assert fact['provider_place_id']=='B00150F6D6' and len(fact['sources'])==1
    assert not validate_place_hours(Place.model_validate(place))


def test_must_keep_stop_cannot_be_deleted(client):
    job_id=job(client);profile=client.get(f'/api/build/{job_id}/result').json();stop=profile['itinerary'][0]['stops'][1]
    assert client.patch(f'/api/trips/{job_id}/days/1/stops/{stop["place_id"]}',json={'must_keep':True}).status_code==200
    deleted=client.delete(f'/api/trips/{job_id}/days/1/stops/{stop["place_id"]}')
    assert deleted.status_code==422 and deleted.json()['detail']['code']=='IMMUTABLE_STOP'


def test_parser_contract_normalizes_legacy_aliases_and_null_lists():
    raw={'keep':['西湖'],'remove':None,'must_include_queries':None,'must_exclude_queries':None,
         'transportation_preference':'打车','time_constraints':None,'budget_constraints':None}
    value=normalize_replan_request(raw,[{'name':'西湖','place_id':'sight-1'}])
    parsed=StructuredReplanRequest.model_validate(value)
    assert any(action.type=='keep_place' and action.place_ref=='sight-1' for action in parsed.actions)
    assert any(action.type=='set_transport' and action.mode=='driving' for action in parsed.actions)


@pytest.mark.parametrize(('instruction','expected'),[
    ('今天轻松一点','current_day'),('迪士尼安排一整天，其他景点放到别天','affected_days'),('整趟旅行按照少走路重新规划','whole_trip')])
def test_scope_resolver_is_explicit_and_bounded(instruction,expected):
    parsed=resolve_scope(instruction,parse_replan_deterministic(instruction,[],2,5),2,5)
    assert parsed.scope==expected
    assert parsed.affected_day_ids==([1,2,3,4,5] if expected=='whole_trip' else [2])


def test_parser_validation_failure_is_controlled(monkeypatch):
    monkeypatch.setenv('ZHIPU_API_KEY','test-key')
    async def invalid(_client,_messages):return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{not-json'))])
    monkeypatch.setattr('app.editable_replan._parser_completion',invalid)
    with pytest.raises(ReplanError) as raised:
        asyncio.run(_parse_instruction('今天轻松一点',{'stops':[]},[],3,1))
    assert raised.value.code=='REPLAN_INSTRUCTION_PARSE_FAILED' and raised.value.status_code==422


def test_cross_day_preview_does_not_commit_until_confirmed(client):
    job_id=job(client);before=client.get(f'/api/build/{job_id}/result').json()
    first_day=before['itinerary'][0];places={place['id']:place for place in before['places']}
    anchor=first_day['stops'][0]['place_id'];name=places[anchor]['display_name']
    response=client.post(f'/api/trips/{job_id}/days/1/replan-preview',json={
        'instruction':f'{name}安排一整天，其他景点放到别天'})
    assert response.status_code==200,response.text
    notice=response.json();assert notice['code']=='REPLAN_SCOPE_AFFECTED_DAYS'
    assert notice['user_title']=='这项调整会影响其他日期'
    preview=notice['details']['preview'];assert preview['scope']=='affected_days'
    assert preview['affected_day_ids'][0]==1 and len(preview['affected_day_ids'])>1
    unchanged=client.get(f'/api/build/{job_id}/result').json()
    assert unchanged['itinerary']==before['itinerary']
    confirmed=client.post(f'/api/trips/{job_id}/replan-previews/{preview["preview_id"]}/confirm')
    assert confirmed.status_code==200,confirmed.text
    after=client.get(f'/api/build/{job_id}/result').json()
    affected=set(preview['affected_day_ids'])
    for number in range(1,len(before['itinerary'])+1):
        if number not in affected:assert after['itinerary'][number-1]==before['itinerary'][number-1]
    undone=client.post(f'/api/trips/{job_id}/days/1/undo')
    assert undone.status_code==200,undone.text
    restored=client.get(f'/api/build/{job_id}/result').json()
    assert restored['itinerary']==before['itinerary']


def test_replan_business_error_uses_user_message_contract(client,monkeypatch):
    job_id=job(client)
    async def unavailable(*_args,**_kwargs):raise ReplanError('REPLAN_NO_FEASIBLE_SCHEDULE','受影响日期没有可用容量',422)
    monkeypatch.setattr('app.main.instruction_preview',unavailable)
    response=client.post(f'/api/trips/{job_id}/days/1/replan-preview',json={'instruction':'把这些地点放到其他天'})
    assert response.status_code==422
    detail=response.json()['detail']
    assert detail['code']=='REPLAN_NO_FEASIBLE_SCHEDULE'
    assert detail['user_title']=='暂时无法按这个方式安排'
    assert detail['details']==[{'type':'reason','message':'受影响日期没有可用容量'}]
    assert '原行程没有变化' in detail['user_message']


def test_unknown_replan_error_is_sanitized_and_persisted(client,monkeypatch):
    job_id=job(client)
    async def broken(*_args,**_kwargs):raise RuntimeError('sensitive traceback text')
    monkeypatch.setattr('app.main.instruction_preview',broken)
    response=client.post(f'/api/trips/{job_id}/days/1/replan-preview',json={'instruction':'调整这一天'})
    assert response.status_code==500
    detail=response.json()['detail']
    assert detail['code']=='REPLAN_INTERNAL_ERROR' and detail['user_title']=='暂时无法生成调整预览'
    assert 'traceback' not in detail['user_message'].lower()
    events=client.get(f'/api/debug/build/{job_id}/errors').json()['errors']
    assert any(item['exception_type']=='RuntimeError' for item in events)


def test_dedicated_day_relative_references_are_day_scoped():
    catalog=[{'place_id':'disney','name':'上海迪士尼乐园','arrival_time':'09:00'},
             {'place_id':'museum','name':'上海博物馆','arrival_time':'14:00'}]
    deleted=resolve_scope('上海迪士尼乐园安排一天，其余行程删除',
        parse_replan_deterministic('上海迪士尼乐园安排一天，其余行程删除',catalog,2,6),2,6)
    assert deleted.scope=='current_day'
    assert any(action.type=='dedicate_day' and action.place_ref=='disney' and not action.redistribute_removed_stops for action in deleted.actions)
    assert any(action.type=='remove_place' and action.selector=='current_day_remaining' for action in deleted.actions)
    redistributed=resolve_scope('上海迪士尼乐园安排一天，其余行程放到其他天',
        parse_replan_deterministic('上海迪士尼乐园安排一天，其余行程放到其他天',catalog,2,6),2,6)
    assert redistributed.scope=='affected_days' and any(action.type=='dedicate_day' and action.redistribute_removed_stops for action in redistributed.actions)
    whole=resolve_scope('整趟旅行除了迪士尼其他都删除',
        parse_replan_deterministic('整趟旅行除了迪士尼其他都删除',catalog,2,6),2,6)
    assert whole.scope=='whole_trip'


def test_dedicated_day_keeps_supporting_activities_and_only_changes_current_day(client):
    job_id=job(client);profile=client.get(f'/api/build/{job_id}/result').json();places={item['id']:item for item in profile['places']}
    day=profile['itinerary'][0];target=next(stop for stop in day['stops'] if places[stop['place_id']]['type']!='restaurant')
    name=places[target['place_id']]['display_name']
    response=client.post(f'/api/trips/{job_id}/days/1/replan-preview',json={'instruction':f'{name}安排一天，其余行程删除'})
    assert response.status_code==200,response.text
    preview=response.json();assert preview['scope']=='current_day'
    dedicated=next(value for value in preview['parsed_constraints']['actions'] if value['type']=='dedicate_day')
    assert dedicated['place_ref']==target['place_id'] and dedicated['redistribute_removed_stops'] is False
    removed={item['place_id'] for item in preview['diff']['removed']}
    assert all(places[pid]['type']!='restaurant' for pid in removed)
    assert preview['affected_day_ids']==[1]


def test_dedicated_place_owned_by_another_day_requires_affected_days_preview(client):
    job_id=job(client);profile=client.get(f'/api/build/{job_id}/result').json();places={item['id']:item for item in profile['places']}
    target=profile['itinerary'][1]['stops'][0];name=places[target['place_id']]['display_name']
    response=client.post(f'/api/trips/{job_id}/days/1/replan-preview',json={'instruction':f'{name}安排一天，其余行程删除'})
    assert response.status_code==200,response.text
    notice=response.json();assert notice['code']=='REPLAN_SCOPE_AFFECTED_DAYS'
    assert set(notice['affected_day_ids'])=={1,2}
    preview=notice['details']['preview']
    dedicated=next(value for value in preview['parsed_constraints']['actions'] if value['type']=='dedicate_day')
    assert dedicated['place_ref']==target['place_id'] and dedicated['redistribute_removed_stops'] is False


def test_unowned_canonical_dedicated_place_is_added_without_provider_call():
    disney={'id':'amap-disney','display_name':'上海迪士尼乐园','local_name':'上海迪士尼乐园','city':'上海',
             'provider':'amap','provider_place_id':'B0DISNEY','latitude':31.14,'longitude':121.66,'type':'sight','place_type':'sight'}
    old={'id':'old-anchor','display_name':'原主景点','city':'上海','type':'sight','place_type':'sight'}
    state={'itinerary':[{'city':'上海','stops':[{'place_id':'old-anchor'}]}],
           'allocation':{'days':[{'day':1,'primary_anchor_id':'old-anchor','preferred_candidate_ids':[],'backup_candidate_ids':[]}],
                         'place_assignments':{'old-anchor':{'place_id':'old-anchor','day':1,'role':'primary','reservation':'hard','fixed':True}}},
           'plan':{'days':[{'primary_anchor_id':'old-anchor'}]},
           'edits':{1:{'stop_overrides':{},'day_transport':None,'segment_overrides':{}}}}
    parsed=SimpleNamespace(dedicated_place_query='上海迪士尼乐园',dedicated_place_id=None)
    place,trace=asyncio.run(_resolve_dedicated_place(state,None,1,parsed,[old,disney]))
    affected,owner=_promote_dedicated_place(state,1,place,[old,disney])
    assert trace['resolution_status']=='canonical_match' and owner is None and affected==[1]
    assert state['allocation']['days'][0]['primary_anchor_id']=='amap-disney'
    assert state['allocation']['place_assignments']['amap-disney']['role']=='primary'


def test_missing_dedicated_place_is_resolved_and_added_to_current_day(client,monkeypatch):
    job_id=job(client)
    candidate=AmapPoiCandidate(provider_place_id='B0THEME',name='杭州主题乐园',city='杭州',district='萧山区',
        address='测试大道1号',latitude=30.1,longitude=120.2,category='风景名胜;主题乐园')
    class Dummy:pass
    async def fake_search(_client,keyword,city,limit=10):
        assert keyword=='杭州主题乐园' and city=='杭州';return [candidate],False
    monkeypatch.setattr('app.editable_replan.AmapClient',lambda connect:Dummy())
    monkeypatch.setattr('app.editable_replan.search_pois',fake_search)
    response=client.post(f'/api/trips/{job_id}/days/1/replan-preview',json={
        'instruction':'杭州主题乐园安排一天，其余行程删除'})
    assert response.status_code==200,response.text
    preview=response.json();assert preview['scope']=='current_day'
    assert any(value['type']=='dedicate_day' and value.get('place_query')=='杭州主题乐园' for value in preview['parsed_constraints']['actions'])
    assert any(item['place_id']=='amap-B0THEME' for item in preview['diff']['added'])
