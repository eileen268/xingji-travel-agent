import asyncio
import hashlib
import json
import os
import logging
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from .execution import WORKER_ID, claim_next, heartbeat, recover_expired, release
from .manifest import generation_manifest
from .migrations import migrate
from .models import BuildInput
from .pack_store import PackStore
from .pipeline import BuildPaused, generate_mock_persisted, generate_persisted, now
from .replay import REPLAY_POINTS, replay_saved
from .validators import check_handoff
from .observability import record_error_event, record_execution_event
from .place_names import normalize_profile_names
from .warnings import dedupe_warnings
from .replan import ReplanError, ReplanRequest, replan_options, replan_trip
from .editable_replan import (CustomActivityRequest, DayTransportRequest, InstructionRequest, SearchAddRequest,
    SegmentTransportRequest, StopEditRequest, confirm_preview, custom_add, delete_stop, edit_stop,
    instruction_preview, search_add, set_day_transport, set_segment_transport, undo_day)

ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/'.env')
logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO').upper(),
                    format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger=logging.getLogger(__name__)


def database_path():
    path=Path(os.getenv('DATABASE_PATH',str(ROOT/'data/travel.db')))
    return path if path.is_absolute() else ROOT/path


@contextmanager
def connect():
    path=database_path(); path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=15); db.row_factory=sqlite3.Row
    try: yield db
    except BaseException: db.rollback(); raise
    else: db.commit()
    finally: db.close()


def update(job_id, run_id=None, **fields):
    with connect() as db:
        fields['updated_at']=now(); where=' WHERE id=?'+(' AND run_id=?' if run_id else '')
        params=[*fields.values(),job_id]+([run_id] if run_id else [])
        return db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+where,params).rowcount==1


def _release_after_error(job_id,run_id,*,status,stage,error,current_operation,error_id=None):
    """Best-effort terminal transition; never let a second persistence error kill the worker loop."""
    try:
        return release(connect,job_id,run_id,status=status,stage=stage,current_operation=current_operation,
                       last_error_event_id=error_id,error=error)
    except Exception as release_exc:
        record_error_event(connect,release_exc,job_id=job_id,run_id=run_id,stage=stage,
                           operation='job_finalize',pack_id=stage,worker_id=WORKER_ID)
        logger.exception('failed to persist job error state',extra={'job_id':job_id,'run_id':run_id,
                         'stage':stage,'operation':'job_finalize'})
        return False


async def _lease_loop(job_id,run_id):
    while True:
        await asyncio.sleep(10)
        if not heartbeat(connect,job_id,run_id,WORKER_ID): return


