import json
import logging
import sqlite3
from contextlib import contextmanager

import pytest

from app.execution import claim_next, heartbeat, recover_expired, release
from app.models import BuildInput,ResolvedPlaceFact
from app.migrations import migrate
from app.observability import classify_exception, record_error_event
from app.pipeline import BuildPaused, generate_persisted
from tests.test_build import PREFS


@pytest.fixture
def observed_db(tmp_path):
    path=tmp_path/'observability.db';migrate(path)
    @contextmanager
    def connect():
        db=sqlite3.connect(path,timeout=5);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    with connect() as db:
        db.execute("""INSERT INTO jobs(id,status,stage,progress,preferences,created_at,updated_at)
                      VALUES ('job-1','running','destination-profile',90,'{}','a','a')""")
        db.execute("""INSERT INTO job_packs(job_id,pack_id,status,payload,updated_at)
                      VALUES ('job-1','places-core','valid','{\"places\":[]}','a')""")
    return connect


@pytest.mark.parametrize(('exc','operation','category','message_fragment'),[
    (ValueError('compiler exploded'),'assemble','compiler','档案编译失败'),
    (sqlite3.OperationalError('database is locked'),'profile_persist','persistence','保存时发生错误'),
    (sqlite3.IntegrityError('constraint failed'),'job_finalize','persistence','保存时发生错误'),
    (RuntimeError('worker lease lost'),'lease_heartbeat','worker_runtime','运行状态异常'),
    (RuntimeError('unexpected'),'build_chunks','unknown_internal','内部运行错误'),
])
def test_fault_injection_persists_original_trace_and_category(observed_db,caplog,exc,operation,category,message_fragment):
    caplog.set_level(logging.ERROR)
    try:
        raise exc
    except BaseException as caught:
        error_id,classification=record_error_event(observed_db,caught,job_id='job-1',run_id='run-1',
            stage='destination-profile',operation=operation,pack_id='destination-profile',substage='finalize')
    assert error_id
    assert classification.category==category and message_fragment in classification.user_message
    with observed_db() as db:
        row=db.execute('SELECT * FROM error_events WHERE error_id=?',(error_id,)).fetchone()
        pack=db.execute("SELECT status,payload FROM job_packs WHERE job_id='job-1' AND pack_id='places-core'").fetchone()
    assert row['exception_type']==type(exc).__name__
    assert row['exception_message']==str(exc)
    assert f'{type(exc).__name__}: {exc}' in row['traceback']
    assert row['error_category']==category and row['operation']==operation
    assert row['user_message']==classification.user_message
    assert row['developer_error'].startswith(f'{type(exc).__name__}: {exc}')
    assert pack['status']=='valid' and json.loads(pack['payload'])=={'places':[]}
    assert any(record.exc_info for record in caplog.records if record.message=='build failed')


def test_unknown_error_is_never_misreported_as_provider():
    result=classify_exception(RuntimeError('boom'),'build_chunks')
    assert result.category=='unknown_internal'
    assert '模型' not in result.user_message and '智谱' not in result.user_message


def test_pydantic_contract_error_is_classified_as_validation():
    with pytest.raises(Exception) as caught:
        ResolvedPlaceFact.model_validate({'value':None,'source_url':None,'source_type':None,'extracted_at':None,
            'confidence':0,'raw_evidence':'','status':'unavailable','sources':[],'unexpected':True})
    result=classify_exception(caught.value,'build_chunks')
    assert result.category=='validation'
    assert '数据契约' in result.user_message


def test_worker_lease_lifecycle_is_persisted(tmp_path):
    path=tmp_path/'leases.db';migrate(path)
    @contextmanager
    def connect():
        db=sqlite3.connect(path,timeout=5);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    with connect() as db:
        db.execute("INSERT INTO jobs(id,status,stage,progress,preferences,created_at,updated_at) VALUES ('lease-job','queued','queued',0,'{}','a','a')")
    claimed=claim_next(connect,'test-worker')
    assert heartbeat(connect,'lease-job',claimed['run_id'],'test-worker')
    assert release(connect,'lease-job',claimed['run_id'],status='paused',stage='worker_runtime',current_operation='paused')
    with connect() as db:
        events=[row['event_type'] for row in db.execute("SELECT event_type FROM execution_events WHERE job_id='lease-job' ORDER BY created_at")]
    assert events==['lease_acquired','lease_released']


def test_expired_lease_records_restart_resume_reason(tmp_path):
    path=tmp_path/'expired.db';migrate(path)
    @contextmanager
    def connect():
        db=sqlite3.connect(path,timeout=5);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    with connect() as db:
        db.execute("""INSERT INTO jobs(id,status,stage,progress,preferences,created_at,updated_at,run_id,lock_owner,lease_expires_at)
          VALUES ('expired','running','itinerary-plan',30,'{}','a','a','old-run','old-worker','2000-01-01T00:00:00+00:00')""")
    assert recover_expired(connect)==1
    with connect() as db:
        event=db.execute("SELECT * FROM execution_events WHERE job_id='expired'").fetchone()
        job=db.execute("SELECT status,current_operation FROM jobs WHERE id='expired'").fetchone()
    assert event['event_type']=='lease_expired_recovered'
    assert json.loads(event['details'])['resume_reason']=='lease_expired'
    assert tuple(job)==('queued','lease_recovery')


