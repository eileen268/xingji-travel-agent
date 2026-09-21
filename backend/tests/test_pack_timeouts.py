import asyncio
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
import httpx
from openai import APITimeoutError

from pydantic import Field, create_model

from app.budgets import enrichment_budget, experiences_for_city, restaurants_for_city, sights_for_city
from app.llm_chunks import ChunkRunner, StageTimeoutError, StageValidationError, build_chunks
from app.migrations import migrate
from app.models import StrictModel, BuildInput
from app.pack_store import PackStore
from app.pipeline import failure_reason
from app.pipeline import assemble
from app.offline import make_packs
from app.validators import validate_destination_data


PREFS=dict(origin='西安',destinations=['兰州'],mainDestination='兰州',startDate='2026-10-02',endDate='2026-10-03',
           adults=1,children=0,seniors=0,travelStyle='独自旅行',budget={'total':3000},
           preferences={'pace':'balanced','interests':['美食'],'localTransport':['transit']})


def store_for(tmp_path, values=None):
    values=values or PREFS
    path=tmp_path/'timeouts.db';migrate(path)
    @contextmanager
    def connect():
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    with connect() as db:
        db.execute("INSERT INTO jobs(id,status,stage,progress,preferences,created_at,updated_at) VALUES ('j','running','x',1,?,'x','x')",
                   (json.dumps(values),))
    trip=BuildInput(**values);store=PackStore(connect,'j',trip.model_dump(mode='json'),trip.days,{},'run-1');store.ensure()
    return store,connect,trip


def test_two_day_budget_is_compact():
    assert sights_for_city(2)==6
    assert 2 <= experiences_for_city(2) <= 4
    assert 3 <= restaurants_for_city(2) <= 4
    budget=enrichment_budget(2)
    assert budget['language_groups']<5 and budget['language_items']<5 and budget['checklist']<24


def test_pack_timeout_is_persisted_and_resume_keeps_unrelated_pack(tmp_path):
    store,_,_=store_for(tmp_path)
    core_sig=store.expected_signature('places-core',())
    sight={'id':'kept','type':'sight','city':'兰州','display_name':'测试景点','local_name':'测试景点',
           'description':'用于验证 pack checkpoint 的测试地点。','official_url':None,
           'map_url':'https://uri.amap.com/search?keyword=test','coordinates':None,'rating':None,'review_count':None,
           'hours_note':'出发前请复核','ticket_note':'出发前请复核','reservation_note':'出发前请复核',
           'recheck_note':'出发前请复核','knowledge_status':'model_knowledge','cuisine':'','signature_dishes':'',
           'experience_type':'','semantic_tags':[],'interest_affinity':{},'semantic_source':[],
           'semantic_confidence':{},'affinity_reasons':{},'semantic_version':'interest-taxonomy-v2'}
    store.mark_running('places-core');store.save_valid('places-core',{'places':[sight]},core_sig)
    store.mark_running('modules-language-notes')
    store.save_timed_out('modules-language-notes','pack_timeout',60,60.2)
    before=store.summary()
    assert before['pack_statuses']['places-core']=='valid'
    assert before['pack_statuses']['modules-language-notes']=='timed_out'
    assert before['validation_errors'][0]['code']=='PACK_TIMEOUT'
    assert before['pack_attempts']['modules-language-notes']['generation_attempt_count']==1
    assert before['pack_attempts']['modules-language-notes']['repair_attempt_count']==0
    store.resume();after=store.summary()['pack_statuses']
    assert after['places-core']=='valid'
    assert after['modules-language-notes']=='pending'
    assert after['destination-profile']=='pending'


def test_subrequest_is_visible_while_provider_call_is_running(tmp_path):
    store,_,_=store_for(tmp_path);started=asyncio.Event()
    class Slow:
        def __init__(self):self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):
            started.set();await asyncio.Event().wait()
    Tiny=create_model('Tiny',__base__=StrictModel,value=(str,Field(min_length=1)))
    runner=ChunkRunner(Slow(),'system',BuildInput(**PREFS),'glm-test',store=store)
    async def exercise():
        task=asyncio.create_task(runner.ask('itinerary-plan',Tiny,'choose',{'items':[1,2]}))
        await started.wait()
        row=store.summary()['llm_subrequests'][0]
        assert row['status']=='running' and row['prompt_chars']>0 and row['estimated_tokens']>0
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        store.save_timed_out('itinerary-plan','task_safety_timeout',1800,1800.1)
    asyncio.run(exercise())
    row=store.summary()['llm_subrequests'][0]
    assert row['status']=='timeout' and row['error_type']=='task_safety_timeout'


def timeout_error():
    return APITimeoutError(request=httpx.Request('POST','https://open.bigmodel.cn/api/paas/v4/chat/completions'))


def response(data,request_id='req-ok'):
    return SimpleNamespace(id=request_id,usage=SimpleNamespace(prompt_tokens=12,completion_tokens=7),
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data,ensure_ascii=False)))])