async def worker():
    while True:
        row=claim_next(connect,WORKER_ID)
        if not row:
            await asyncio.sleep(.4); continue
        job_id=row['id']; run_id=row['run_id']; lease_task=asyncio.create_task(_lease_loop(job_id,run_id))
        current_operation='worker'
        try:
            t=BuildInput.model_validate_json(row['preferences'])
            manifest=json.loads(row['manifest']) if row.get('manifest') else generation_manifest(row.get('mode') or 'REAL_LLM')
            store=PackStore(connect,job_id,t.model_dump(mode='json'),t.days,manifest,run_id); store.ensure()
            def progress(stage,pct,status=None):
                status=status or ('validating' if stage in ('validating','itinerary-coherence','destination-profile') else 'running')
                fields={'status':status,'stage':stage}
                if pct is not None:fields['progress']=pct
                update(job_id,run_id,**fields)
            if row.get('mode')=='REPLAY':
                current_operation='build_chunks'
                source_row=job(row['source_job_id'])
                source_manifest=json.loads(source_row['manifest']) if source_row['manifest'] else {}
                source_store=PackStore(connect,source_row['id'],t.model_dump(mode='json'),t.days,source_manifest)
                result,packs=await replay_saved(t,job_id,source_store,store,row['replay_from'],manifest,progress)
            elif row.get('mode')=='MOCK':
                current_operation='build_chunks'
                result,packs=await generate_mock_persisted(t,job_id,progress,store)
                result['generation']['manifest']=manifest
            else:
                current_operation='build_chunks'
                result,packs=await generate_persisted(t,job_id,progress,store)
                result['generation']['manifest']=manifest
            if row.get('mode')!='REPLAY':
                from .pack_graph import dependencies
                store.save_valid('destination-profile',result,
                    store.expected_signature('destination-profile',dependencies('destination-profile',store.days)),
                    observability={'repair_result':'not_needed'},source_payload=result)
            current_operation='check_handoff'
            errors=check_handoff(result,packs)
            if errors: raise ValueError('交付校验失败')
            final_status='done_with_warnings' if result['generation'].get('warnings') else 'done'
            current_operation='job_finalize'
            record_execution_event(connect,event_type='job_finalize_started',job_id=job_id,run_id=run_id,
                                   worker_id=WORKER_ID,operation='job_finalize',update_current=True)
            released=release(connect,job_id,run_id,status=final_status,stage='done',progress=100,current_operation='completed',
                    result=json.dumps(result,ensure_ascii=False),packs=json.dumps(packs,ensure_ascii=False),
                    report=json.dumps({'passed':True,'errors':[]},ensure_ascii=False),error=None)
            if not released: raise RuntimeError('worker lease lost before final status update')
            record_execution_event(connect,event_type='job_finalize_completed',job_id=job_id,run_id=run_id,
                                   worker_id=WORKER_ID,operation='job_finalize')
        except asyncio.CancelledError:
            record_execution_event(connect,event_type='worker_cancelled',job_id=job_id,run_id=run_id,
                                   worker_id=WORKER_ID,operation=current_operation,
                                   details={'resume_reason':'worker_shutdown_or_reload'})
            _release_after_error(job_id,run_id,status='queued',stage='queued',current_operation='worker_cancelled',
                                 error=None)
            raise
        except BuildPaused as exc:
            _release_after_error(job_id,run_id,status='paused',stage=exc.pack_id,current_operation='paused',
                error_id=exc.error_id,error=json.dumps({'code':'BUILD_PAUSED','message':str(exc),
                'category':exc.error_category,'error_id':exc.error_id},ensure_ascii=False))
        except Exception as exc:
            error_id,classification=record_error_event(connect,exc,job_id=job_id,run_id=run_id,
                stage=row.get('stage') or 'worker',operation=current_operation,worker_id=WORKER_ID)
            _release_after_error(job_id,run_id,status='paused' if classification.recoverable else 'failed',
                stage=classification.category,current_operation='paused' if classification.recoverable else 'failed',
                error_id=error_id,error=json.dumps({'code':'BUILD_PAUSED' if classification.recoverable else 'BUILD_FAILED',
                'message':classification.user_message,'category':classification.category,'error_id':error_id},ensure_ascii=False))
        finally:
            lease_task.cancel()
            try: await lease_task
            except asyncio.CancelledError: pass


@asynccontextmanager
async def lifespan(app):
    migrate(database_path())
    record_execution_event(connect,event_type='worker_started',worker_id=WORKER_ID,operation='worker',
                           details={'resume_reason':'process_start'})
    recover_expired(connect)
    task=asyncio.create_task(worker()); yield; task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    record_execution_event(connect,event_type='worker_stopped',worker_id=WORKER_ID,operation='worker',
                           details={'reason':'lifespan_shutdown_or_reload'})


app=FastAPI(title='行迹 Journey Notes API',version='1.3.0',lifespan=lifespan)


@app.get('/')
async def root(): return {'service':'行迹 Journey Notes API','status':'ok','docs':'/docs'}


