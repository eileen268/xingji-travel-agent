import asyncio
import sqlite3
from contextlib import contextmanager

import httpx

from app.migrations import migrate
from app.models import BuildInput
from app.scheduler import _meal_schedule_issues,schedule_day_with_routes
from app.scheduler_config import MEAL_WINDOWS,PACE_CONFIG
from app.services.amap import AmapClient,AmapRouteService,RouteResult
from app.validators import validate_day


def local_db(tmp_path):
    path=tmp_path/'route.db';migrate(path)
    @contextmanager
    def connect():
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    return connect


def amap(handler,connect=None):
    return AmapClient('test-key','https://restapi.amap.com/v3',connect,httpx.MockTransport(handler))


def place(place_id,name,lat,lng,kind='sight'):
    return {'id':place_id,'display_name':name,'latitude':lat,'longitude':lng,'type':kind}


def trip(outbound='morning',return_period='evening',pace='balanced'):
    return BuildInput.model_validate({'origin':'北京','destinations':['兰州'],'mainDestination':'兰州',
      'startDate':'2026-10-10','endDate':'2026-10-10','adults':2,'children':0,'seniors':0,
      'travelStyle':'朋友出行','budget':{'total':5000},'preferences':{'pace':pace,'localTransport':['transit']},
      'outboundPeriod':outbound,'returnPeriod':return_period})


def test_amap_walking_driving_and_transit_are_normalized_and_cached(tmp_path):
    calls=[]
    async def handler(request):
        calls.append(request.url.path)
        if 'transit' in request.url.path:
            route={'transits':[{'distance':'2400','duration':'1200'}]}
        else:
            route={'paths':[{'distance':'1500','duration':'900','strategy':'fast'}]}
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','route':route})
    connect=local_db(tmp_path);service=AmapRouteService(amap(handler,connect),connect)
    a=place('a','A',36.05,103.80);b=place('b','B',36.07,103.84)
    for mode in ('walking','driving','transit'):
        result=asyncio.run(service.route(a,b,mode,'兰州'))
        assert result.verification_status=='verified' and result.provider=='amap'
        assert result.distance_meters>0 and result.duration_seconds>0
        cached=asyncio.run(service.route(a,b,mode,'兰州'))
        assert cached.cache_hit is True
    assert len(calls)==3
    with connect() as db:assert db.execute('select count(*) n from routes').fetchone()['n']==3


def test_route_provider_failure_uses_coordinate_estimate(tmp_path):
    async def handler(request):return httpx.Response(503,text='unavailable')
    connect=local_db(tmp_path);service=AmapRouteService(amap(handler,connect),connect)
    result=asyncio.run(service.route(place('a','A',36.05,103.80),place('b','B',36.07,103.84),'transit','兰州'))
    assert result.verification_status=='estimated_by_distance'
    assert result.provider=='deterministic_estimate' and result.distance_meters>0 and result.duration_seconds>0
    assert result.warning and '保守估算' in result.warning


def test_missing_coordinates_is_unresolved_without_provider_call(tmp_path):
    calls=[]
    async def handler(request):calls.append(request);return httpx.Response(200,json={})
    connect=local_db(tmp_path);service=AmapRouteService(amap(handler,connect),connect)
    result=asyncio.run(service.route(place('a','A',None,None),place('b','B',36.07,103.84),'walking','兰州'))
    assert result.verification_status=='unresolved' and result.distance_meters is None
    assert result.duration_seconds is None and result.route_error=='ROUTE_INPUT_INVALID'
    assert not calls


def test_zero_provider_route_is_rejected_and_replaced_by_positive_estimate(tmp_path):
    async def handler(request):
        key='transits' if 'transit' in request.url.path else 'paths'
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','route':{key:[{'distance':'0','duration':'0'}]}})
    connect=local_db(tmp_path);service=AmapRouteService(amap(handler,connect),connect)
    result=asyncio.run(service.route(place('a','A',36.05,103.80),place('b','B',36.07,103.84),'driving','兰州'))
    assert result.verification_status=='estimated_by_distance'
    assert result.duration_seconds>0 and result.distance_meters>0
    assert result.route_error=='INVALID_ROUTE_DURATION'


