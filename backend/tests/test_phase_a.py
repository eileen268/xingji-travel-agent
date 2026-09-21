import asyncio
import json
import sqlite3
import time
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.execution import claim_next
from app.llm_chunks import ChunkRunner
from app.main import app, connect
from app.migrations import migrate, migration_status
from app.models import BuildInput, PlacesPack
from app.pack_store import PackStore
from benchmark_runner import load_cases
from tests.test_build import PREFS


TERMINAL=('done','done_with_warnings','paused','failed')


def finish(client,job_id):
    for _ in range(150):
        state=client.get(f'/api/build/{job_id}').json()
        if state['status'] in TERMINAL:return state
        time.sleep(.03)
    raise AssertionError('job timeout')


def test_migrations_upgrade_old_database_and_create_backup(tmp_path):
    path=tmp_path/'old.db'; db=sqlite3.connect(path)
    db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY,status TEXT NOT NULL,stage TEXT NOT NULL,progress INTEGER NOT NULL,preferences TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,result TEXT,packs TEXT,report TEXT,error TEXT)')
    db.execute('CREATE TABLE job_packs (job_id TEXT NOT NULL,pack_id TEXT NOT NULL,status TEXT NOT NULL,payload TEXT,validation_errors TEXT,generation_attempt_count INTEGER NOT NULL DEFAULT 0,repair_attempt_count INTEGER NOT NULL DEFAULT 0,resume_count INTEGER NOT NULL DEFAULT 0,input_signature TEXT,prompt_version TEXT,schema_version TEXT,pipeline_version TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(job_id,pack_id))')
    db.commit(); db.close()
    assert migrate(path)==[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17]
    assert migration_status(path)=={'current_version':17,'latest_version':17,'pending':[]}
    assert list((tmp_path/'backups').glob('old-before-v1-*.db'))
    assert migrate(path)==[]


def test_atomic_claim_allows_only_one_worker(tmp_path):
    path=tmp_path/'claim.db'; migrate(path)
    @contextmanager
    def local_connect():
        db=sqlite3.connect(path,timeout=5);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    with local_connect() as db:
        db.execute("INSERT INTO jobs(id,status,stage,progress,preferences,created_at,updated_at) VALUES ('one','queued','queued',0,'{}','a','a')")
    first=claim_next(local_connect,'worker-a'); second=claim_next(local_connect,'worker-b')
    assert first['id']=='one' and first['run_id']
    assert second is None


def test_build_idempotency_and_resume_idempotency(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'idempotent.db'));monkeypatch.setenv('ZHIPU_API_KEY','')
    with TestClient(app) as client:
        headers={'Idempotency-Key':'same-request'}
        a=client.post('/api/build',json=PREFS,headers=headers);b=client.post('/api/build',json=PREFS,headers=headers)
        assert a.json()['job_id']==b.json()['job_id'] and b.json()['idempotent_replay']
        conflict=client.post('/api/build',json=PREFS|{'origin':'北京'},headers=headers)
        assert conflict.status_code==409
        other=client.post('/api/build',json=PREFS|{'destinations':['北京'],'mainDestination':'北京'}).json()['job_id']
        first=client.post(f'/api/build/{other}/resume'); second=client.post(f'/api/build/{other}/resume')
        assert first.status_code==202 and second.status_code==202


def test_manifest_observability_mock_and_replay(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'phase-a.db'));monkeypatch.setenv('ZHIPU_API_KEY','should-not-be-used')
    monkeypatch.setenv('ENABLE_DEV_MODES','1')
    with TestClient(app) as client:
        created=client.post('/api/build',json=PREFS,headers={'X-Agent-Mode':'MOCK'}).json()
        state=finish(client,created['job_id'])
        assert state['status']=='done_with_warnings' and state['manifest']['mode']=='MOCK'
        assert state['execution_attempt_count']==1 and state['run_id'] is None
        assert all('duration_ms' in value for value in state['pack_observability'].values())
        diagnostic=client.get(f"/api/debug/build/{created['job_id']}/errors")
        assert diagnostic.status_code==200
        lifecycle=[event['event_type'] for event in diagnostic.json()['execution_events']]
        assert 'lease_acquired' in lifecycle and 'job_finalize_started' in lifecycle and 'job_finalize_completed' in lifecycle
        replay=client.post(f"/api/build/{created['job_id']}/replay",json={'from_pack':'itinerary-plan'})
        assert replay.status_code==202 and replay.json()['job_id']!=created['job_id']
        replay_state=finish(client,replay.json()['job_id'])
        assert replay_state['status']=='done_with_warnings' and replay_state['manifest']['mode']=='REPLAY'
        assert replay_state['pack_observability']['itinerary-plan']['repair_result']=='replay'


def test_llm_usage_and_request_id_are_recorded():
    class Client:
        def __init__(self):self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):
            return SimpleNamespace(id='request-1',usage=SimpleNamespace(prompt_tokens=12,completion_tokens=8),
                choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))])
    runner=ChunkRunner(Client(),'system',BuildInput(**PREFS),'model')
    try:asyncio.run(runner.ask('bad',PlacesPack,'instruction'))
    except ValueError:pass
    record=runner.records['bad']
    assert record['model_request_id']=='request-1,request-1'
    assert record['token_input']==24 and record['token_output']==16 and record['repair_result']=='failed'


def test_golden_benchmark_has_fixed_valid_coverage():
    cases=load_cases(); names={case['name'] for case in cases}
    assert len(cases)>=15
    assert {'hz_3d_relaxed','sh_4d_photo_history','bj_2d_dense_interests','cd_5d_elderly','sh_sz_multicity',
            'arrival_late','departure_early','many_must_visit','conflicting_preferences','small_city_sparse_pool'}<=names


def test_resume_normalizes_deprecated_interest_failure_without_regenerating_day(tmp_path):
    path=tmp_path/'resume-interest.db';migrate(path)
    @contextmanager
    def local_connect():
        db=sqlite3.connect(path,timeout=5);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    with local_connect() as db:
        db.execute("INSERT INTO jobs(id,status,stage,progress,preferences,created_at,updated_at) VALUES ('interest-job','paused','itinerary-coherence',60,'{}','a','a')")
    store=PackStore(local_connect,'interest-job',{'preferences':{}},1);store.ensure()
    with local_connect() as db:
        db.execute("""UPDATE job_packs SET status='invalid',payload=?,validation_errors=?,generation_attempt_count=1,repair_attempt_count=0
                      WHERE job_id='interest-job' AND pack_id='itinerary-day-1'""",
                   (json.dumps({'day':1,'stops':[]}),json.dumps([{'code':'UNSUPPORTED_INTEREST','pointer':'/itinerary/0/stops'}])))
    store.resume()
    with local_connect() as db:
        row=db.execute("SELECT status,payload,validation_errors,generation_attempt_count,repair_attempt_count,resume_count,repair_result FROM job_packs WHERE job_id='interest-job' AND pack_id='itinerary-day-1'").fetchone()
    assert row['status']=='valid'
    assert json.loads(row['payload'])=={'day':1,'stops':[]}
    assert json.loads(row['validation_errors'])[0]['code']=='INTEREST_DECLARATION_IGNORED'
    assert (row['generation_attempt_count'],row['repair_attempt_count'],row['resume_count'])==(1,0,0)
    assert row['repair_result']=='deprecated_interest_rule_normalized'