@app.get('/_AMapService/{upstream_path:path}')
async def amap_service_proxy(upstream_path: str, request: Request):
    """高德 JS API 2.0 安全密钥代理（官方"代理服务器转发"方案）。

    前端将 window._AMapSecurityConfig.serviceHost 指向同源 /_AMapService，
    JSAPI 把对高德 Web 服务的请求（含 v3/assistant/security/jscode 动态密钥
    换取、地图样式等）发到本前缀；本服务对每个请求在服务端注入安全密钥
    jscode 后按官方规则分发到对应高德主机，并原样透传响应。安全密钥永不下发。
    仅允许转发到高德官方主机的白名单路径，避免成为开放代理。
    """
    security_code=os.getenv('AMAP_SECURITY_JS_CODE','').strip()
    if not security_code:
        raise HTTPException(503,detail={'code':'AMAP_SECURITY_NOT_CONFIGURED',
                                        'message':'地图安全代理未配置 AMAP_SECURITY_JS_CODE'})
    if upstream_path == 'v4/map/styles' or upstream_path.startswith('v4/map/styles/'):
        upstream_base='https://webapi.amap.com'
    elif upstream_path == 'v3/vectormap' or upstream_path.startswith('v3/vectormap/'):
        upstream_base='https://fmap01.amap.com'
    elif upstream_path.startswith('v3/') or upstream_path.startswith('v4/') or upstream_path.startswith('v5/'):
        upstream_base='https://restapi.amap.com'
    else:
        raise HTTPException(404,detail={'code':'AMAP_PROXY_PATH_NOT_ALLOWED',
                                        'message':'该路径不允许通过地图代理访问'})
    params=[(k,v) for k,v in request.query_params.multi_items() if k!='jscode']
    params.append(('jscode',security_code))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0,connect=5.0),follow_redirects=False) as client:
            upstream=await client.get(f'{upstream_base}/{upstream_path}',params=params)
    except httpx.HTTPError:
        raise HTTPException(502,detail={'code':'AMAP_PROXY_UPSTREAM_FAILED',
                                        'message':'地图服务暂时不可用'}) from None
    headers={'Cache-Control':'no-store'}
    if 'content-type' in upstream.headers:
        headers['Content-Type']=upstream.headers['content-type']
    return Response(content=upstream.content,status_code=upstream.status_code,headers=headers)


@app.exception_handler(RequestValidationError)
async def input_error(request,exc):
    return JSONResponse(status_code=422,content={'error':{'code':'INVALID_PREFERENCES','message':'请检查必填字段与输入范围','details':[{'pointer':'/'+ '/'.join(map(str,e['loc'][1:])), 'message':e['msg']} for e in exc.errors()]}})


@app.exception_handler(sqlite3.Error)
async def database_error(request,exc):
    return JSONResponse(status_code=503,content={'error':{'code':'DATABASE_UNAVAILABLE','message':'档案存储暂时不可用，请稍后重试'}})


