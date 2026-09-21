import asyncio
import copy
import json
import time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from app.main import app, connect
from app.models import BuildInput, DestinationProfile, PlacesPack, PracticalPack
from app.llm_chunks import ChunkRunner, StageValidationError
from app.offline import make_packs
from app.pipeline import assemble, generate
from app.pipeline import failure_reason
from app.validators import check_handoff, schema_issues, validate_destination_data, validate_research_pack, validate_day, validate_trip_coherence
from app.pack_graph import signature, descendants
from app.pack_store import PackStore

PREFS=dict(origin='上海',destinations=['杭州'],mainDestination='杭州',startDate='2026-10-02',endDate='2026-10-06',adults=2,children=1,seniors=1,travelStyle='家庭旅行',budget={'total':10000},preferences={'pace':'balanced','interests':['人文古迹','美食'],'localTransport':['transit']},outboundPeriod='morning',returnPeriod='evening')

def test_first_use_intake_preferences_are_preserved():
    values=PREFS|{'preferences':PREFS['preferences']|{'budgetLevel':'comfort','mustGo':'西湖','avoid':'长途徒步','constraints':'海鲜过敏'}}
    prefs=BuildInput(**values).model_dump(mode='json')['preferences']
    assert {key:prefs[key] for key in ('budgetLevel','mustGo','avoid','constraints')} == {
        'budgetLevel':'comfort','mustGo':'西湖','avoid':'长途徒步','constraints':'海鲜过敏'}

def test_failure_reason_unwraps_without_exposing_details():
    reason=failure_reason(ExceptionGroup('provider secret',[TimeoutError('secret-key')]))
    assert '超时' in reason and 'secret' not in reason
    assert '校验' in failure_reason(ValueError('raw model response'))


def test_stage_validation_error_preserves_actionable_diagnostics():
    error=StageValidationError('day-3',[{'pointer':'/stops/2/place_id','code':'DAY_STOP_OUTSIDE_PLAN_POOL',
        'message':'停靠点必须来自当天骨架候选池。','invalid_value':'outside-id',
        'validator':'day_candidate_pool','depends_on_map_api':False}])
    assert error.errors[0] == {'pointer':'/stops/2/place_id','code':'DAY_STOP_OUTSIDE_PLAN_POOL',
        'message':'停靠点必须来自当天骨架候选池。','invalid_value':'outside-id',
        'validator':'day_candidate_pool','depends_on_map_api':False}

def test_other_city_preserves_llm_failure(monkeypatch):
    monkeypatch.setenv('ZHIPU_API_KEY','test')
    class Unavailable:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): raise TimeoutError('private data')
        async def __aexit__(self,*args): pass
    monkeypatch.setattr('app.pipeline.AsyncOpenAI',Unavailable)
    with pytest.raises(ValueError,match='智谱生成超时') as exc:
        asyncio.run(generate(BuildInput(**(PREFS|{'destinations':['北京'],'mainDestination':'北京'})),'test',lambda *args:None))
    assert 'private' not in str(exc.value)

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'test.db'))
    monkeypatch.setenv('ZHIPU_API_KEY','')
    with TestClient(app) as c: yield c

def finish(c,id):
    for _ in range(100):
        result=c.get('/api/build/'+id).json()
        if result['status'] in ('done','done_with_warnings','paused','failed'): return result
        time.sleep(.03)
    pytest.fail('任务没有在时限内结束')

def test_offline_api_complete_persistent(client):
    submitted=client.post('/api/build',json=PREFS)
    assert submitted.status_code==202
    id=submitted.json()['job_id']
    assert finish(client,id)['status']=='done_with_warnings'
    result=client.get(f'/api/build/{id}/result')
    assert result.status_code==200
    profile=result.json()
    assert not validate_destination_data(profile)
    assert profile['generation']['degraded']
    assert len([p for p in profile['places'] if p['type']=='sight'])>=8
    assert all('official_url' in p and p['rating'] is None for p in profile['places'])
    assert len(profile['module_groups']['language']['keyword_groups'])==5
    assert 'menu_primer' not in profile['module_groups']['food']
    assert client.get('/api/trips').json()['items'][0]['id']==id
    with connect() as db:
        row=db.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
        assert json.loads(row['report'])['passed']
        assert not check_handoff(json.loads(row['result']),json.loads(row['packs']))

@pytest.mark.parametrize('key',['origin','destinations','mainDestination','startDate','endDate','adults','children','seniors','travelStyle','budget'])
def test_required_fields(client,key):
    payload=copy.deepcopy(PREFS);payload.pop(key)
    r=client.post('/api/build',json=payload)
    assert r.status_code==422
    assert r.json()['error']['details']
    assert not client.get('/api/trips').json()['items']

