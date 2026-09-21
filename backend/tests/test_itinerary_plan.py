import copy
import asyncio
import json
from types import SimpleNamespace
import pytest
from jsonschema import Draft202012Validator

from app.models import BuildInput, ItineraryPlanDecision
from app.offline import make_packs
from app.llm_chunks import day_city, build_chunks, ChunkRunner, _persist
from app.itinerary_plan import (
    build_day_pools, canonicalize_decision, normalize_day_candidate_pools, decision_schema, hydrate_decision,
    validate_decision, validate_actual_interest_coverage,
)
from app.day_planner import day_catalog, day_decision_schema, validate_day_decision, schedule_day

PREFS=dict(origin='上海',destinations=['杭州'],mainDestination='杭州',startDate='2026-10-02',endDate='2026-10-06',
           adults=2,children=0,seniors=0,travelStyle='朋友出游',budget={'total':8000},
           preferences={'pace':'balanced','interests':['人文古迹','摄影','自然风景']})


def fixtures():
    trip=BuildInput(**PREFS); places=make_packs(trip)['places-core']['places']; pools=build_day_pools(trip,places,day_city)
    return trip,places,pools


def valid_decision(pools):
    anchors=[pool['allowed_primary_anchor_ids'][i % len(pool['allowed_primary_anchor_ids'])] for i,pool in enumerate(pools)]
    return {'days':[{'area_labels':['同区慢行'],'primary_anchor_id':anchor,'candidate_place_ids':[],
                     'backup_candidate_ids':[],
                     'intent':'按区域与体力安排代表性地点'} for anchor in anchors]}


def test_primary_anchor_schema_and_pool_exclude_non_sights():
    trip,places,pools=fixtures(); schema=decision_schema(pools,trip.preferences.interests)
    allowed=schema['properties']['days']['prefixItems'][0]['properties']['primary_anchor_id']['enum']
    Draft202012Validator.check_schema(schema)
    assert allowed==pools[0]['allowed_primary_anchor_ids']
    assert allowed and all(next(p for p in places if p['id']==pid)['type']=='sight' for pid in allowed)
    assert not any(p['id'] in allowed for p in places if p['type'] in ('restaurant','experience','chain'))


def test_invalid_anchor_reports_unknown_restaurant_experience_and_other_city():
    trip,places,pools=fixtures()
    bad_values=[next(p['id'] for p in places if p['type']=='restaurant'),next(p['id'] for p in places if p['type']=='experience'),'unknown-id']
    foreign=copy.deepcopy(next(p for p in places if p['type']=='sight')); foreign['id']='foreign-sight'; foreign['city']='上海'; places.append(foreign)
    bad_values.append('foreign-sight')
    for value in bad_values:
        decision=valid_decision(pools); decision['days'][3]['primary_anchor_id']=value
        errors,_=validate_decision(decision,pools,places,trip.preferences.interests)
        error=next(e for e in errors if e['pointer']=='/days/3/primary_anchor_id')
        assert error['code']=='INVALID_PRIMARY_ANCHOR' and error['allowed_values']==pools[3]['allowed_primary_anchor_ids']


def test_llm_interest_declarations_are_removed_and_backend_field_is_empty_until_stops_exist():
    trip,places,pools=fixtures(); decision=valid_decision(pools)
    decision['days'][0]['primary_anchor_id']='s2'; decision['days'][0]['covered_interests']=['人文古迹']
    decision['days'][1]['primary_anchor_id']='s0'; decision['days'][1]['covered_interests']=['摄影']
    decision['days'][2]['primary_anchor_id']='s9'; decision['days'][2]['covered_interests']=['自然风景']
    decision['days'][3]['covered_interests']=[]
    decision['days'][0]['intent']='旧城建筑与地方历史'; decision['days'][1]['intent']='湖岸光影记录'; decision['days'][2]['intent']='湿地慢行'
    errors,warnings=validate_decision(decision,pools,places,trip.preferences.interests)
    assert not errors and not warnings
    normalized=canonicalize_decision(decision)
    ItineraryPlanDecision.model_validate(normalized)
    hydrated=hydrate_decision(normalized,pools)
    assert all('covered_interests' not in day for day in hydrated['days'])
    assert hydrated['days'][0]['date']==pools[0]['date'] and hydrated['days'][0]['city']=='杭州'