def _request_signature(preferences: BuildInput):
    raw=json.dumps(preferences.model_dump(mode='json'),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(raw.encode()).hexdigest()


@app.post('/api/build',status_code=202)
async def build(preferences: BuildInput, idempotency_key: str | None=Header(default=None,alias='Idempotency-Key'),
                agent_mode: str | None=Header(default=None,alias='X-Agent-Mode')):
    mode=(agent_mode or 'REAL_LLM').upper()
    if mode not in ('REAL_LLM','MOCK'):
        raise HTTPException(400,detail={'code':'INVALID_MODE','message':'新任务模式只能是 REAL_LLM 或 MOCK；REPLAY 请使用 replay 接口'})
    if mode=='MOCK' and os.getenv('ENABLE_DEV_MODES','').lower() not in ('1','true','yes'):
        raise HTTPException(403,detail={'code':'DEV_MODE_DISABLED','message':'MOCK 仅在 ENABLE_DEV_MODES=1 时可用'})
    signature=_request_signature(preferences)
    if idempotency_key:
        with connect() as db: existing=db.execute('SELECT id,status,request_signature FROM jobs WHERE idempotency_key=?',(idempotency_key,)).fetchone()
        if existing:
            if existing['request_signature']!=signature:
                raise HTTPException(409,detail={'code':'IDEMPOTENCY_CONFLICT','message':'同一 Idempotency-Key 不能提交不同偏好'})
            return {'job_id':existing['id'],'status':existing['status'],'idempotent_replay':True}
    job_id=str(uuid.uuid4()); stamp=now(); manifest=generation_manifest(mode)
    try:
        with connect() as db:
            db.execute('''INSERT INTO jobs (id,status,stage,progress,preferences,created_at,updated_at,mode,manifest,idempotency_key,request_signature)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(job_id,'queued','queued',0,preferences.model_dump_json(),stamp,stamp,mode,
                          json.dumps(manifest,ensure_ascii=False),idempotency_key,signature))
    except sqlite3.IntegrityError:
        with connect() as db: existing=db.execute('SELECT id,status,request_signature FROM jobs WHERE idempotency_key=?',(idempotency_key,)).fetchone()
        if existing and existing['request_signature']==signature:
            return {'job_id':existing['id'],'status':existing['status'],'idempotent_replay':True}
        raise HTTPException(409,detail={'code':'IDEMPOTENCY_CONFLICT','message':'同一 Idempotency-Key 不能提交不同偏好'})
    return {'job_id':job_id,'status':'queued','mode':mode}


def job(job_id):
    with connect() as db: row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if row is None: raise HTTPException(404,detail={'code':'NOT_FOUND','message':'任务不存在'})
    return row


@app.get('/api/build/{job_id}')
async def status(job_id: str):
    row=job(job_id); result=json.loads(row['result']) if row['result'] else None
    if result and isinstance(result.get('generation'),dict):
        result['generation']['warnings']=dedupe_warnings(('legacy_profile',result['generation'].get('warnings',[])))
    t=BuildInput.model_validate_json(row['preferences']); manifest=json.loads(row['manifest']) if row['manifest'] else {}
    store=PackStore(connect,row['id'],t.model_dump(mode='json'),t.days,manifest,row['run_id']); store.ensure(); summary=store.summary()
    complete=row['status'] in ('done','done_with_warnings')
    progress=100 if complete else max(row['progress'],round(summary['completed_packs']/max(1,summary['total_packs'])*95))
    return {'job_id':row['id'],'status':row['status'],'stage':row['stage'],'progress':progress,'mode':row['mode'],
            'manifest':manifest,'run_id':row['run_id'],'execution_attempt_count':row['execution_attempt_count'],
            'degraded':result['generation']['degraded'] if result else row['stage']=='offline',
            'warnings':result['generation']['warnings'] if result else [],'error':json.loads(row['error']) if row['error'] else None,
            **summary,'poi_resolution_metrics':store.poi_resolution_metrics(),'route_metrics':store.route_metrics(),
            'fact_verification_metrics':store.fact_verification_metrics(),
            'repairing':row['status']=='repairing'}


@app.get('/api/debug/build/{job_id}/errors')
async def debug_errors(job_id: str):
    """Developer-only durable exception and execution timeline."""
    if os.getenv('ENABLE_DEV_MODES','').lower() not in ('1','true','yes'):
        raise HTTPException(403,detail={'code':'DEV_MODE_DISABLED','message':'错误详情接口仅在 ENABLE_DEV_MODES=1 时可用'})
    job(job_id)
    with connect() as db:
        errors=[dict(row) for row in db.execute(
            'SELECT * FROM error_events WHERE job_id=? ORDER BY created_at,error_id',(job_id,)).fetchall()]
        events=[dict(row) for row in db.execute(
            'SELECT * FROM execution_events WHERE job_id=? ORDER BY created_at,event_id',(job_id,)).fetchall()]
        resolutions=[dict(row) for row in db.execute(
            'SELECT * FROM place_resolutions WHERE job_id=? ORDER BY created_at,resolution_id',(job_id,)).fetchall()]
        schedule_runs=[dict(row) for row in db.execute(
            'SELECT * FROM schedule_runs WHERE job_id=? ORDER BY started_at,schedule_run_id',(job_id,)).fetchall()]
        fact_runs=[dict(row) for row in db.execute(
            'SELECT * FROM fact_verification_runs WHERE job_id=? ORDER BY started_at,verification_run_id',(job_id,)).fetchall()]
        official_resolutions=[dict(row) for row in db.execute(
            'SELECT * FROM official_source_resolutions WHERE job_id=? ORDER BY resolved_at,resolution_id',(job_id,)).fetchall()]
        official_pages=[dict(row) for row in db.execute('''SELECT page_type,url,final_url,http_status,content_hash,
            fetched_at,expires_at,error FROM official_page_cache WHERE url IN
            (SELECT official_url FROM official_source_resolutions WHERE job_id=? AND official_url IS NOT NULL)
            ORDER BY fetched_at,url''',(job_id,)).fetchall()]
    for row in errors: row['recoverable']=bool(row['recoverable'])
    for row in events: row['details']=json.loads(row['details'] or '{}')
    for row in resolutions:row['candidates']=json.loads(row.pop('candidates_json') or '[]')
    for row in schedule_runs:
        row['warnings']=json.loads(row['warnings'] or '[]')
        row['payload']=json.loads(row['payload']) if row['payload'] else None
    route_keys=set()
    mode_labels={'步行':'walking','驾车或出租车':'driving','公共交通':'transit'}
    for run in schedule_runs:
        stops=(run.get('payload') or {}).get('stops') or []
        for previous,current in zip(stops,stops[1:]):
            trace=current.get('route_input') or {}
            origin=trace.get('origin_place_id') or previous.get('place_id')
            destination=trace.get('destination_place_id') or current.get('place_id')
            mode=trace.get('selected_mode') or mode_labels.get(current.get('transport_mode'))
            if origin and destination and mode:route_keys.add((origin,destination,mode))
    route_diagnostics=[]
    if route_keys:
        with connect() as db:
            for origin,destination,mode in sorted(route_keys):
                row=db.execute('''SELECT * FROM routes WHERE origin_place_id=? AND destination_place_id=? AND mode=?''',
                    (origin,destination,mode)).fetchone()
                if row:route_diagnostics.append(dict(row))
    for row in fact_runs:row['summary']=json.loads(row.pop('summary_json'))
    for row in official_resolutions:row['candidates']=json.loads(row.pop('candidates_json') or '[]')
    total=len(resolutions);verified=sum(row['status']=='exact_match' for row in resolutions)
    metrics={
        'poi_resolution_rate':round(sum(row['selected_place_id'] is not None for row in resolutions)/total,3) if total else 0,
        'verified_poi_rate':round(verified/total,3) if total else 0,
        'ambiguous_poi_rate':round(sum(row['status'] in ('multiple_candidates','ambiguous') for row in resolutions)/total,3) if total else 0,
        'generated_experience_rate':round(sum(row['status']=='generated_experience' for row in resolutions)/total,3) if total else 0,
    }
    return {'job_id':job_id,'errors':errors,'execution_events':events,'place_resolutions':resolutions,
            'fact_verification_runs':fact_runs,'official_source_resolutions':official_resolutions,
            'official_page_fetches':official_pages,
            'schedule_runs':schedule_runs,'route_diagnostics':route_diagnostics,'poi_resolution_metrics':metrics,
            'route_metrics':PackStore(connect,job_id,{},1,{}).route_metrics()}


@app.post('/api/build/{job_id}/resume',status_code=202)
async def resume(job_id: str):
    row=job(job_id)
    if row['status'] in ('queued','running','repairing','validating'):
        return {'job_id':job_id,'status':row['status'],'idempotent_replay':True}
    if row['status'] not in ('paused','done_with_warnings'):
        raise HTTPException(409,detail={'code':'NOT_RESUMABLE','message':'该任务当前不能继续生成','status':row['status']})
    t=BuildInput.model_validate_json(row['preferences']); manifest=json.loads(row['manifest']) if row['manifest'] else {}
    store=PackStore(connect,row['id'],t.model_dump(mode='json'),t.days,manifest); store.ensure()
    if row['status']=='done_with_warnings' and 'timed_out' not in store.summary()['pack_statuses'].values():
        raise HTTPException(409,detail={'code':'NO_PENDING_ENRICHMENT','message':'当前没有待补充的超时内容','status':row['status']})
    store.resume()
    update(job_id,status='queued',stage='queued',error=None)
    return {'job_id':job_id,'status':'queued','idempotent_replay':False}


class ReplayRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    from_pack: str='itinerary-plan'


@app.post('/api/build/{job_id}/replay',status_code=202)
async def replay(job_id: str, request: ReplayRequest):
    if os.getenv('ENABLE_DEV_MODES','').lower() not in ('1','true','yes'):
        raise HTTPException(403,detail={'code':'DEV_MODE_DISABLED','message':'Replay 仅在 ENABLE_DEV_MODES=1 时可用'})
    source=job(job_id)
    if source['status'] not in ('done','done_with_warnings','paused'):
        raise HTTPException(409,detail={'code':'SOURCE_NOT_STABLE','message':'只能 Replay 已完成或暂停的任务'})
    if request.from_pack not in REPLAY_POINTS and not request.from_pack.startswith('itinerary-day-'):
        raise HTTPException(422,detail={'code':'INVALID_REPLAY_POINT','message':'不支持的 Replay 起点'})
    new_id=str(uuid.uuid4()); stamp=now(); manifest=generation_manifest('REPLAY')
    with connect() as db:
        db.execute('''INSERT INTO jobs (id,status,stage,progress,preferences,created_at,updated_at,mode,source_job_id,replay_from,manifest,request_signature)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',(new_id,'queued','queued',0,source['preferences'],stamp,stamp,'REPLAY',job_id,
                      request.from_pack,json.dumps(manifest,ensure_ascii=False),source['request_signature']))
    return {'job_id':new_id,'status':'queued','mode':'REPLAY','source_job_id':job_id,'replay_from':request.from_pack}