def test_unresolved_route_remains_null_in_schedule_and_never_becomes_zero():
    places=[place('anchor','主景点',None,None),place('meal','固定餐厅',36.04,103.78,'restaurant')]
    outline={'date':'2026-10-10','city':'兰州','primary_anchor_id':'anchor','candidate_place_ids':[],
             'backup_candidate_ids':[],'fixed_meal_stop_id':'meal','area_labels':[]}
    decision={'optional_stop_order':[],'day_intent':'缺少坐标测试',
              'practical_notes':['缺少坐标时保留未知路线状态并使用内部调度缓冲。']}
    day=asyncio.run(schedule_day_with_routes(decision,outline,places,trip(),0))
    leg=day['stops'][1]
    segment=day['segments'][0]
    assert leg['route_status']=='unresolved'
    assert leg['transfer_minutes'] is None and leg['distance_km'] is None
    assert leg['route_minutes'] is None and leg['fallback_schedule_minutes']==30
    assert leg['buffer_minutes']==10 and leg['idle_minutes']==20  # meal window wait only; fallback is accounted separately
    assert leg['route_error_code']=='ROUTE_INPUT_INVALID'
    assert segment['route_status']=='unresolved' and segment['duration_minutes'] is None
    assert segment['fallback_schedule_minutes']==30 and segment['buffer_minutes']==10
    assert leg['arrival_time']>'10:30'  # Internal fallback affects scheduling, not displayed route facts.


def test_day_validator_rejects_zero_route_facts_with_specific_codes():
    places=[place('anchor','主景点',36.0,103.7),place('meal','固定餐厅',36.04,103.78,'restaurant')]
    day={'date':'2026-10-10','city':'兰州','theme':'测试','summary':'测试路线数据是否合法。',
         'periods':{key:{'title':'安排','description':'测试安排。'} for key in ('morning','afternoon','evening')},
         'stops':[{'place_id':'anchor','arrival_time':'09:00','dwell_minutes':60,'transport_mode':'当天首站',
             'transfer_minutes':None,'distance_km':None,'estimated_cost':None,'route_status':'not_applicable',
             'route_provider':None,'route_queried_at':None,'cost_note':'费用待复核。','practical_note':'现场信息出发前请复核。','time_guard':'预留缓冲。'},
            {'place_id':'meal','arrival_time':'10:30','dwell_minutes':60,'transport_mode':'驾车或出租车',
             'transfer_minutes':0,'distance_km':0,'estimated_cost':None,'route_status':'verified','route_provider':'amap',
             'route_queried_at':'2026-09-16T00:00:00+00:00','cost_note':'费用待复核。','practical_note':'现场信息出发前请复核。','time_guard':'预留缓冲。'}],
         'photo_advice':[{'title':'提示','note':'遵守现场规则。'}],
         'total_travel_minutes':0,'total_visit_minutes':120,'schedule_warnings':[],'density_evaluation':None}
    errors=validate_day(day,places,expected_date='2026-10-10',expected_city='兰州',required_ids=('anchor','meal'))
    assert {'INVALID_ROUTE_DURATION','INVALID_ROUTE_DISTANCE'} <= {item['code'] for item in errors}