@pytest.mark.parametrize('change',[{'endDate':'2026-01-01'},{'adults':-1},{'adults':True},{'adults':0,'children':0,'seniors':0},{'mainDestination':'北京'},{'destinations':[]},{'budget':{'total':100,'food':101}}])
def test_invalid_preferences(client,change):
    assert client.post('/api/build',json=PREFS|change).status_code==422

def test_unknown_and_no_partial(client):
    assert client.get('/api/build/missing/result').status_code==404
    id=client.post('/api/build',json=PREFS|{'destinations':['北京'],'mainDestination':'北京'}).json()['job_id']
    state=finish(client,id)
    assert state['status']=='paused'
    assert 'ZHIPU_API_KEY' in state['error']['message']
    assert client.get(f'/api/build/{id}/result').status_code==409
    assert not client.get('/api/trips').json()['items']
    assert client.post(f'/api/build/{id}/resume').status_code==202

@pytest.mark.parametrize('mutation',['missing','shopping','rating','hours','references','checklist_item','date','menu'])
def test_gate_rejects_corruption(mutation):
    t=BuildInput(**PREFS); packs=make_packs(t);p=assemble(t,'test',packs,'offline',[])
    if mutation=='missing':p['places'][0].pop('official_url')
    if mutation=='shopping':p['module_groups']['shopping']=[]
    if mutation=='rating':p['places'][0]['rating']=4.8
    if mutation=='hours':p['places'][0]['hours_note']='09:00开放，出发前请复核'
    if mutation=='references':p['itinerary'][0]['stops'][0]['place_id']='unknown'
    if mutation=='checklist_item':p['module_groups']['preparation']['essentials'][0].pop('title')
    if mutation=='date':p['itinerary'].pop()
    if mutation=='menu':p['module_groups']['food']['menu_primer']=[]
    assert validate_destination_data(p)


@pytest.mark.parametrize('count',[5,6])
def test_canonical_preparation_compiles_for_valid_short_enrichment(count):
    t=BuildInput(**PREFS);packs=make_packs(t)
    preparation=packs['modules-practical']['preparation']
    preparation['essentials']=preparation['essentials'][:count]
    preparation['confirm_ahead']=preparation['confirm_ahead'][:count]
    assert not validate_research_pack('modules-practical',packs['modules-practical'])
    profile=assemble(t,f'prep-{count}',packs,'offline',[])
    assert not validate_destination_data(profile)
    assert not check_handoff(profile,packs)


def test_practical_and_profile_share_canonical_preparation_schema():
    practical=PracticalPack.model_json_schema()
    profile=DestinationProfile.model_json_schema()
    assert practical['$defs']['Preparation']==profile['$defs']['Preparation']
    assert 'anyOf' not in practical['$defs']['Preparation']
    assert profile['$defs']['Modules']['properties']['preparation']=={'$ref':'#/$defs/Preparation'}


def test_nested_json_schema_errors_are_fully_exposed():
    schema={'anyOf':[{'type':'string'},{'type':'object','required':['title'],'additionalProperties':False}]}
    errors=schema_issues(Draft202012Validator(schema).iter_errors({'extra':1}))
    assert {error['validator'] for error in errors}=={'json_schema.type','json_schema.required','json_schema.additionalProperties'}
    assert all({'schema_path','instance_path','validator_value','message','invalid_value'} <= error.keys() for error in errors)
    assert not any(error['message']=='is not valid under any of the given schemas' for error in errors)

def test_provenance():
    t=BuildInput(**PREFS);packs=make_packs(t);p=copy.deepcopy(assemble(t,'test',packs,'offline',[]))
    p['itinerary'][0]['theme']='被修改的主题'
    assert check_handoff(p,packs)

def test_json_repair_once():
    class Fake:
        def __init__(self):self.calls=[];self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):
            self.calls.append(copy.deepcopy(kwargs))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))])
    fake=Fake()
    with pytest.raises(ValueError):asyncio.run(ChunkRunner(fake,'test',BuildInput(**PREFS),'glm-4-plus').ask('places',PlacesPack,'test'))
    assert len(fake.calls)==2
    assert fake.calls[0]['response_format']=={'type':'json_object'}
    assert '修复' in fake.calls[1]['messages'][-1]['content']
    assert all(c['max_tokens']==4096 for c in fake.calls)

def test_provider_failure_falls_back(monkeypatch):
    monkeypatch.setenv('ZHIPU_API_KEY','test-not-a-real-key')
    class Unavailable:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):raise RuntimeError('unavailable')
        async def __aexit__(self,*args):pass
    monkeypatch.setattr('app.pipeline.AsyncOpenAI',Unavailable)
    p,packs=asyncio.run(generate(BuildInput(**PREFS),'fallback',lambda *args:None))
    assert p['generation']['degraded'] and not check_handoff(p,packs)