@app.get('/api/build/{job_id}/result')
async def result(job_id: str):
    row=job(job_id)
    if not row['result'] or row['status'] in ('failed','paused'):
        raise HTTPException(409,detail={'code':'RESULT_NOT_READY','message':'完整档案尚不可用','status':row['status']})
    profile=normalize_profile_names(json.loads(row['result']))
    if isinstance(profile.get('generation'),dict):
        profile['generation']['warnings']=dedupe_warnings(('legacy_profile',profile['generation'].get('warnings',[])))
    # Compatibility for schedules written before route-sanity v5. Missing
    # provider facts were historically serialized as zero. Never expose those
    # sentinels as real travel facts; the next day edit persists canonical nulls.
    for day in profile.get('itinerary',[]):
        for stop in day.get('stops',[]):
            invalid_duration=stop.get('transfer_minutes') is not None and stop['transfer_minutes']<=0
            invalid_distance=stop.get('distance_km') is not None and stop['distance_km']<=0
            if invalid_duration:stop['transfer_minutes']=None
            if invalid_distance:stop['distance_km']=None
            if invalid_duration or invalid_distance:
                stop['route_status']='unresolved'
                stop.setdefault('route_error_code','ROUTE_FACT_UNAVAILABLE')
        if not day.get('segments'):
            day['segments']=[]
            for previous,current in zip(day.get('stops',[]),day.get('stops',[])[1:]):
                raw_mode=str(current.get('transport_mode',''))
                mode='walking' if '步行' in raw_mode else ('public_transit' if '公共' in raw_mode else 'driving')
                day['segments'].append({'segment_id':f'{previous["place_id"]}->{current["place_id"]}',
                    'from_place_id':previous['place_id'],'to_place_id':current['place_id'],'mode':mode,
                    'distance_meters':round(current['distance_km']*1000) if current.get('distance_km') else None,
                    'duration_minutes':current.get('transfer_minutes'),'buffer_minutes':current.get('buffer_minutes',0),
                    'fallback_schedule_minutes':current.get('fallback_schedule_minutes',0),'idle_minutes':current.get('idle_minutes',0),
                    'route_status':current.get('route_status','unresolved') if current.get('route_status')!='not_applicable' else 'unresolved',
                    'route_provider':current.get('route_provider'),'route_queried_at':current.get('route_queried_at'),
                    'route_input':current.get('route_input'),'route_error_code':current.get('route_error_code'),
                    'route_http_status':current.get('route_http_status'),'route_provider_code':current.get('route_provider_code')})
    return profile