def test_scheduler_injects_fixed_stops_uses_routes_and_persists_run(tmp_path):
    class Routes:
        def __init__(self):self.calls=[]
        async def route(self,origin,destination,mode,city):
            self.calls.append((origin['id'],destination['id'],mode,city))
            return RouteResult(origin_place_id=origin['id'],destination_place_id=destination['id'],mode=mode,
                distance_meters=2400,duration_seconds=1200,provider='amap',verification_status='verified',
                queried_at='2026-09-15T00:00:00+00:00')
    places=[place('anchor','主景点',36.00,103.70),place('near','附近景点',36.01,103.71),
            place('far','较远景点',36.10,103.90),place('meal','固定餐厅',36.04,103.78,'restaurant')]
    outline={'date':'2026-10-10','city':'兰州','primary_anchor_id':'anchor','candidate_place_ids':['far','near'],
             'backup_candidate_ids':[],'fixed_meal_stop_id':'meal','area_labels':['城关区']}
    decision={'optional_stop_order':['far','near'],'day_intent':'真实路线测试',
              'practical_notes':['根据当天路线和现场情况调整停留并保留充足交通缓冲。']}
    connect=local_db(tmp_path);routes=Routes()
    day=asyncio.run(schedule_day_with_routes(decision,outline,places,trip(),0,routes,connect,'job-1'))
    ids=[stop['place_id'] for stop in day['stops']]
    assert ids[0]=='anchor' and 'meal' in ids
    assert all(stop['route_status']=='verified' for stop in day['stops'][1:])
    assert all(stop['transfer_minutes']==20 and stop['distance_km']==2.4 for stop in day['stops'][1:])
    assert all(segment['route_status']=='verified' and segment['duration_minutes']==20 and segment['distance_meters']==2400
               for segment in day['segments'])
    assert [(segment['from_place_id'],segment['to_place_id']) for segment in day['segments']]==list(zip(ids,ids[1:]))
    assert day['total_travel_minutes']==20*(len(day['stops'])-1)
    for previous,current in zip(day['stops'],day['stops'][1:]):
        ph,pm=map(int,previous['arrival_time'].split(':'));ch,cm=map(int,current['arrival_time'].split(':'))
        assert ch*60+cm>=ph*60+pm+previous['dwell_minutes']
    with connect() as db:
        row=db.execute('select status,payload from schedule_runs where job_id=?',('job-1',)).fetchone()
        assert row['status']=='completed' and row['payload']


def test_scheduler_drops_optional_stop_when_return_window_is_tight():
    places=[place('anchor','主景点',36.00,103.70),place('optional','可选景点',36.10,103.90),
            place('meal','固定餐厅',36.04,103.78,'restaurant')]
    outline={'date':'2026-10-10','city':'兰州','primary_anchor_id':'anchor','candidate_place_ids':['optional'],
             'backup_candidate_ids':[],'fixed_meal_stop_id':'meal','area_labels':[]}
    decision={'optional_stop_order':['optional'],'day_intent':'早返测试','practical_notes':['根据返程时间压缩可选安排并优先保留固定地点。']}
    day=asyncio.run(schedule_day_with_routes(decision,outline,places,trip(return_period='morning'),0))
    assert 'optional' not in [stop['place_id'] for stop in day['stops']]
    assert day['schedule_warnings']


def density_places(far=False):
    delta=.50 if far else .006
    values=[place('anchor','核心博物馆',31.230,121.470),place('meal','本地午餐',31.235,121.475,'restaurant')]
    for index in range(1,5):
        item=place(f'p{index}',f'候选景点{index}',31.230+delta*index,121.470+delta*index)
        item.update(city='上海',district='黄浦区',verification_status='verified',description='黄浦区代表性景点',
                    interest_affinity={'history_culture':.7 if index==1 else .2},semantic_tags=['museum'])
        values.append(item)
    for item in values:
        item.setdefault('city','上海');item.setdefault('district','黄浦区');item.setdefault('verification_status','verified')
        item.setdefault('description','上海本地地点');item.setdefault('interest_affinity',{});item.setdefault('semantic_tags',[])
    return values


def density_outline():
    return {'date':'2026-10-10','city':'上海','primary_anchor_id':'anchor',
            'candidate_place_ids':['p1','p2'],'backup_candidate_ids':['p3','p4'],
            'fixed_meal_stop_id':'meal','area_labels':['黄浦区']}


def density_decision(selected=None):
    return {'optional_stop_order':selected or [],'day_intent':'城市文化一日安排',
            'practical_notes':['按照地点距离、当天节奏和现场开放情况安排，并保留交通缓冲。']}


def density_trip(pace='balanced',outbound='morning',return_period='evening'):
    value=trip(outbound,return_period,pace)
    value.destinations=['上海'];value.mainDestination='上海';value.preferences.interests=['历史文化']
    return value