def test_restart_recovers_queue(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'restart.db'));monkeypatch.setenv('ZHIPU_API_KEY','')
    with TestClient(app):
        with connect() as db:
            db.execute("INSERT INTO jobs(id,status,stage,progress,preferences,created_at,updated_at) VALUES ('recovery','running','places-core',10,?,'x','x')",(json.dumps(PREFS),))
    with TestClient(app) as c:
        assert finish(c,'recovery')['status']=='done_with_warnings'
        assert c.get('/api/trips').json()['items'][0]['id']=='recovery'

def test_bounded_llm_success(monkeypatch):
    """Exercise actual chunk orchestration, not just the fallback branch."""
    monkeypatch.setenv('ZHIPU_API_KEY','test-not-a-real-key')
    t=BuildInput(**PREFS); packs=make_packs(t)
    all_places=packs['places-core']['places']; modules=packs['modules-practical']; lang=packs['modules-language-notes']
    class FakeClient:
        calls=[]
        def __init__(self,**kwargs):self.chat=SimpleNamespace(completions=self)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def create(self,**kwargs):
            self.calls.append(kwargs)
            stage=json.loads(kwargs['messages'][1]['content'])['stage']
            if stage.startswith('sights-'):
                i=int(stage.split('-')[-1]);data={'places':[p for p in all_places if p['type']=='sight'][i*4:i*4+4]}
            elif stage.startswith('restaurants-'):
                data={'places':[p for p in all_places if p['type']=='restaurant'][:6]}
            elif stage.startswith('experiences-'):
                kind=['culture','nature','food_workshop'][int(stage.split('-')[-1])];data={'places':[p for p in all_places if p['type']=='experience' and p['experience_type']==kind]}
            elif stage=='chains':data={'places':[p for p in all_places if p['type']=='chain']}
            elif stage=='itinerary-plan':
                context=json.loads(kwargs['messages'][1]['content'])['context']; data={'days':[]}
                for i,pool in enumerate(context['days']):
                    anchor=pool['allowed_primary_anchor_ids'][i%len(pool['allowed_primary_anchor_ids'])]
                    options=[pid for pid in pool['allowed_candidate_place_ids'] if pid!=anchor]
                    data['days'].append({'area_labels':['同区安排'],'primary_anchor_id':anchor,
                        'candidate_place_ids':options[:2],'backup_candidate_ids':options[2:4],
                        'intent':'按体力与片区安排当天行程'})
            elif stage.startswith('day-'):
                context=json.loads(kwargs['messages'][1]['content'])['context']
                preferred=[p['id'] for p in context['preferred_candidate_places']]
                data={'optional_stop_order':preferred[:2],'day_intent':'按片区与体力安排当天参观',
                      'practical_notes':['关注现场开放与人流，根据体力保留休息和调整空间。']}
            elif stage=='menu-guide':data=modules['food']['menu_guide']
            elif stage=='snacks':data={'local_snacks':modules['food']['local_snacks']}
            elif stage=='preparation':data=modules['preparation']
            elif stage.startswith('keywords-'):data=lang['language']['keyword_groups'][int(stage.split('-')[-1])]
            elif stage.startswith('phrases-'):data=lang['language']['phrase_groups'][int(stage.split('-')[-1])]
            elif stage.startswith('note-'):data=next(n for n in lang['travel_notes'] if n['category']==stage[5:])
            else:raise AssertionError(stage)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data,ensure_ascii=False)))])
    monkeypatch.setattr('app.pipeline.AsyncOpenAI',FakeClient)
    p,packs=asyncio.run(generate(t,'llm-success',lambda *args:None))
    assert p['generation']['mode']=='llm',p['generation']['warnings']
    assert not check_handoff(p,packs)
    assert len(FakeClient.calls)>20
    assert all(c['max_tokens']==4096 for c in FakeClient.calls)
    restaurant_prompts=[json.loads(c['messages'][1]['content']) for c in FakeClient.calls if json.loads(c['messages'][1]['content'])['stage'].startswith('restaurants-')]
    assert all(x['schema']['properties']['places']['minItems']==2 and x['schema']['properties']['places']['maxItems']==6 for x in restaurant_prompts)
    assert all('2到6个' in x['instruction'] for x in restaurant_prompts)
    assert all(x['hours_note']=='开放与接待时间尚未核实，出发前请复核。' for x in p['places'])