def test_false_interest_claim_is_ignored_instead_of_hard_failure():
    trip,places,pools=fixtures(); decision=valid_decision(pools)
    decision['days'][0]['covered_interests']=['海岛与沙滩']
    errors,warnings=validate_decision(decision,pools,places,[*trip.preferences.interests,'海岛与沙滩'])
    assert not errors and not warnings
    decision=valid_decision(pools)
    errors,warnings=validate_decision(decision,pools,places,trip.preferences.interests)
    assert not errors and not warnings


def test_candidate_pool_normalization_applies_role_priority_without_reordering_or_creation():
    cases=[
        ({'primary_anchor_id':'a','candidate_place_ids':['p1','a','p2'],'backup_candidate_ids':['b1']},(['p1','p2'],['b1'])),
        ({'primary_anchor_id':'a','candidate_place_ids':['p1'],'backup_candidate_ids':['b1','a','b2']},(['p1'],['b1','b2'])),
        ({'primary_anchor_id':'a','candidate_place_ids':['p1','p2'],'backup_candidate_ids':['b1','p1','b2','p2']},(['p1','p2'],['b1','b2'])),
        ({'primary_anchor_id':'a','candidate_place_ids':['a','p1'],'backup_candidate_ids':['a','p1','b1']},(['p1'],['b1'])),
    ]
    for raw,(preferred,backups) in cases:
        original_ids={raw['primary_anchor_id'],*raw['candidate_place_ids'],*raw['backup_candidate_ids']}
        result=normalize_day_candidate_pools({'days':[copy.deepcopy(raw)]})['days'][0]
        assert result['primary_anchor_id']=='a'
        assert result['candidate_place_ids']==preferred and result['backup_candidate_ids']==backups
        assert {result['primary_anchor_id'],*preferred,*backups} <= original_ids


def test_normalized_overlap_passes_semantic_validation_without_llm_repair():
    trip,places,pools=fixtures(); raw=valid_decision(pools)
    anchor=raw['days'][2]['primary_anchor_id']; other=next(pid for pid in pools[2]['allowed_candidate_place_ids'] if pid!=anchor)
    raw['days'][2]['candidate_place_ids']=[anchor,other]
    raw['days'][2]['backup_candidate_ids']=[anchor,other]
    normalized=normalize_day_candidate_pools(canonicalize_decision(copy.deepcopy(raw)))
    errors,_=validate_decision(normalized,pools,places,trip.preferences.interests)
    assert not any(error['code']=='OVERLAPPING_DAY_POOLS' for error in errors)

    class Client:
        def __init__(self): self.calls=0; self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):
            self.calls+=1
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw,ensure_ascii=False)))])
    client=Client(); runner=ChunkRunner(client,'test',trip,'glm-4-plus')
    def semantic(data): return validate_decision(data,pools,places,trip.preferences.interests)[0]
    result=asyncio.run(runner.ask('itinerary-plan',ItineraryPlanDecision,'test',{},semantic,
        lambda data:normalize_day_candidate_pools(canonicalize_decision(data)),decision_schema(pools,trip.preferences.interests)))
    assert client.calls==1 and runner.records['itinerary-plan']['attempts']==1
    assert result['days'][2]['primary_anchor_id']==anchor
    assert result['days'][2]['candidate_place_ids']==[other] and result['days'][2]['backup_candidate_ids']==[]
    class Store:
        days=trip.days
        def expected_signature(self,*args): return 'signature'
        def save_valid(self,*args,**kwargs): self.saved=(args,kwargs)
    store=Store(); _persist(store,'itinerary-plan',result,runner,'itinerary-plan')
    assert store.saved[0][4]==0