def test_density_targets_are_configuration_owned():
    assert (PACE_CONFIG['relaxed'].target_utilization_min,PACE_CONFIG['relaxed'].target_utilization_max)==(.55,.70)
    assert (PACE_CONFIG['balanced'].target_utilization_min,PACE_CONFIG['balanced'].target_utilization_max)==(.65,.80)
    assert (PACE_CONFIG['intensive'].target_utilization_min,PACE_CONFIG['intensive'].target_utilization_max)==(.75,.90)
    assert [PACE_CONFIG[p].max_major_stops for p in ('relaxed','balanced','intensive')]==[3,4,5]


def test_balanced_full_day_fill_pass_adds_candidates_and_records_metrics():
    day=asyncio.run(schedule_day_with_routes(density_decision(),density_outline(),density_places(),density_trip(),0))
    density=day['density_evaluation']
    assert density['fill_attempt_count'] in (1,2)
    assert density['added_place_ids']
    assert density['stop_count']>2 and density['attraction_count']<=4
    assert density['available_minutes']==660
    assert density['scheduled_minutes']==density['visit_minutes']+density['meal_minutes']+density['total_route_minutes']+density['configured_buffer_minutes']


def test_late_arrival_uses_reduced_window_without_underfilled_issue():
    day=asyncio.run(schedule_day_with_routes(density_decision(),density_outline(),density_places(),
                                             density_trip(outbound='evening'),0))
    density=day['density_evaluation']
    assert density['reduced_window'] is True and density['available_minutes']==180
    assert not any(code.startswith('UNDERFILLED_DAY') for code in density['issues'])
    assert density['fill_attempt_count']==0


def test_relaxed_fill_pass_respects_major_stop_limit():
    day=asyncio.run(schedule_day_with_routes(density_decision(),density_outline(),density_places(),density_trip('relaxed'),0))
    density=day['density_evaluation']
    assert density['attraction_count']<=PACE_CONFIG['relaxed'].max_major_stops
    assert density['fill_attempt_count']<=2


def test_far_candidates_are_not_forced_into_underfilled_day():
    day=asyncio.run(schedule_day_with_routes(density_decision(),density_outline(),density_places(far=True),density_trip(),0))
    density=day['density_evaluation']
    assert density['added_place_ids']==[]
    assert 'UNDERFILLED_DAY_NO_GOOD_CANDIDATE' in density['issues']
    assert any(warning['code']=='UNDERFILLED_DAY_NO_GOOD_CANDIDATE' for warning in day['schedule_warnings'])


def test_long_tail_gap_triggers_fill_before_final_warning():
    day=asyncio.run(schedule_day_with_routes(density_decision(),density_outline(),density_places(),density_trip(),0))
    density=day['density_evaluation']
    assert density['added_place_ids']
    assert density['fill_attempt_count']<=2
    assert density['longest_idle_gap_minutes']<density['available_minutes']


class PairRoutes:
    def __init__(self,durations):
        self.durations=durations;self.calls=[]
    async def route(self,origin,destination,mode,city):
        self.calls.append((origin['id'],destination['id'],mode))
        minutes=self.durations.get((origin['id'],destination['id']),20)
        return RouteResult(origin_place_id=origin['id'],destination_place_id=destination['id'],mode=mode,
            distance_meters=max(500,minutes*300),duration_seconds=minutes*60,provider='amap',
            verification_status='verified',queried_at='2026-09-20T00:00:00+00:00')


def meal_case():
    values=[place('century-park','上海世纪公园',31.215,121.544),
            place('botanical-garden','上海植物园',31.147,121.445),
            place('seafood','海之味海鲜餐厅',31.190,121.500,'restaurant')]
    for item in values:item.update(city='上海',district='浦东新区',verification_status='verified')
    outline={'date':'2026-10-10','city':'上海','primary_anchor_id':'century-park',
        'candidate_place_ids':['botanical-garden'],'backup_candidate_ids':[],
        'fixed_meal_stop_id':'seafood','area_labels':['浦东新区']}
    decision={'optional_stop_order':['botanical-garden'],'day_intent':'上海公园与植物主题',
        'practical_notes':['按照真实路线、正常用餐时段和现场开放情况安排。']}
    routes=PairRoutes({('century-park','botanical-garden'):96,('botanical-garden','seafood'):30,
                       ('century-park','seafood'):20,('seafood','botanical-garden'):30})
    return values,outline,decision,routes


