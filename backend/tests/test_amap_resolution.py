import asyncio
import json
import sqlite3
from contextlib import contextmanager

import httpx

from app.migrations import migrate
from app.place_resolution import (PlaceResolver,ResolutionContext,build_search_queries,canonical_place_id,
                                  normalize_name,score_candidate)
from app.services.amap import AmapClient,AmapPoiCandidate,get_poi_detail,search_pois
from app.services.amap.normalizer import normalize_poi
from tests.test_place_names import provider_place


def response_poi(name='正宁路夜市',poi_id='B0REALPOI1',category='餐饮服务;中餐厅'):
    return {'id':poi_id,'name':name,'cityname':'兰州市','adname':'城关区','address':'正宁路附近',
            'location':'103.825100,36.056900','type':category,'typecode':'050100','adcode':'620102','citycode':'0931'}


def client(handler,connect=None):
    return AmapClient('test-key','https://restapi.amap.com/v3',connect,httpx.MockTransport(handler))


def local_db(tmp_path):
    path=tmp_path/'amap.db';migrate(path)
    @contextmanager
    def connect():
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    return connect


def test_search_and_detail_use_poi_v2_business_fields():
    requests=[]
    async def handler(request):
        requests.append(request)
        poi=response_poi();poi['business']={'opentime_today':'09:00-17:00','opentime_week':'周二至周日 09:00-17:00',
            'tel':'021-12345678','rating':'4.8','cost':'80','business_area':'人民广场','tag':['博物馆','亲子']}
        payload={'status':'1','info':'OK','infocode':'10000','pois':[poi]}
        return httpx.Response(200,json=payload)
    amap=client(handler)
    candidates,cached=asyncio.run(search_pois(amap,'正宁路夜市','兰州'))
    detail,_=asyncio.run(get_poi_detail(amap,'B0REALPOI1'))
    assert not cached and candidates[0].name=='正宁路夜市'
    assert candidates[0].latitude==36.0569 and candidates[0].longitude==103.8251
    assert detail and detail.provider_place_id=='B0REALPOI1'
    assert detail.opening_hours_today=='09:00-17:00'
    assert detail.opening_hours_weekly=='周二至周日 09:00-17:00'
    assert detail.phone=='021-12345678' and detail.provider_rating==4.8 and detail.provider_cost==80
    assert all(request.url.path.startswith('/v5/place/') for request in requests)
    assert all(request.url.params.get('show_fields')=='business' for request in requests)
    assert requests[0].url.params.get('region')=='兰州' and requests[1].url.params.get('id')=='B0REALPOI1'


def test_english_alias_binds_through_chinese_local_name_and_persists(tmp_path):
    calls=[]
    async def handler(request):
        calls.append(request);return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':[response_poi()]})
    connect=local_db(tmp_path);place=provider_place();place['semantic_tags']=['restaurant']
    resolver=PlaceResolver(connect,'job-1','places-core',client(handler,connect))
    result=asyncio.run(resolver.resolve_place(place))
    assert result.status=='exact_match' and result.confidence>=.85
    assert place['id']==canonical_place_id('amap','B0REALPOI1')
    assert place['provider']=='amap' and place['provider_place_id']=='B0REALPOI1'
    assert place['display_name']=='正宁路夜市' and place['english_name']=='Zhengning Road Night Market'
    assert place['address']=='正宁路附近' and place['verification_status']=='verified'
    with connect() as db:
        assert db.execute('select count(*) n from canonical_places').fetchone()['n']==1
        assert db.execute('select status from place_resolutions').fetchone()['status']=='exact_match'
    # A second resolution of the same query is served by the persistent cache.
    other=provider_place();asyncio.run(resolver.resolve_place(other))
    assert len(calls)==2


def test_ambiguous_candidates_are_not_auto_bound():
    async def handler(request):
        values=[response_poi('中山桥景区','A'),response_poi('中山桥','B')]
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':values})
    place=provider_place();place.update(local_name='中山桥公园',display_name='Zhongshan Bridge Park')
    result=asyncio.run(PlaceResolver(client=client(handler)).resolve_place(place))
    assert result.status in ('ambiguous','multiple_candidates','probable_match','no_match')
    if result.status in ('ambiguous','multiple_candidates'):assert place['provider']=='llm_generated'


def test_context_resolves_restaurant_branch_near_day_cluster():
    async def handler(request):
        values=[
            {**response_poi('上海老饭店(豫园店)','central'), 'cityname':'上海市','adname':'黄浦区','location':'121.490675,31.227625'},
            {**response_poi('上海老饭店(环城路店)','remote'), 'cityname':'上海市','adname':'松江区','location':'121.255557,31.005942'},
        ]
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':values})
    place=provider_place();place.update(local_name='上海老饭店',display_name='上海老饭店',city='上海',type='restaurant')
    context=ResolutionContext(previous_coordinates=(31.208454,121.469276),next_coordinates=(31.233516,121.492127),
                              day_cluster_coordinates=[(31.208454,121.469276),(31.233516,121.492127)],area_labels=['黄浦区'])
    result=asyncio.run(PlaceResolver(client=client(handler)).resolve_place(place,context))
    assert result.status=='context_resolved'
    assert result.selected_provider_place_id=='central'
    assert place['display_name']=='上海老饭店(豫园店)' and place['district']=='黄浦区'


