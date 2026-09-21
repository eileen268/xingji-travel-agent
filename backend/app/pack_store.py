"""SQLite persistence for independently validated build packs."""
from __future__ import annotations

import json
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Callable

from .pack_graph import PROMPT_VERSION, SCHEMA_VERSION, PIPELINE_VERSION, descendants, pack_ids, signature
from .contracts import LEGACY_METADATA_VERSIONS, contract_version, validate_pack_model


def _now(): return datetime.now(timezone.utc).isoformat()


class PackStore:
    def __init__(self, connect: Callable, job_id: str, normalized_input: dict, days: int, manifest: dict | None = None, run_id: str | None = None):
        self.connect = connect; self.job_id = job_id; self.normalized_input = normalized_input; self.days = days
        self.manifest = manifest or {}; self.run_id = run_id

    def ensure(self):
        with self.connect() as db:
            for pack_id in pack_ids(self.days):
                db.execute("""INSERT OR IGNORE INTO job_packs
                    (job_id,pack_id,status,generation_attempt_count,repair_attempt_count,resume_count,prompt_version,schema_version,pipeline_version,updated_at)
                    VALUES (?,?, 'pending',0,0,0,?,?,?,datetime('now'))""",
                    (self.job_id,pack_id,PROMPT_VERSION,SCHEMA_VERSION,PIPELINE_VERSION))
                db.execute("UPDATE job_packs SET schema_version=? WHERE job_id=? AND pack_id=? AND status='pending'",
                           (contract_version(pack_id),self.job_id,pack_id))
            db.execute("UPDATE job_packs SET manifest=COALESCE(manifest,?) WHERE job_id=?",
                       (json.dumps(self.manifest,ensure_ascii=False),self.job_id))

    def dependency_signatures(self, dependency_ids: tuple[str, ...]) -> dict[str, str]:
        if not dependency_ids: return {}
        with self.connect() as db:
            rows=db.execute(f"SELECT pack_id,input_signature FROM job_packs WHERE job_id=? AND pack_id IN ({','.join('?'*len(dependency_ids))})",
                            (self.job_id,*dependency_ids)).fetchall()
        return {r['pack_id']:r['input_signature'] or '' for r in rows}

    def expected_signature(self, pack_id: str, dependency_ids: tuple[str, ...]) -> str:
        normalized=self.normalized_input
        reference_aware=(pack_id in ('itinerary-plan','itinerary-allocation','itinerary-coherence',
                                     'modules-practical','destination-profile') or pack_id.startswith('itinerary-day-'))
        if reference_aware:
            normalized={**self.normalized_input,'_place_reference_version':self.place_reference_version()}
        return signature(pack_id,normalized,self.dependency_signatures(dependency_ids))

    def place_reference_version(self) -> str:
        """Stable version of successful identity mappings used by downstream signatures."""
        with self.connect() as db:
            rows=db.execute("""SELECT generated_place_id,selected_place_id FROM place_resolutions
              WHERE job_id=? AND selected_place_id IS NOT NULL ORDER BY generated_place_id,selected_place_id""",
                            (self.job_id,)).fetchall()
        raw=json.dumps([(row['generated_place_id'],row['selected_place_id']) for row in rows],separators=(',',':'))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def apply_reference_mapping(self,mapping: dict[str,str]):
        """Migrate identity-only references and force route-dependent days to be rescheduled."""
        if not mapping:return
        from .pre_schedule_resolution import replace_place_references
        with self.connect() as db:
            rows=db.execute("SELECT pack_id,payload,source_payload FROM job_packs WHERE job_id=?",(self.job_id,)).fetchall()
            for row in rows:
                pack_id=row['pack_id']
                if pack_id in ('framing','places-core','places-experiences','places-food'):continue
                payload=(replace_place_references(json.loads(row['payload']),mapping) if row['payload'] else None)
                source=(replace_place_references(json.loads(row['source_payload']),mapping) if row['source_payload'] else None)
                if pack_id.startswith('itinerary-day-'):
                    db.execute("""UPDATE job_packs SET status='pending',payload=NULL,source_payload=?,validation_errors=NULL,
                      repair_result='reference_remap_reschedule',updated_at=? WHERE job_id=? AND pack_id=?""",
                      (json.dumps(source,ensure_ascii=False) if source is not None else None,_now(),self.job_id,pack_id))
                elif pack_id in ('itinerary-coherence','destination-profile'):
                    db.execute("""UPDATE job_packs SET status='pending',payload=NULL,source_payload=?,validation_errors=NULL,
                      repair_result='reference_remap',updated_at=? WHERE job_id=? AND pack_id=?""",
                      (json.dumps(source,ensure_ascii=False) if source is not None else None,_now(),self.job_id,pack_id))
                elif payload is not None or source is not None:
                    db.execute("""UPDATE job_packs SET payload=?,source_payload=?,repair_result='reference_remap',updated_at=?
                      WHERE job_id=? AND pack_id=?""",
                      (json.dumps(payload,ensure_ascii=False) if payload is not None else None,
                       json.dumps(source,ensure_ascii=False) if source is not None else None,_now(),self.job_id,pack_id))

    def get_valid(self, pack_id: str, expected_signature: str):
        with self.connect() as db:
            row=db.execute("SELECT status,payload,input_signature,prompt_version,pipeline_version,schema_version FROM job_packs WHERE job_id=? AND pack_id=?",(self.job_id,pack_id)).fetchone()
            if row and row['status']=='valid' and row['input_signature']==expected_signature and row['payload']:
                return json.loads(row['payload'])
            legacy=(row['prompt_version'],row['pipeline_version'],row['schema_version']) if row else None
            if row and row['status']=='valid' and row['payload'] and legacy in LEGACY_METADATA_VERSIONS:
                try:
                    migrated=validate_pack_model(pack_id,json.loads(row['payload']))
                except Exception:
                    db.execute("UPDATE job_packs SET status='pending',payload=NULL,validation_errors=NULL WHERE job_id=? AND pack_id=?",(self.job_id,pack_id))
                else:
                    db.execute("UPDATE job_packs SET payload=?,input_signature=?,schema_version=?,updated_at=? WHERE job_id=? AND pack_id=?",
                               (json.dumps(migrated,ensure_ascii=False),expected_signature,contract_version(pack_id),_now(),self.job_id,pack_id))
                    return migrated
        return None

    def mark_running(self, pack_id: str):
        with self.connect() as db:
            db.execute("""UPDATE job_packs SET status='running',validation_errors=NULL,started_at=?,finished_at=NULL,
                duration_ms=NULL,run_id=?,manifest=?,updated_at=? WHERE job_id=? AND pack_id=?""",
                (_now(),self.run_id,json.dumps(self.manifest,ensure_ascii=False),_now(),self.job_id,pack_id))

    def get_subrequest_checkpoint(self, pack_id, substage, checkpoint_key):
        with self.connect() as db:
            row=db.execute("""SELECT payload,provider_request_id,token_input,token_output,provider_retry_count
              FROM llm_subrequests WHERE job_id=? AND pack_id=? AND substage=? AND checkpoint_key=?
              AND status='completed' AND payload IS NOT NULL ORDER BY finished_at DESC LIMIT 1""",
              (self.job_id,pack_id,substage,checkpoint_key)).fetchone()
        if not row:return None
        return {'payload':json.loads(row['payload']),'provider_request_id':row['provider_request_id'],
                'token_input':row['token_input'],'token_output':row['token_output'],
                'provider_retry_count':row['provider_retry_count']}

    def start_subrequest(self, pack_id, substage, model_name, prompt_chars, estimated_tokens,
                         generation_attempt_number, repair_attempt_number, checkpoint_key=None,
                         max_tokens=4096,target_item_count=None,expected_output_tokens=None):
        subrequest_id=str(uuid.uuid4()); started=_now()
        with self.connect() as db:
            db.execute("""INSERT INTO llm_subrequests
              (job_id,subrequest_id,pack_id,substage,run_id,status,model_name,started_at,prompt_chars,estimated_tokens,
               generation_attempt_number,repair_attempt_number,checkpoint_key,max_tokens,target_item_count,expected_output_tokens)
              VALUES (?,?,?,?,?,'running',?,?,?,?,?,?,?,?,?,?)""",
              (self.job_id,subrequest_id,pack_id,substage,self.run_id,model_name,started,prompt_chars,estimated_tokens,
               generation_attempt_number,repair_attempt_number,checkpoint_key,max_tokens,target_item_count,expected_output_tokens))
        return subrequest_id

    def finish_subrequest(self, subrequest_id, status, provider_request_id=None, error_type=None, error_status_code=None,
                          payload=None,token_input=0,token_output=0,provider_retry_count=None):
        finished=_now()
        with self.connect() as db:
            row=db.execute("SELECT started_at FROM llm_subrequests WHERE job_id=? AND subrequest_id=?",
                           (self.job_id,subrequest_id)).fetchone()
            if not row:return
            elapsed=max(0,int((datetime.fromisoformat(finished)-datetime.fromisoformat(row['started_at'])).total_seconds()*1000))
            db.execute("""UPDATE llm_subrequests SET status=?,finished_at=?,elapsed_ms=?,
              provider_request_id=COALESCE(?,provider_request_id),error_type=?,error_status_code=?,
              payload=COALESCE(?,payload),token_input=?,token_output=?,
              provider_retry_count=COALESCE(?,provider_retry_count)
              WHERE job_id=? AND subrequest_id=?""",
              (status,finished,elapsed,provider_request_id,error_type,error_status_code,
               json.dumps(payload,ensure_ascii=False) if payload is not None else None,token_input,token_output,
               provider_retry_count,self.job_id,subrequest_id))

    def mark_subrequest_retry(self, subrequest_id, provider_retry_count, error_type, error_status_code=None):
        with self.connect() as db:
            db.execute("""UPDATE llm_subrequests SET provider_retry_count=?,error_type=?,error_status_code=?,status='running'
              WHERE job_id=? AND subrequest_id=?""",
              (provider_retry_count,error_type,error_status_code,self.job_id,subrequest_id))

    def timeout_running_subrequests(self, pack_id, error_type):
        finished=_now()
        with self.connect() as db:
            rows=db.execute("SELECT subrequest_id,started_at FROM llm_subrequests WHERE job_id=? AND pack_id=? AND status='running'",
                            (self.job_id,pack_id)).fetchall()
            for row in rows:
                elapsed=max(0,int((datetime.fromisoformat(finished)-datetime.fromisoformat(row['started_at'])).total_seconds()*1000))
                db.execute("UPDATE llm_subrequests SET status='timeout',finished_at=?,elapsed_ms=?,error_type=? WHERE job_id=? AND subrequest_id=?",
                           (finished,elapsed,error_type,self.job_id,row['subrequest_id']))

    def active_subrequest_pack(self):
        with self.connect() as db:
            row=db.execute("SELECT pack_id FROM llm_subrequests WHERE job_id=? AND status='running' ORDER BY started_at DESC LIMIT 1",
                           (self.job_id,)).fetchone()
        return row['pack_id'] if row else None

    def latest_subrequest(self, pack_id=None):
        with self.connect() as db:
            where='WHERE job_id=?'+(' AND pack_id=?' if pack_id else '')
            params=(self.job_id,pack_id) if pack_id else (self.job_id,)
            row=db.execute(f"""SELECT pack_id,substage,provider_request_id,status,error_type,started_at,finished_at
              FROM llm_subrequests {where} ORDER BY started_at DESC LIMIT 1""",params).fetchone()
        return dict(row) if row else None

    def poi_resolution_metrics(self):
        with self.connect() as db:
            rows=db.execute("SELECT status,selected_place_id FROM place_resolutions WHERE job_id=?",(self.job_id,)).fetchall()
        total=len(rows)
        def rate(predicate):return round(sum(predicate(row) for row in rows)/total,3) if total else 0
        return {'poi_resolution_rate':rate(lambda row:row['selected_place_id'] is not None),
                'verified_poi_rate':rate(lambda row:row['status']=='exact_match'),
                'ambiguous_poi_rate':rate(lambda row:row['status'] in ('multiple_candidates','ambiguous')),
                'generated_experience_rate':rate(lambda row:row['status']=='generated_experience')}

    def route_metrics(self):
        with self.connect() as db:
            rows=db.execute("SELECT payload FROM job_packs WHERE job_id=? AND pack_id LIKE 'itinerary-day-%' AND status='valid'",
                            (self.job_id,)).fetchall()
        segments=[];days=[]
        for row in rows:
            if not row['payload']:continue
            day=json.loads(row['payload']);days.append(day);segments.extend(day.get('segments',[]))
        total=len(segments)
        rate=lambda status:round(sum(segment.get('route_status')==status for segment in segments)/total,3) if total else 0
        densities=[day.get('density_evaluation') for day in days if day.get('density_evaluation')]
        issue_count=lambda code:sum(code in density.get('issues',[]) for density in densities)
        return {'route_leg_count':total,'route_success_rate':rate('verified'),
                'route_estimated_rate':rate('estimated_by_distance'),'route_unresolved_rate':rate('unresolved'),
                'schedule_feasibility_rate':round(sum(bool(day.get('stops')) for day in days)/len(days),3) if days else 0,
                'avg_daily_travel_minutes':round(sum(day.get('total_travel_minutes',0) for day in days)/len(days),1) if days else 0,
                'avg_daily_stop_count':round(sum(len(day.get('stops',[])) for day in days)/len(days),1) if days else 0,
                'avg_day_utilization':round(sum(value['utilization'] for value in densities)/len(densities),3) if densities else 0,
                'underfilled_day_count':issue_count('UNDERFILLED_DAY')+issue_count('UNDERFILLED_DAY_NO_GOOD_CANDIDATE'),
                'long_idle_gap_day_count':issue_count('LONG_IDLE_GAP'),
                'fill_added_place_count':sum(len(value.get('added_place_ids',[])) for value in densities)}

    def fact_verification_metrics(self):
        with self.connect() as db:
            row=db.execute("SELECT summary_json FROM fact_verification_runs WHERE job_id=? ORDER BY started_at DESC LIMIT 1",
                           (self.job_id,)).fetchone()
        if not row:return None
        summary=json.loads(row['summary_json'])
        defaults={'real_poi_resolution_rate':0.0,'scheduled_real_poi_count':0,'resolved_real_poi_count':0,
                  'scheduled_real_poi_resolution_rate':0.0,'ambiguous_real_poi_count':0,
                  'unresolved_real_poi_count':0,'llm_place_count_in_final_itinerary':0,
                  'route_segment_count':0,'fallback_route_count':0,'route_coverage_rate':0.0}
        return {key:summary.get(key,defaults.get(key)) for key in (
            'verified_place_rate','verified_route_rate','unverified_place_count',
            'real_poi_resolution_rate','scheduled_real_poi_count','resolved_real_poi_count',
            'scheduled_real_poi_resolution_rate','ambiguous_real_poi_count','unresolved_real_poi_count',
            'llm_place_count_in_final_itinerary','route_segment_count','fallback_route_count','route_coverage_rate',
            'generated_experience_count','estimated_route_count','unresolved_route_count',
            'official_source_count','verified_dynamic_fact_count','conflicting_dynamic_fact_count',
            'unavailable_dynamic_fact_count','medium_facts_needing_recheck','verified_experience_count',
            'suggested_experience_count','generated_experience_in_schedule_count','scheduled_place_fact_coverage',
            'official_fact_coverage','provider_fact_coverage','needs_recheck_count')}

    def save_timed_out(self, pack_id, error_type, configured_seconds, elapsed_seconds):
        finished=_now()
        details=[{'pointer':'/','code':error_type.upper(),'message':'该内容包生成超时，已保存此前完成内容。',
                  'invalid_value':None,'validator':'execution_timeout','depends_on_map_api':False,
                  'timeout_type':error_type,'configured_timeout_seconds':configured_seconds,'actual_elapsed_seconds':round(elapsed_seconds,3)}]
        with self.connect() as db:
            row=db.execute("SELECT started_at,generation_attempt_count,repair_attempt_count,resume_count FROM job_packs WHERE job_id=? AND pack_id=?",(self.job_id,pack_id)).fetchone()
            repaired=db.execute("SELECT 1 FROM llm_subrequests WHERE job_id=? AND pack_id=? AND run_id IS ? AND repair_attempt_number>0 LIMIT 1",
                                (self.job_id,pack_id,self.run_id)).fetchone()
            provider_retries=db.execute("SELECT COALESCE(SUM(provider_retry_count),0) total FROM llm_subrequests WHERE job_id=? AND pack_id=? AND run_id IS ?",
                                        (self.job_id,pack_id,self.run_id)).fetchone()['total']
            details[0]['generation_attempt_count']=(row['generation_attempt_count'] if row else 0)+1
            details[0]['repair_attempt_count']=(row['repair_attempt_count'] if row else 0)+(1 if repaired else 0)
            details[0]['provider_retry_count']=provider_retries
            details[0]['resume_count']=row['resume_count'] if row else 0
            started=row['started_at'] if row and row['started_at'] else finished
            duration=max(0,int((datetime.fromisoformat(finished)-datetime.fromisoformat(started)).total_seconds()*1000))
            db.execute("""UPDATE job_packs SET status='timed_out',validation_errors=?,finished_at=?,duration_ms=?,
              generation_attempt_count=generation_attempt_count+1,repair_attempt_count=repair_attempt_count+?,provider_retry_count=provider_retry_count+?,
              repair_result='not_attempted_timeout',run_id=?,updated_at=? WHERE job_id=? AND pack_id=?""",
              (json.dumps(details,ensure_ascii=False),finished,duration,1 if repaired else 0,provider_retries,self.run_id,finished,self.job_id,pack_id))
        self.timeout_running_subrequests(pack_id,error_type)

    def save_valid(self, pack_id: str, payload, expected_signature: str, generation_attempts=0, repair_attempts=0,
                   validation_warnings=None, observability=None, source_payload=None):
        payload=validate_pack_model(pack_id,payload)
        obs=observability or {}; finished=_now()
        with self.connect() as db:
            row=db.execute("SELECT started_at FROM job_packs WHERE job_id=? AND pack_id=?",(self.job_id,pack_id)).fetchone()
            started=row['started_at'] if row and row['started_at'] else finished
            duration=max(0,int((datetime.fromisoformat(finished)-datetime.fromisoformat(started)).total_seconds()*1000))
            db.execute("""UPDATE job_packs SET status='valid',payload=?,source_payload=COALESCE(?,source_payload),validation_errors=?,input_signature=?,
                generation_attempt_count=generation_attempt_count+?,repair_attempt_count=repair_attempt_count+?,
                prompt_version=?,schema_version=?,pipeline_version=?,finished_at=?,duration_ms=?,model_request_id=?,
                token_input=token_input+?,token_output=token_output+?,provider_retry_count=provider_retry_count+?,repair_result=?,manifest=?,run_id=?,updated_at=?
                WHERE job_id=? AND pack_id=?""",
                (json.dumps(payload,ensure_ascii=False),json.dumps(source_payload,ensure_ascii=False) if source_payload is not None else None,
                 json.dumps(validation_warnings,ensure_ascii=False) if validation_warnings else None,expected_signature,generation_attempts,repair_attempts,
                 PROMPT_VERSION,contract_version(pack_id),PIPELINE_VERSION,finished,duration,obs.get('model_request_id'),obs.get('token_input',0),
                 obs.get('token_output',0),obs.get('provider_retry_count',0),obs.get('repair_result','not_needed'),json.dumps(self.manifest,ensure_ascii=False),self.run_id,finished,self.job_id,pack_id))

    def save_invalid(self, pack_id: str, errors, generation_attempts=1, repair_attempts=1, observability=None, source_payload=None):
        obs=observability or {}; finished=_now()
        with self.connect() as db:
            row=db.execute("SELECT generation_attempt_count,repair_attempt_count,resume_count,started_at FROM job_packs WHERE job_id=? AND pack_id=?",
                           (self.job_id,pack_id)).fetchone()
            final_generation=(row['generation_attempt_count'] if row else 0)+generation_attempts
            final_repair=(row['repair_attempt_count'] if row else 0)+repair_attempts
            resume_count=row['resume_count'] if row else 0
            details=[]
            for error in errors:
                detail=dict(error)
                detail.setdefault('invalid_value',None)
                detail.setdefault('validator','semantic_validation')
                detail.setdefault('depends_on_map_api',False)
                detail['generation_attempt_count']=final_generation
                detail['repair_attempt_count']=final_repair
                detail['resume_count']=resume_count
                details.append(detail)
            started=row['started_at'] if row and row['started_at'] else finished
            duration=max(0,int((datetime.fromisoformat(finished)-datetime.fromisoformat(started)).total_seconds()*1000))
            db.execute("""UPDATE job_packs SET status='invalid',validation_errors=?,source_payload=COALESCE(?,source_payload),
                generation_attempt_count=generation_attempt_count+?,repair_attempt_count=repair_attempt_count+?,finished_at=?,duration_ms=?,
                model_request_id=?,token_input=token_input+?,token_output=token_output+?,provider_retry_count=provider_retry_count+?,repair_result=?,manifest=?,run_id=?,updated_at=?
                WHERE job_id=? AND pack_id=?""",
                (json.dumps(details,ensure_ascii=False),json.dumps(source_payload,ensure_ascii=False) if source_payload is not None else None,
                 generation_attempts,repair_attempts,finished,duration,obs.get('model_request_id'),obs.get('token_input',0),
                 obs.get('token_output',0),obs.get('provider_retry_count',0),obs.get('repair_result','failed'),json.dumps(self.manifest,ensure_ascii=False),self.run_id,
                 finished,self.job_id,pack_id))

    def resign_valid(self, pack_id: str, expected_signature: str, *, repair_result='replan_reused'):
        """Refresh dependency metadata while preserving an intentionally reused payload byte-for-byte."""
        with self.connect() as db:
            changed=db.execute("""UPDATE job_packs SET input_signature=?,prompt_version=?,schema_version=?,pipeline_version=?,
              manifest=?,run_id=?,repair_result=?,updated_at=? WHERE job_id=? AND pack_id=? AND status='valid' AND payload IS NOT NULL""",
              (expected_signature,PROMPT_VERSION,contract_version(pack_id),PIPELINE_VERSION,
               json.dumps(self.manifest,ensure_ascii=False),self.run_id,repair_result,_now(),self.job_id,pack_id)).rowcount
        if changed != 1:raise ValueError(f'cannot re-sign missing valid pack: {pack_id}')

    def get_source_payload(self, pack_id: str):
        with self.connect() as db:
            row=db.execute("SELECT source_payload,payload FROM job_packs WHERE job_id=? AND pack_id=?",(self.job_id,pack_id)).fetchone()
        value=(row['source_payload'] or row['payload']) if row else None
        return json.loads(value) if value else None

    def copy_from(self, source_job_id: str):
        """Copy replay inputs/outputs; attempt counters remain local to the replay job."""
        with self.connect() as db:
            rows=db.execute("SELECT * FROM job_packs WHERE job_id=?",(source_job_id,)).fetchall()
            for row in rows:
                db.execute("""UPDATE job_packs SET status=?,payload=?,source_payload=?,validation_errors=?,input_signature=?,
                    prompt_version=?,schema_version=?,pipeline_version=?,manifest=?,updated_at=? WHERE job_id=? AND pack_id=?""",
                    (row['status'],row['payload'],row['source_payload'],row['validation_errors'],row['input_signature'],row['prompt_version'],
                     row['schema_version'],row['pipeline_version'],row['manifest'],_now(),self.job_id,row['pack_id']))

    def copy_place_resolutions_from(self,source_job_id: str):
        """Replay keeps provider decisions traceable without calling AMap again."""
        with self.connect() as db:
            rows=db.execute("SELECT * FROM place_resolutions WHERE job_id=? ORDER BY created_at",(source_job_id,)).fetchall()
            for row in rows:
                db.execute("""INSERT INTO place_resolutions
                  (resolution_id,job_id,pack_id,generated_place_id,generated_name,city,selected_place_id,provider_place_id,
                   score,status,candidates_json,error_code,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (str(uuid.uuid4()),self.job_id,row['pack_id'],row['generated_place_id'],row['generated_name'],row['city'],
                   row['selected_place_id'],row['provider_place_id'],row['score'],row['status'],row['candidates_json'],
                   row['error_code'],_now()))

    def invalidate(self, pack_id: str, errors, include_descendants=True):
        targets={pack_id}|(descendants(pack_id,self.days) if include_descendants else set())
        with self.connect() as db:
            for target in targets:
                db.execute("UPDATE job_packs SET status=?,payload=CASE WHEN ?='invalid' THEN payload ELSE NULL END,validation_errors=?,updated_at=datetime('now') WHERE job_id=? AND pack_id=?",
                           ('invalid' if target==pack_id else 'pending','invalid' if target==pack_id else 'pending',json.dumps(errors,ensure_ascii=False) if target==pack_id else None,self.job_id,target))

    def resume(self):
        with self.connect() as db:
            invalid_rows=db.execute("SELECT pack_id,payload,validation_errors FROM job_packs WHERE job_id=? AND status='invalid'",(self.job_id,)).fetchall()
            # Older coherence rules could invalidate an otherwise valid day only because
            # the model-declared interest labels were not supported by string matching.
            # Interests are now derived from actual stops, so retain those day payloads
            # and turn the obsolete hard error into a traceable normalization warning.
            for row in invalid_rows:
                errors=json.loads(row['validation_errors'] or '[]')
                if row['payload'] and errors and all(error.get('code')=='UNSUPPORTED_INTEREST' for error in errors):
                    warnings=[{
                        'pointer':error.get('pointer','/'),
                        'code':'INTEREST_DECLARATION_IGNORED',
                        'pack_id':row['pack_id'],
                        'message':'历史模型声明的兴趣已忽略；兴趣匹配改由实际停靠点确定性推导。',
                    } for error in errors]
                    db.execute("UPDATE job_packs SET status='valid',validation_errors=?,repair_result='deprecated_interest_rule_normalized',updated_at=datetime('now') WHERE job_id=? AND pack_id=?",
                               (json.dumps(warnings,ensure_ascii=False),self.job_id,row['pack_id']))
            invalid=[r['pack_id'] for r in db.execute("SELECT pack_id FROM job_packs WHERE job_id=? AND status IN ('invalid','timed_out','running')",(self.job_id,))]
            failed_days={int(pack_id.rsplit('-',1)[1]) for pack_id in invalid if pack_id.startswith('itinerary-day-')}
            if failed_days:
                rows={r['pack_id']:r for r in db.execute("SELECT pack_id,status,payload FROM job_packs WHERE job_id=? AND pack_id IN (?,?,?,?,?)",
                    (self.job_id,'itinerary-allocation','itinerary-plan','places-core','places-experiences','places-food')).fetchall()}
                required=('itinerary-allocation','itinerary-plan','places-core','places-experiences','places-food')
                if all(name in rows and rows[name]['payload'] for name in required) and rows['itinerary-allocation']['status']=='valid':
                    from .place_allocator import reallocate_days
                    allocation=json.loads(rows['itinerary-allocation']['payload']); plan=json.loads(rows['itinerary-plan']['payload'])
                    places=[]
                    for name in ('places-core','places-experiences','places-food'):
                        places.extend(json.loads(rows[name]['payload'])['places'])
                    rebuilt=reallocate_days(allocation,plan,places,failed_days,
                        must_go=(self.normalized_input.get('preferences') or {}).get('mustGo',''),
                        interests=(self.normalized_input.get('preferences') or {}).get('interests',[]))
                    db.execute("UPDATE job_packs SET payload=?,updated_at=datetime('now') WHERE job_id=? AND pack_id='itinerary-allocation'",
                               (json.dumps(rebuilt,ensure_ascii=False),self.job_id))
            targets=set(invalid)
            for item in invalid: targets.update(descendants(item,self.days))
            for target in targets:
                db.execute("UPDATE job_packs SET status='pending',payload=NULL,validation_errors=NULL,resume_count=resume_count+1,updated_at=datetime('now') WHERE job_id=? AND pack_id=?",(self.job_id,target))

    def summary(self):
        with self.connect() as db:
            rows=db.execute("""SELECT pack_id,status,validation_errors,generation_attempt_count,repair_attempt_count,resume_count,
                started_at,finished_at,duration_ms,model_request_id,token_input,token_output,provider_retry_count,repair_result,run_id
                FROM job_packs WHERE job_id=?""",(self.job_id,)).fetchall()
            requests=db.execute("""SELECT subrequest_id,pack_id,substage,status,model_name,started_at,finished_at,elapsed_ms,
                prompt_chars,estimated_tokens,generation_attempt_number,repair_attempt_number,provider_request_id,error_type,error_status_code,
                provider_retry_count,max_tokens,target_item_count,expected_output_tokens,token_input,token_output,checkpoint_key
                FROM llm_subrequests WHERE job_id=? ORDER BY started_at DESC LIMIT 50""",(self.job_id,)).fetchall()
        complete=sum(r['status']=='valid' for r in rows)
        current=next((r['pack_id'] for r in rows if r['status'] in ('running','invalid','timed_out')),None)
        errors=[]; warnings=[]
        for r in rows:
            if r['status'] in ('invalid','timed_out') and r['validation_errors']: errors.extend(json.loads(r['validation_errors']))
            if r['status']=='valid' and r['validation_errors']: warnings.extend(json.loads(r['validation_errors']))
        return {'current_pack':current,'completed_packs':complete,'total_packs':len(rows),'validation_errors':errors[:10],
                'validation_warnings':warnings[:10],'pack_statuses':{r['pack_id']:r['status'] for r in rows},
                'pack_attempts':{r['pack_id']:{'generation_attempt_count':r['generation_attempt_count'],
                    'repair_attempt_count':r['repair_attempt_count'],'provider_retry_count':r['provider_retry_count'],'resume_count':r['resume_count']} for r in rows},
                'pack_observability':{r['pack_id']:{'started_at':r['started_at'],'finished_at':r['finished_at'],
                    'duration_ms':r['duration_ms'],'model_request_id':r['model_request_id'],'token_input':r['token_input'],
                    'token_output':r['token_output'],'provider_retry_count':r['provider_retry_count'],'repair_result':r['repair_result'],'run_id':r['run_id']} for r in rows},
                'llm_subrequests':[dict(r) for r in requests]}