def test_actual_day_pois_derive_soft_interest_warnings_without_failure():
    trip,places,pools=fixtures(); decision=valid_decision(pools)
    decision['days'][0]['covered_interests']=['海岛与沙滩']; plan=hydrate_decision(decision,pools)
    restaurant=next(p['id'] for p in places if p['type']=='restaurant')
    itinerary=[{'stops':[{'place_id':restaurant}]} for _ in pools]
    errors,warnings=validate_actual_interest_coverage(itinerary,plan,places,[*trip.preferences.interests,'海岛与沙滩'])
    assert errors==[]
    assert warnings and all(w['code'] in ('INTEREST_WEAK_MATCH','INTEREST_DENSITY_HIGH') for w in warnings)


def test_day_contract_exposes_only_assigned_catalog_and_dynamic_optional_enum():
    trip,places,pools=fixtures(); decision=valid_decision(pools)
    anchor=decision['days'][2]['primary_anchor_id']; options=[pid for pid in pools[2]['allowed_candidate_place_ids'] if pid!=anchor]
    decision['days'][2]['candidate_place_ids']=options[:2]; decision['days'][2]['backup_candidate_ids']=options[2:4]
    outline=hydrate_decision(decision,pools)['days'][2]
    context=day_catalog(outline,places); schema=day_decision_schema(outline)
    exposed={context['fixed_primary_anchor']['id'],context['fixed_meal_stop']['id'],
             *[p['id'] for p in context['preferred_candidate_places']],
             *[p['id'] for p in context['backup_candidate_places']]}
    assert exposed < {p['id'] for p in places}
    assert 'candidate_catalog' not in context
    assert set(schema['properties'])=={'optional_stop_order','day_intent','practical_notes'}
    optional=schema['properties']['optional_stop_order']
    assert optional['uniqueItems'] is True
    assert set(optional['items']['enum'])==set(options[:4])
    assert all(field not in schema['properties'] for field in ('date','city','arrival_time','primary_anchor_id','fixed_meal_stop_id'))


def test_day_optional_can_be_empty_and_backend_injects_fixed_stops_without_map_api():
    trip,places,pools=fixtures(); plan=hydrate_decision(valid_decision(pools),pools); outline=plan['days'][0]
    day=schedule_day({'optional_stop_order':[],'day_intent':'轻松游览城市代表地点',
                      'practical_notes':['根据现场开放和体力情况灵活调整当天停留时间。']},outline,places,trip,0)
    ids=[stop['place_id'] for stop in day['stops']]
    assert ids==[outline['primary_anchor_id'],outline['fixed_meal_stop_id']]
    assert day['date']==outline['date'] and day['city']==outline['city']
    assert all(stop['transfer_minutes'] is None and stop['distance_km'] is None for stop in day['stops'])


def test_day_can_select_backup_but_rejects_every_id_outside_plan_pool():
    trip,places,pools=fixtures(); decision=valid_decision(pools)
    anchor=decision['days'][0]['primary_anchor_id']; options=[pid for pid in pools[0]['allowed_candidate_place_ids'] if pid!=anchor]
    decision['days'][0]['candidate_place_ids']=options[:1]; decision['days'][0]['backup_candidate_ids']=options[1:3]
    outline=hydrate_decision(decision,pools)['days'][0]; backup=options[1]
    selected={'optional_stop_order':[backup],'day_intent':'使用备用地点完善当天内容',
              'practical_notes':['备用地点仅在当天节奏允许时加入，并保留现场调整空间。']}
    assert not validate_day_decision(selected,outline)
    assert backup in [stop['place_id'] for stop in schedule_day(selected,outline,places,trip,0)['stops']]
    error=validate_day_decision({**selected,'optional_stop_order':['outside-id']},outline)[0]
    assert error['code']=='DAY_STOP_OUTSIDE_PLAN_POOL' and error['invalid_value']=='outside-id'
    schema=day_decision_schema(outline)
    assert list(Draft202012Validator(schema).iter_errors({**selected,'optional_stop_order':['outside-id']}))