def test_context_can_choose_representative_for_same_scenic_complex():
    async def handler(request):
        values=[
            {**response_poi('田子坊东部旅游区','east','风景名胜;旅游景点'), 'cityname':'上海市','adname':'黄浦区','location':'121.469276,31.208454'},
            {**response_poi('田子坊中部旅游区','middle','风景名胜;旅游景点'), 'cityname':'上海市','adname':'黄浦区','location':'121.468819,31.207945'},
        ]
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':values})
    place=provider_place();place.update(local_name='田子坊',display_name='田子坊',city='上海',type='sight')
    context=ResolutionContext(previous_coordinates=(31.227625,121.490675),day_cluster_coordinates=[(31.227625,121.490675)])
    result=asyncio.run(PlaceResolver(client=client(handler)).resolve_place(place,context))
    assert result.status=='context_resolved' and result.selected_provider_place_id in {'east','middle'}
    assert place['provider']=='amap' and place['latitude'] is not None


def test_generated_experience_is_never_forced_to_unrelated_poi():
    async def handler(request):
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':[]})
    place=provider_place('experience');place.update(local_name='兰州传统小吃制作工坊',display_name='Lanzhou Traditional Snacks Making Workshop')
    result=asyncio.run(PlaceResolver(client=client(handler)).resolve_place(place))
    assert result.status=='generated_experience'
    assert place['place_type']=='generated_experience' and place['provider_place_id'] is None
    assert place['verification_status']=='generated' and place['map_url'] is None


def test_workshop_title_does_not_bind_to_similar_restaurant():
    async def handler(request):
        poi=response_poi('兰放甜醅子奶茶',category='餐饮服务;冷饮店;冷饮店')
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':[poi]})
    place=provider_place('experience')
    place.update(local_name='兰州甜醅子制作体验',display_name='兰州甜醅子制作体验',experience_title='兰州甜醅子制作体验')
    result=asyncio.run(PlaceResolver(client=client(handler)).resolve_place(place))
    assert result.status=='generated_experience'
    assert result.selected_place_id is None
    assert place['place_type']=='generated_experience'
    assert place['provider_place_id'] is None


def test_provider_error_is_a_warning_state_not_an_exception():
    async def handler(request):return httpx.Response(503,text='unavailable')
    place=provider_place();resolver=PlaceResolver(client=client(handler))
    results,warnings=asyncio.run(resolver.resolve_places([place],'places-core'))
    assert results[0].status=='provider_error' and warnings[0]['code']=='POI_PROVIDER_ERROR'
    assert place['verification_status']=='unverified'


def test_scoring_rejects_wrong_city_and_category():
    good=AmapPoiCandidate.model_validate(normalize_poi(response_poi(poi_id='A')))
    bad=good.model_copy(update={'provider_place_id':'B','city':'北京市','category':'汽车维修'})
    place=provider_place();place['type']='restaurant';place['semantic_tags']=['restaurant']
    assert score_candidate(place,good).score>score_candidate(place,bad).score


def test_city_prefix_alias_is_preferred_over_looser_similar_name():
    place=provider_place();place['semantic_tags']=['food_experience']
    exactish=AmapPoiCandidate.model_validate(normalize_poi(response_poi('兰州市正宁路夜市','A')))
    loose=AmapPoiCandidate.model_validate(normalize_poi(response_poi('正宁路小吃夜市','B')))
    assert score_candidate(place,exactish).score>score_candidate(place,loose).score


def test_entity_suffix_is_not_erased_and_provider_alias_supports_landmark_match():
    assert normalize_name('东方明珠')!=normalize_name('东方明珠公园')
    tower=response_poi('东方明珠广播电视塔','tower','风景名胜;风景名胜;国家级景点')
    tower['business']={'alias':'东方明珠塔'}
    park=response_poi('东方明珠公园','park','风景名胜;公园广场;公园')
    place=provider_place();place.update(local_name='东方明珠',display_name='东方明珠',type='sight')
    tower_score=score_candidate(place,AmapPoiCandidate.model_validate(normalize_poi(tower))).score
    park_score=score_candidate(place,AmapPoiCandidate.model_validate(normalize_poi(park))).score
    assert tower_score>park_score


def test_search_queries_use_exact_district_alias_and_city_fallbacks():
    place=provider_place();place.update(local_name='南翔馒头店',canonical_name='南翔馒头店(豫园店)',
        display_name='南翔馒头店',english_name='Nanxiang Steamed Bun Restaurant',district='黄浦区',city='上海')
    queries=build_search_queries(place)
    assert queries[0]=='南翔馒头店'
    assert '南翔馒头店 黄浦区' in queries
    assert any('南翔馒头店(豫园店)' in query for query in queries)


def test_close_brand_branches_without_context_remain_ambiguous():
    async def handler(request):
        values=[response_poi('海底捞',poi_id='A'),response_poi('海底捞',poi_id='B')]
        values[0]['location']='121.48,31.22';values[1]['location']='121.52,31.25'
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':values})
    place=provider_place();place.update(local_name='海底捞',display_name='海底捞',city='上海',type='restaurant',district=None)
    result=asyncio.run(PlaceResolver(client=client(handler)).resolve_place(place))
    assert result.status=='ambiguous' and result.selected_place_id is None