def test_pack_signature_covers_input_dependencies_and_versions():
    a=signature('itinerary-day-1',{'city':'杭州'},{'itinerary-plan':'one'})
    assert a==signature('itinerary-day-1',{'city':'杭州'},{'itinerary-plan':'one'})
    assert a!=signature('itinerary-day-1',{'city':'北京'},{'itinerary-plan':'one'})
    assert a!=signature('itinerary-day-1',{'city':'杭州'},{'itinerary-plan':'two'})

def test_pack_store_invalidates_only_descendants(client):
    job_id=client.post('/api/build',json=PREFS).json()['job_id']
    assert finish(client,job_id)['status']=='done_with_warnings'
    t=BuildInput(**PREFS); store=PackStore(connect,job_id,t.model_dump(mode='json'),t.days)
    store.invalidate('itinerary-day-3',[{'pointer':'/stops','message':'test'}])
    states=store.summary()['pack_statuses']
    assert states['itinerary-day-3']=='invalid'
    assert states['itinerary-day-2']=='valid'
    assert states['modules-language-notes']=='valid'
    assert states['itinerary-coherence']=='pending'
    assert 'destination-profile' in descendants('itinerary-day-3',t.days)


def test_resume_rebuilds_failed_day_allocation_from_persisted_packs(client):
    job_id=client.post('/api/build',json=PREFS).json()['job_id']
    assert finish(client,job_id)['status']=='done_with_warnings'
    t=BuildInput(**PREFS); store=PackStore(connect,job_id,t.model_dump(mode='json'),t.days)
    with connect() as db:
        before=json.loads(db.execute("SELECT payload FROM job_packs WHERE job_id=? AND pack_id='itinerary-allocation'",(job_id,)).fetchone()['payload'])
    other_before={pid:owner for pid,owner in before['place_assignments'].items() if owner['day']!=3}
    store.invalidate('itinerary-day-3',[{'pointer':'/stops','code':'test','message':'test'}])
    store.resume()
    with connect() as db:
        row=db.execute("SELECT status,payload FROM job_packs WHERE job_id=? AND pack_id='itinerary-allocation'",(job_id,)).fetchone()
    after=json.loads(row['payload']); other_after={pid:owner for pid,owner in after['place_assignments'].items() if owner['day']!=3}
    assert row['status']=='valid' and other_after==other_before
    assert any(owner['day']==3 for owner in after['place_assignments'].values())
    assert store.summary()['pack_statuses']['itinerary-day-3']=='pending'

def test_plan_resume_keeps_all_valid_place_packs(client):
    job_id=client.post('/api/build',json=PREFS).json()['job_id']
    assert finish(client,job_id)['status']=='done_with_warnings'
    t=BuildInput(**PREFS); store=PackStore(connect,job_id,t.model_dump(mode='json'),t.days)
    store.invalidate('itinerary-plan',[{'pointer':'/days/3/primary_anchor_id','code':'INVALID_PRIMARY_ANCHOR','message':'test'}])
    before=store.summary()['pack_statuses']
    assert all(before[name]=='valid' for name in ('places-core','places-experiences','places-food'))
    assert before['itinerary-plan']=='invalid' and before['itinerary-day-1']=='pending'
    assert before['modules-language-notes']=='valid'
    store.resume(); after=store.summary()['pack_statuses']
    assert after['itinerary-plan']=='pending'
    assert all(after[name]=='valid' for name in ('places-core','places-experiences','places-food','modules-language-notes'))

def test_day_and_trip_semantic_gates_target_one_day():
    t=BuildInput(**PREFS); packs=make_packs(t); places=packs['places-core']['places']; day=copy.deepcopy(packs['itinerary']['itinerary'][2])
    day['stops'][1]['arrival_time']=day['stops'][0]['arrival_time']
    assert any(e['code']=='day' for e in validate_day(day,places,expected_date=day['date'],expected_city='杭州'))
    plan={'days':[]}
    for i,d in enumerate(packs['itinerary']['itinerary'],1):
        sight=next(s['place_id'] for s in d['stops'] if next(p for p in places if p['id']==s['place_id'])['type']=='sight')
        restaurant=next(s['place_id'] for s in d['stops'] if next(p for p in places if p['id']==s['place_id'])['type']=='restaurant')
        plan['days'].append({'primary_anchor_id':sight,'restaurant_id':restaurant})
    broken=copy.deepcopy(packs['itinerary']['itinerary']); broken[2]['stops']=[s for s in broken[2]['stops'] if s['place_id']!=plan['days'][2]['primary_anchor_id']]
    errors=validate_trip_coherence(broken,plan,places,t.preferences.model_dump(mode='json'))
    assert errors and {e['pack_id'] for e in errors}=={'itinerary-day-3'}