def test_meal_windows_are_configuration_owned():
    lunch=MEAL_WINDOWS['lunch'];dinner=MEAL_WINDOWS['dinner']
    assert (lunch.preferred_start,lunch.preferred_end,lunch.acceptable_start,lunch.acceptable_end)==(690,810,660,840)
    assert (dinner.preferred_start,dinner.preferred_end,dinner.acceptable_start,dinner.acceptable_end)==(1050,1170,1020,1230)


def test_meal_aware_scheduler_moves_lunch_before_long_attraction_route():
    places,outline,decision,routes=meal_case()
    day=asyncio.run(schedule_day_with_routes(decision,outline,places,density_trip(),0,routes))
    ids=[stop['place_id'] for stop in day['stops']]
    assert ids.index('seafood')<ids.index('botanical-garden')
    meal=next(stop for stop in day['stops'] if stop['place_id']=='seafood')
    assert meal['meal_role']=='lunch'
    assert (meal['meal_window_preferred_start'],meal['meal_window_preferred_end'])==('11:30','13:30')
    assert '11:30'<=meal['arrival_time']<='13:30'
    assert meal['meal_window_status']=='preferred' and meal['meal_timing_penalty']==0
    assert '比较了' in meal['meal_timing_reason'] and '用餐窗口' in meal['meal_timing_reason']


def test_late_arrival_uses_dinner_role_without_forcing_lunch():
    places,outline,decision,routes=meal_case()
    day=asyncio.run(schedule_day_with_routes(decision,outline,places,density_trip(outbound='evening'),0,routes))
    meal=next(stop for stop in day['stops'] if stop['place_id']=='seafood')
    assert meal['meal_role']=='dinner'
    assert meal['meal_window_preferred_start']=='17:30'
    assert not any(warning['code']=='MEAL_MISSING' for warning in day['schedule_warnings'])


def test_explicit_late_lunch_preference_overrides_default_window():
    places,outline,decision,routes=meal_case();custom=density_trip()
    custom.notes='\u6211\u5e0c\u671b 14:30 \u518d\u5403\u5348\u996d'
    day=asyncio.run(schedule_day_with_routes(decision,outline,places,custom,0,routes))
    meal=next(stop for stop in day['stops'] if stop['place_id']=='seafood')
    assert meal['meal_window_preferred_start']=='14:30'
    assert meal['arrival_time']>='14:30' and meal['meal_window_status']=='preferred'


def test_verified_restaurant_opening_hours_shift_meal_within_window():
    places,outline,decision,routes=meal_case()
    restaurant=next(item for item in places if item['id']=='seafood')
    restaurant['official_facts']={'opening_hours':{
        'status':'provider_verified','value':{'weekly':'12:00-14:00'},
        'sources':[{'source_type':'amap','value':{'weekly':'12:00-14:00'}}]}}
    day=asyncio.run(schedule_day_with_routes(decision,outline,places,density_trip(),0,routes))
    meal=next(stop for stop in day['stops'] if stop['place_id']=='seafood')
    assert meal['arrival_time']=='12:00' and meal['meal_window_status']=='preferred'
    assert not any(warning['code']=='MEAL_OPENING_HOURS_CONFLICT' for warning in day['schedule_warnings'])


def test_missing_meal_is_soft_warning_and_does_not_create_restaurant():
    places,outline,decision,routes=meal_case();outline['fixed_meal_stop_id']=None
    day=asyncio.run(schedule_day_with_routes(decision,outline,places,density_trip(),0,routes))
    assert all(stop['place_id']!='seafood' for stop in day['stops'])
    assert any(warning['code']=='MEAL_MISSING' for warning in day['schedule_warnings'])


def test_formal_meals_too_close_is_a_soft_quality_issue():
    warnings=_meal_schedule_issues([
        {'place_id':'breakfast','arrival_time':'09:00','meal_role':'breakfast'},
        {'place_id':'lunch','arrival_time':'10:30','meal_role':'lunch'},
    ],0)
    assert warnings[0]['code']=='MEALS_TOO_CLOSE' and warnings[0]['metadata']['gap_minutes']==90