class _AsyncClient:
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False


class _FinalStore:
    def __init__(self,connect,save_error=None):
        self.connect=connect;self.job_id='job-1';self.run_id='run-1';self.days=2;self.save_error=save_error
    def summary(self):return {'current_pack':'destination-profile'}
    def active_subrequest_pack(self):return None
    def latest_subrequest(self,pack_id=None):return None
    def mark_running(self,pack_id):pass
    def expected_signature(self,*args):return 'signature'
    def save_valid(self,pack_id,*args,**kwargs):
        if pack_id=='destination-profile' and self.save_error:raise self.save_error


def _prepare_running_job(connect):
    with connect() as db:
        db.execute("UPDATE jobs SET run_id='run-1',lock_owner='worker',status='running' WHERE id='job-1'")


def test_destination_finalization_boundaries_are_persisted(observed_db,monkeypatch):
    import app.pipeline as pipeline
    _prepare_running_job(observed_db)
    monkeypatch.setenv('ZHIPU_API_KEY','configured')
    monkeypatch.setattr(pipeline,'AsyncOpenAI',lambda **kwargs:_AsyncClient())
    async def chunks(*args,**kwargs):return {'packs':'ok'}
    monkeypatch.setattr(pipeline,'build_chunks',chunks)
    monkeypatch.setattr(pipeline,'assemble',lambda *args,**kwargs:{'profile':'ok'})
    monkeypatch.setattr(pipeline,'check_handoff',lambda *args,**kwargs:[])
    result,_=__import__('asyncio').run(generate_persisted(BuildInput(**PREFS),'job-1',lambda *args:None,_FinalStore(observed_db)))
    assert result=={'profile':'ok'}
    with observed_db() as db:
        events=[row['event_type'] for row in db.execute("SELECT event_type FROM execution_events WHERE job_id='job-1' ORDER BY created_at")]
    assert events==['build_chunks_started','build_chunks_completed','assemble_started','assemble_completed',
                    'check_handoff_started','check_handoff_completed','profile_persist_started','profile_persist_completed']


@pytest.mark.parametrize(('operation_error','category'),[
    (ValueError('compiler injection'),'compiler'),
    (sqlite3.OperationalError('database is locked'),'persistence'),
])
def test_pipeline_fault_injection_keeps_original_exception(observed_db,monkeypatch,operation_error,category):
    import app.pipeline as pipeline
    _prepare_running_job(observed_db)
    monkeypatch.setenv('ZHIPU_API_KEY','configured')
    monkeypatch.setattr(pipeline,'AsyncOpenAI',lambda **kwargs:_AsyncClient())
    async def chunks(*args,**kwargs):return {'packs':'ok'}
    monkeypatch.setattr(pipeline,'build_chunks',chunks)
    if category=='compiler':
        def broken_assemble(*args,**kwargs):raise operation_error
        monkeypatch.setattr(pipeline,'assemble',broken_assemble)
        store=_FinalStore(observed_db)
    else:
        monkeypatch.setattr(pipeline,'assemble',lambda *args,**kwargs:{'profile':'ok'})
        monkeypatch.setattr(pipeline,'check_handoff',lambda *args,**kwargs:[])
        store=_FinalStore(observed_db,operation_error)
    with pytest.raises(BuildPaused) as caught:
        __import__('asyncio').run(generate_persisted(BuildInput(**PREFS),'job-1',lambda *args:None,store))
    assert caught.value.error_category==category and caught.value.error_id
    with observed_db() as db:
        row=db.execute('SELECT * FROM error_events WHERE error_id=?',(caught.value.error_id,)).fetchone()
        pack=db.execute("SELECT status FROM job_packs WHERE job_id='job-1' AND pack_id='places-core'").fetchone()
    assert row['exception_message']==str(operation_error)
    assert row['error_category']==category
    assert f'{type(operation_error).__name__}: {operation_error}' in row['traceback']
    assert pack['status']=='valid'


def test_final_status_update_failure_is_recorded_without_stopping_worker(observed_db,monkeypatch):
    import app.main as main
    _prepare_running_job(observed_db)
    monkeypatch.setattr(main,'connect',observed_db)
    def locked_release(*args,**kwargs):raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(main,'release',locked_release)
    assert not main._release_after_error('job-1','run-1',status='paused',stage='persistence',
        current_operation='paused',error='{}')
    with observed_db() as db:
        event=db.execute("SELECT * FROM error_events WHERE job_id='job-1' ORDER BY created_at DESC LIMIT 1").fetchone()
    assert event['error_category']=='persistence'
    assert event['operation']=='job_finalize'
    assert 'database is locked' in event['traceback']
