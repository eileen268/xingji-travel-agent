import asyncio
import json

import httpx

from app.place_resolution import PlaceResolver
from app.pre_schedule_resolution import (
    apply_resolution_mapping,enforce_pre_schedule_resolution,is_resolved_real_place,
)
from app.services.amap import AmapClient
from tests.test_place_names import provider_place
from tests.test_pack_timeouts import store_for


def amap_client(handler):
    return AmapClient('test-key','https://restapi.amap.com/v3',transport=httpx.MockTransport(handler))


def poi(name='上海老饭店',poi_id='SH001',district='黄浦区',address='福佑路242号'):
    return {'id':poi_id,'name':name,'cityname':'上海市','adname':district,'address':address,
            'location':'121.492100,31.229100','type':'餐饮服务;中餐厅;上海菜','typecode':'050101'}


def real_place(name='上海老饭店'):
    place=provider_place();place.update(id='llm_old_restaurant',type='restaurant',place_type='restaurant',city='上海',
        canonical_name=name,local_name=name,display_name=name,english_name=None,district='黄浦区',
        entity_kind='poi',experience_title=None,provider='llm_generated',provider_place_id=None,
        latitude=None,longitude=None,semantic_tags=['restaurant'])
    return place


def test_shanghai_old_restaurant_is_resolved_and_references_are_replaced():
    async def handler(request):
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':[poi()]})
    place=real_place();old=place['id']
    gate=asyncio.run(enforce_pre_schedule_resolution([place],PlaceResolver(client=amap_client(handler))))
    plan={'days':[{'primary_anchor_id':old,'candidate_place_ids':[old]}]}
    updated,=apply_resolution_mapping(gate.id_mapping,plan)
    assert is_resolved_real_place(place)
    assert place['provider_place_id']=='SH001' and place['latitude']==31.2291
    assert updated['days'][0]['primary_anchor_id']==place['id']
    assert old not in updated['days'][0]['candidate_place_ids']


def test_old_brand_english_alias_and_chinese_alias_use_provider_identity():
    async def handler(request):
        query=request.url.params.get('keywords','')
        result=poi('上海老饭店',poi_id='SH001') if query in ('上海老饭店','Shanghai Old Restaurant') else poi('老饭店',poi_id='SH002')
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':[result]})
    for canonical,local in [('Shanghai Old Restaurant','上海老饭店'),('上海老饭店（豫园店）','上海老饭店')]:
        place=real_place(local);place['canonical_name']=canonical
        gate=asyncio.run(enforce_pre_schedule_resolution([place],PlaceResolver(client=amap_client(handler))))
        assert gate.resolved==1 and place['provider_place_id']=='SH001'


def test_same_name_branches_require_district_evidence():
    async def handler(request):
        values=[poi(poi_id='HUANGPU',district='黄浦区'),poi(poi_id='PUDONG',district='浦东新区')]
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':values})
    place=real_place();gate=asyncio.run(enforce_pre_schedule_resolution([place],PlaceResolver(client=amap_client(handler))))
    assert gate.resolved==1 and place['provider_place_id']=='HUANGPU'
    ambiguous=real_place();ambiguous['district']=None
    gate=asyncio.run(enforce_pre_schedule_resolution([ambiguous],PlaceResolver(client=amap_client(handler))))
    assert gate.unresolved==1 and gate.warnings[0]['code']=='REAL_POI_UNRESOLVED'


def test_generated_experience_skips_amap_resolution():
    calls=0
    async def handler(request):
        nonlocal calls;calls+=1
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':[]})
    place=real_place('兰州传统小吃制作体验');place.update(type='experience',place_type='generated_experience',
        entity_kind='experience_concept',experience_title='兰州传统小吃制作体验',linked_place_id=None)
    gate=asyncio.run(enforce_pre_schedule_resolution([place],PlaceResolver(client=amap_client(handler))))
    assert gate.skipped_generated==1 and gate.attempted==0 and calls==0


def test_unresolved_real_place_is_explicit_and_keeps_llm_identity():
    async def handler(request):
        return httpx.Response(200,json={'status':'1','info':'OK','infocode':'10000','pois':[]})
    place=real_place();gate=asyncio.run(enforce_pre_schedule_resolution([place],PlaceResolver(client=amap_client(handler))))
    assert place['id'].startswith('llm_') and place['verification_status']=='unverified'
    assert place['source_metadata']['pre_schedule_resolution_status']=='REAL_POI_UNRESOLVED'
    assert gate.metrics()['real_poi_resolution_rate']==0


def test_reference_mapping_migrates_modules_and_reschedules_days(tmp_path):
    store,connect,_=store_for(tmp_path)
    old='llm_restaurant';new='amap_restaurant'
    before=store.expected_signature('itinerary-day-1',('itinerary-allocation',))
    with connect() as db:
        db.execute("""UPDATE job_packs SET status='valid',payload=?,source_payload=?,input_signature='old'
          WHERE job_id='j' AND pack_id='itinerary-day-1'""",
          (json.dumps({'stops':[{'place_id':old}],'segments':[{'from_place_id':'anchor','to_place_id':old}]}),
           json.dumps({'optional_stop_order':[old],'day_intent':'test','practical_notes':['test']})))
        db.execute("""UPDATE job_packs SET status='valid',payload=?,source_payload=?,input_signature='old'
          WHERE job_id='j' AND pack_id='modules-practical'""",
          (json.dumps({'food':{'dedicated_trip':[{'place_id':old}]}}),json.dumps({'place_id':old})))
        db.execute("""INSERT INTO place_resolutions
          (resolution_id,job_id,generated_place_id,generated_name,city,selected_place_id,provider_place_id,score,status,candidates_json,created_at)
          VALUES ('resolution','j',?,'测试餐厅','兰州',?,'AMAP',.9,'exact_match','[]','now')""",(old,new))
    after=store.expected_signature('itinerary-day-1',('itinerary-allocation',))
    assert before!=after
    store.apply_reference_mapping({old:new})
    with connect() as db:
        day=db.execute("SELECT status,payload,source_payload FROM job_packs WHERE job_id='j' AND pack_id='itinerary-day-1'").fetchone()
        module=db.execute("SELECT payload FROM job_packs WHERE job_id='j' AND pack_id='modules-practical'").fetchone()
    assert day['status']=='pending' and day['payload'] is None
    assert json.loads(day['source_payload'])['optional_stop_order']==[new]
    assert json.loads(module['payload'])['food']['dedicated_trip'][0]['place_id']==new