@app.get('/api/trips')
async def trips():
    with connect() as db: rows=db.execute("SELECT id,result,updated_at,status FROM jobs WHERE status IN ('done','done_with_warnings') ORDER BY updated_at DESC").fetchall()
    items=[]
    for row in rows:
        p=json.loads(row['result']); t=p['trip']
        items.append(dict(id=row['id'],title=p['display_name'],startDate=t['startDate'],endDate=t['endDate'],destinations=t['destinations'],
            travelers=dict(adults=t['adults'],children=t['children'],seniors=t['seniors'],style=t['travelStyle']),budget=t['budget'],
            updatedAt=row['updated_at'],status='completed_with_warnings' if row['status']=='done_with_warnings' else 'completed',degraded=p['generation']['degraded']))
    return {'items':items}


@app.post('/api/trips/{job_id}/replan')
async def local_replan(job_id: str, request: ReplanRequest,
                       idempotency_key: str | None=Header(default=None,alias='Idempotency-Key')):
    try:
        return await replan_trip(connect,job_id,request,idempotency_key)
    except ReplanError as exc:
        raise HTTPException(exc.status_code,detail={'code':exc.code,'message':str(exc)}) from None


@app.get('/api/trips/{job_id}/replan-options')
async def local_replan_options(job_id: str, day: int):
    try:
        return replan_options(connect, job_id, day)
    except ReplanError as exc:
        raise HTTPException(exc.status_code, detail={'code': exc.code, 'message': str(exc)}) from None