def test_cached_places_flow_enters_day_generation_without_researching_places():
    trip,places,pools=fixtures(); decision=valid_decision(pools)
    class Store:
        days=trip.days
        def __init__(self): self.saved={}; self.running=[]
        def expected_signature(self,pack_id,deps): return pack_id
        def get_valid(self,pack_id,signature):
            groups={
                'places-core':[p for p in places if p['type']=='sight'],
                'places-experiences':[p for p in places if p['type']=='experience'],
                'places-food':[p for p in places if p['type'] in ('restaurant','chain')],
            }
            return {'places':groups[pack_id]} if pack_id in groups else None
        def mark_running(self,pack_id): self.running.append(pack_id)
        def save_valid(self,pack_id,payload,signature,generation_attempts=0,repair_attempts=0,validation_warnings=None,**kwargs): self.saved[pack_id]=payload
        def invalidate(self,*args,**kwargs): pass
    class Client:
        def __init__(self): self.calls=[]; self.prompts=[]; self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):
            prompt=json.loads(kwargs['messages'][1]['content']); stage=prompt['stage']; self.calls.append(stage); self.prompts.append(prompt)
            data=decision if stage=='itinerary-plan' else {}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data,ensure_ascii=False)))])
    store=Store(); client=Client()
    with pytest.raises(BaseExceptionGroup):
        asyncio.run(build_chunks(client,trip,lambda *args:None,'test', 'glm-4-plus',store))
    assert 'itinerary-plan' in store.saved
    assert any(stage.startswith('day-') for stage in client.calls)
    assert not any(stage.startswith(('sights-','restaurants-','experiences-')) or stage=='chains' for stage in client.calls)
    for prompt in [p for p in client.prompts if p['stage'].startswith('day-')]:
        assert set(prompt['context'])=={'fixed_primary_anchor','preferred_candidate_places','backup_candidate_places','fixed_meal_stop','area_labels'}


def test_partial_research_cache_reruns_only_missing_food_pack():
    trip,places,pools=fixtures();decision=valid_decision(pools)
    class Store:
        days=trip.days
        def __init__(self):self.saved={};self.running=[]
        def expected_signature(self,pack_id,deps):return pack_id
        def get_valid(self,pack_id,signature):
            groups={
                'places-core':[p for p in places if p['type']=='sight'],
                'places-experiences':[p for p in places if p['type']=='experience'],
            }
            return {'places':groups[pack_id]} if pack_id in groups else None
        def mark_running(self,pack_id):self.running.append(pack_id)
        def save_valid(self,pack_id,payload,signature,generation_attempts=0,repair_attempts=0,validation_warnings=None,**kwargs):self.saved[pack_id]=payload
        def invalidate(self,*args,**kwargs):pass
    class Client:
        def __init__(self):self.calls=[];self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):
            stage=json.loads(kwargs['messages'][1]['content'])['stage'];self.calls.append(stage)
            if stage.startswith('restaurants-'):data={'places':[p for p in places if p['type']=='restaurant'][:4]}
            elif stage=='chains':data={'places':[p for p in places if p['type']=='chain']}
            elif stage=='itinerary-plan':data=decision
            else:data={}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data,ensure_ascii=False)))])
    store=Store();client=Client()
    with pytest.raises(BaseExceptionGroup):
        asyncio.run(build_chunks(client,trip,lambda *args:None,'test','glm-4-plus',store))
    assert any(stage.startswith('restaurants-') for stage in client.calls) and 'chains' in client.calls
    assert not any(stage.startswith(('sights-','experiences-')) for stage in client.calls)
    assert 'places-food' in store.saved and 'places-core' in store.saved and 'places-experiences' in store.saved