def test_provider_read_timeout_retries_once_without_semantic_repair(tmp_path,monkeypatch):
    store,_,trip=store_for(tmp_path);monkeypatch.setattr('app.llm_chunks.random.uniform',lambda *_:0)
    class Flaky:
        def __init__(self):self.calls=0;self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):
            self.calls+=1
            if self.calls==1:raise timeout_error()
            return response({'value':'OK'})
    Tiny=create_model('RetryTiny',__base__=StrictModel,value=(str,Field(min_length=1)))
    client=Flaky();runner=ChunkRunner(client,'system',trip,'glm-test',store=store)
    result=asyncio.run(runner.ask('itinerary-plan',Tiny,'只返回结果'))
    assert result=={'value':'OK'} and client.calls==2
    assert runner.records['itinerary-plan']['provider_retry_count']==1
    assert runner.records['itinerary-plan']['attempts']==1
    row=store.summary()['llm_subrequests'][0]
    assert row['provider_retry_count']==1 and row['repair_attempt_number']==0 and row['status']=='completed'


def test_completed_subrequest_checkpoint_survives_backend_restart(tmp_path):
    store,connect,trip=store_for(tmp_path)
    Tiny=create_model('CheckpointTiny',__base__=StrictModel,value=(str,Field(min_length=1)))
    class First:
        def __init__(self):self.calls=0;self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):self.calls+=1;return response({'value':'saved'},'req-saved')
    first=First();runner=ChunkRunner(first,'system',trip,'glm-test',store=store)
    assert asyncio.run(runner.ask('sights-checkpoint',Tiny,'保存一次'))['value']=='saved'
    restarted=PackStore(connect,'j',trip.model_dump(mode='json'),trip.days,{},'run-2')
    class MustNotCall:
        def __init__(self):self.calls=0;self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):self.calls+=1;raise AssertionError('checkpoint should be reused')
    second=MustNotCall();runner2=ChunkRunner(second,'system',trip,'glm-test',store=restarted)
    assert asyncio.run(runner2.ask('sights-checkpoint',Tiny,'保存一次'))['value']=='saved'
    assert second.calls==0 and runner2.records['sights-checkpoint']['checkpoint_reused']


def test_research_pack_checkpoint_and_resume_skip_completed_packs(tmp_path,monkeypatch):
    values={**PREFS,'destinations':['杭州'],'mainDestination':'杭州'}
    store,_,trip=store_for(tmp_path,values);fixture=make_packs(trip)['places-core']['places']
    monkeypatch.setattr('app.llm_chunks.random.uniform',lambda *_:0)
    class ResearchTimeout:
        def __init__(self):self.calls=[];self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):
            stage=json.loads(kwargs['messages'][1]['content'])['stage'];self.calls.append(stage)
            if stage.startswith('sights-'):
                batch=int(stage.rsplit('-',1)[1]);return response({'places':[p for p in fixture if p['type']=='sight'][batch*4:batch*4+(4 if batch==0 else 2)]})
            if stage.startswith('experiences-'):
                kind=['culture','nature','food_workshop'][int(stage.rsplit('-',1)[1])]
                return response({'places':[next(p for p in fixture if p['type']=='experience' and p['experience_type']==kind)]})
            if stage.startswith('restaurants-'):raise timeout_error()
            raise AssertionError(stage)
    first=ResearchTimeout()
    try:asyncio.run(build_chunks(first,trip,lambda *args:None,'system','glm-test',store))
    except StageTimeoutError as exc:assert exc.pack_id=='places-food'
    else:raise AssertionError('places-food should time out')
    state=store.summary()['pack_statuses']
    assert state['places-core']=='valid' and state['places-experiences']=='valid' and state['places-food']=='timed_out'
    assert store.get_source_payload('places-core')['places']
    store.resume()
    class ResumeFood:
        def __init__(self):self.calls=[];self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):
            stage=json.loads(kwargs['messages'][1]['content'])['stage'];self.calls.append(stage)
            if stage.startswith('restaurants-'):return response({'places':[p for p in fixture if p['type']=='restaurant'][:4]})
            if stage=='chains':return response({'places':[p for p in fixture if p['type']=='chain'][:1]})
            return response({})
    second=ResumeFood()
    try:asyncio.run(build_chunks(second,trip,lambda *args:None,'system','glm-test',store))
    except StageValidationError:pass
    assert not any(x.startswith(('sights-','experiences-')) for x in second.calls)
    assert second.calls[0].startswith('restaurants-') and 'chains' in second.calls
    assert store.summary()['pack_statuses']['places-food']=='valid'


def test_timeout_message_does_not_blame_trip_length():
    exc=StageTimeoutError('modules-language-notes','pack_timeout',60,60.1)
    message=failure_reason(exc)
    assert '已保存' in message and '继续生成' in message
    assert '缩短行程' not in message


def test_core_profile_remains_viewable_when_language_enrichment_is_deferred():
    values={**PREFS,'destinations':['杭州'],'mainDestination':'杭州'}
    trip=BuildInput(**values);packs=make_packs(trip)
    packs['modules-language-notes']={'language':{'edition':'待补充','keyword_groups':[],'phrase_groups':[]},'travel_notes':[]}
    packs['enrichment']={'practical':'complete','language_notes':'deferred','pending_packs':['modules-language-notes']}
    packs['enrichment_warnings']=['语言与旅行贴士生成超时，已保留核心行程，可继续生成。']
    profile=assemble(trip,'deferred',packs,'llm',[])
    assert profile['enrichment']['language_notes']=='deferred'
    assert profile['itinerary'] and not validate_destination_data(profile)
    assert any('核心行程' in warning['message'] for warning in profile['generation']['warnings'])