def _editable_error(exc: ReplanError, *, scope='current_day', affected_day_ids=None):
    messages={
        'REPLAN_NO_FEASIBLE_SCHEDULE':('暂时无法按这个方式安排',f'{exc}。原行程没有变化。'),
        'REPLAN_INSTRUCTION_PARSE_FAILED':('没有完全理解你的调整要求','可以尝试写得更明确，例如：“迪士尼单独安排一天，其他景点尽量移到第3、4天。”原行程没有变化。'),
        'REPLAN_PROVIDER_UNAVAILABLE':('智能调整服务暂时不可用','原行程没有变化，请稍后再试。'),
        'REPLAN_PLACE_NOT_FOUND':('没有找到这个地点',f'{exc}。原行程没有变化。'),
        'REPLAN_PLACE_AMBIGUOUS':('需要确认具体地点',f'{exc}。原行程没有变化。'),
        'REPLAN_LOCKED_STOP_CONFLICT':('调整与已锁定安排冲突',f'{exc}。请先解锁该地点，原行程没有变化。'),
        'REPLAN_NO_FEASIBLE_SLOT':('暂时找不到合适时间',f'{exc}。原行程没有变化。'),
        'REPLAN_OPENING_HOURS_CONFLICT':('这个时间与营业时间冲突',f'{exc}。原行程没有变化。'),
        'REPLAN_ROUTE_INFEASIBLE':('这样的路线暂时无法执行',f'{exc}。原行程没有变化。'),
        'REPLAN_ROUTE_UNRESOLVED':('路线暂时无法可靠计算',f'{exc}。原行程没有变化。'),
        'REPLAN_CROSS_CITY_CONFLICT':('这项调整存在跨城市冲突',f'{exc}。原行程没有变化。'),
        'REPLAN_DAY_CAPACITY_EXCEEDED':('目标日期没有足够时间',f'{exc}。原行程没有变化。'),
        'REPLAN_INSERTION_CONFLICT':('新增安排与现有日程冲突',f'{exc}。原行程没有变化。'),
        'REPLAN_SCOPE_CONFIRMATION_REQUIRED':('这项调整会影响其他日期',f'{exc}。请先确认调整范围，原行程没有变化。'),
    }
    title,message=messages.get(exc.code,('暂时无法完成这项调整',f'{exc}。原行程没有变化。'))
    reasons=[part for part in str(exc).split('；') if part] if exc.code=='REPLAN_NO_FEASIBLE_SCHEDULE' else []
    details=exc.details or [{'type':'reason','message':reason} for reason in reasons]
    detail={'code':exc.code,'user_title':title,'user_message':message,'scope':scope,
            'affected_day_ids':affected_day_ids or [],'details':details}
    raise HTTPException(exc.status_code,detail=detail) from None


def _editable_internal(job_id,operation,exc):
    error_id,_=record_error_event(connect,exc,job_id=job_id,run_id=None,stage='editable-replan',operation=operation)
    raise HTTPException(500,detail={'code':'REPLAN_INTERNAL_ERROR','user_title':'暂时无法生成调整预览',
        'user_message':'原行程没有变化，请稍后再试。','scope':'current_day','affected_day_ids':[],
        'details':[{'type':'error_reference','message':'错误记录已保存','error_id':error_id}]}) from None


@app.post('/api/trips/{job_id}/days/{day_id}/stops/search-add')
async def editable_search_add(job_id: str, day_id: str, request: SearchAddRequest):
    try:return await search_add(connect,job_id,day_id,request)
    except ReplanError as exc:_editable_error(exc)
    except Exception as exc:_editable_internal(job_id,'editable_search_add',exc)


@app.post('/api/trips/{job_id}/days/{day_id}/custom-activities')
async def editable_custom_add(job_id: str, day_id: str, request: CustomActivityRequest):
    try:return await custom_add(connect,job_id,day_id,request)
    except ReplanError as exc:_editable_error(exc)
    except Exception as exc:_editable_internal(job_id,'editable_custom_add',exc)


@app.patch('/api/trips/{job_id}/days/{day_id}/stops/{place_id}')
async def editable_stop_update(job_id: str, day_id: str, place_id: str, request: StopEditRequest):
    try:return await edit_stop(connect,job_id,day_id,place_id,request)
    except ReplanError as exc:_editable_error(exc)
    except Exception as exc:_editable_internal(job_id,'editable_stop_update',exc)


@app.delete('/api/trips/{job_id}/days/{day_id}/stops/{place_id}')
async def editable_stop_delete(job_id: str, day_id: str, place_id: str):
    try:return await delete_stop(connect,job_id,day_id,place_id)
    except ReplanError as exc:_editable_error(exc)
    except Exception as exc:_editable_internal(job_id,'editable_stop_delete',exc)


@app.patch('/api/trips/{job_id}/days/{day_id}/transport')
async def editable_day_transport(job_id: str, day_id: str, request: DayTransportRequest):
    try:return await set_day_transport(connect,job_id,day_id,request)
    except ReplanError as exc:_editable_error(exc)
    except Exception as exc:_editable_internal(job_id,'editable_day_transport',exc)


@app.patch('/api/trips/{job_id}/days/{day_id}/segments/transport')
async def editable_segment_transport(job_id: str, day_id: str, request: SegmentTransportRequest):
    try:return await set_segment_transport(connect,job_id,day_id,request)
    except ReplanError as exc:_editable_error(exc)
    except Exception as exc:_editable_internal(job_id,'editable_segment_transport',exc)


@app.post('/api/trips/{job_id}/days/{day_id}/replan-preview')
async def editable_instruction_preview(job_id: str, day_id: str, request: InstructionRequest):
    try:return await instruction_preview(connect,job_id,day_id,request)
    except ReplanError as exc:_editable_error(exc)
    except Exception as exc:_editable_internal(job_id,'editable_instruction_preview',exc)


@app.post('/api/trips/{job_id}/replan-preview')
async def editable_trip_instruction_preview(job_id: str, request: InstructionRequest):
    try:
        day_id=request.anchor_day_id or f'{job_id}-day-1'
        return await instruction_preview(connect,job_id,day_id,request)
    except ReplanError as exc:_editable_error(exc,scope='whole_trip')
    except Exception as exc:_editable_internal(job_id,'editable_trip_instruction_preview',exc)


@app.post('/api/trips/{job_id}/replan-previews/{preview_id}/confirm')
async def editable_preview_confirm(job_id: str, preview_id: str):
    try:return await confirm_preview(connect,job_id,preview_id)
    except ReplanError as exc:_editable_error(exc)
    except Exception as exc:_editable_internal(job_id,'editable_preview_confirm',exc)


@app.post('/api/trips/{job_id}/days/{day_id}/undo')
async def editable_day_undo(job_id: str, day_id: str):
    try:return await undo_day(connect,job_id,day_id)
    except ReplanError as exc:_editable_error(exc)
    except Exception as exc:_editable_internal(job_id,'editable_day_undo',exc)


@app.get('/api/trips/{job_id}/replans')
async def replan_history(job_id: str):
    job(job_id)
    with connect() as db:
        rows=db.execute("""SELECT replan_id,action,day_number,status,affected_packs_json,error_code,error_message,
          created_at,completed_at FROM trip_replan_runs WHERE job_id=? ORDER BY created_at DESC""",(job_id,)).fetchall()
    return {'items':[{'replan_id':row['replan_id'],'action':row['action'],'day':row['day_number'],
                      'status':row['status'],'affected_packs':json.loads(row['affected_packs_json']),
                      'error':({'code':row['error_code'],'message':row['error_message']} if row['error_code'] else None),
                      'created_at':row['created_at'],'completed_at':row['completed_at']} for row in rows]}
