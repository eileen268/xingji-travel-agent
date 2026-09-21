"""Bounded JSON stages for GLM-4-Plus's 4K output limit."""
import asyncio
import hashlib
import json
import random
import re
import time
from openai import APIConnectionError, APIStatusError, APITimeoutError
from jsonschema import Draft202012Validator, FormatChecker
from .models import ConstraintFallbackDecision, DayDecision, ItineraryPlanDecision, ItineraryPlan, ResearchPlace
from .contracts import language_request_models, practical_request_models, research_batch_model
from .validators import map_url, schema_issues, validate_day as semantic_day, validate_trip_coherence
from .pack_graph import dependencies
from .itinerary_plan import build_day_pools, decision_schema, canonicalize_decision, normalize_day_candidate_pools, hydrate_decision, validate_decision, validate_actual_interest_coverage
from .day_planner import day_catalog, day_decision_schema, canonicalize_day_decision, validate_day_decision, schedule_day
from .scheduler import schedule_day_with_routes
from .place_allocator import AllocationError, allocate_trip, allocation_outline, finalize_selected, validate_cross_day_ownership
from .interests import derive_day_interests, enrich_places, normalize_interests
from .food_contract import (
    FOOD_MAX_PER_CITY, FOOD_MIN_PER_CITY, food_batch_instruction, food_batch_model,
    food_target_per_city, normalize_food_batch,
)
from .timeouts import pack_timeout, provider_retry_config
from .budgets import sights_for_city, experiences_for_city
from .place_names import normalize_place_names, normalize_places
from .place_resolution import PlaceResolver
from .pre_schedule_resolution import (apply_resolution_mapping,build_day_resolution_contexts,
                                      enforce_pre_schedule_resolution)
from .services.amap import AmapRouteService
from .services.official import OfficialFactService
from .warnings import dedupe_warnings
from .trip_constraints import compile_trip_constraints,city_for_day,city_day_counts,merge_llm_fallback

RESEARCH_PLACE_FIELDS=tuple(ResearchPlace.model_fields)


def compact_research_batch(data):
    if not isinstance(data,dict) or not isinstance(data.get('places'),list):return data
    normalized=[normalize_place_names(item,provider_output=True) if isinstance(item,dict) else item for item in data['places']]
    for item in normalized:
        if isinstance(item,dict):
            for key in ('cuisine','signature_dishes','experience_type'):
                if item.get(key) is None:item[key]=''
    return {**data,'places':[{key:item[key] for key in RESEARCH_PLACE_FIELDS if key in item} if isinstance(item,dict) else item
                             for item in normalized]}

def day_city(t,index,constraints=None):
    """Keep destination order while assigning all extra days to the primary destination."""
    return city_for_day(t,index,constraints or compile_trip_constraints(t))

class StageValidationError(ValueError):
    def __init__(self, stage, errors, generation_attempts=1, repair_attempts=1, observability=None, source_payload=None):
        self.stage=stage
        self.generation_attempts=generation_attempts
        self.repair_attempts=repair_attempts
        self.observability=observability or {}
        self.source_payload=source_payload
        self.errors=[]
        for error in errors:
            detail=dict(error)
            detail.setdefault('pointer','/')
            detail.setdefault('code','validation')
            detail.setdefault('message','校验失败')
            detail.setdefault('invalid_value',None)
            detail.setdefault('validator','semantic_validation')
            detail.setdefault('depends_on_map_api',False)
            self.errors.append(detail)
        self.pointers=[e['pointer'] for e in self.errors]
        suffix='在一次修复后仍未通过' if repair_attempts else '未通过确定性内部校验'
        super().__init__(stage+suffix+'；字段：'+','.join(self.pointers[:8]))


class StageTimeoutError(TimeoutError):
    def __init__(self, pack_id, timeout_type, configured_seconds, elapsed_seconds, substage=None):
        self.pack_id=pack_id; self.stage=pack_id; self.timeout_type=timeout_type
        self.configured_seconds=configured_seconds; self.elapsed_seconds=elapsed_seconds; self.substage=substage
        super().__init__(f'{pack_id} 生成超时（{timeout_type}，{elapsed_seconds:.1f}/{configured_seconds:.1f} 秒）')


def _find_timeout(exc):
    if isinstance(exc,StageTimeoutError):return exc
    if isinstance(exc,BaseExceptionGroup):
        for child in exc.exceptions:
            found=_find_timeout(child)
            if found:return found
    return None


def _find_provider_timeout(exc):
    if isinstance(exc,APITimeoutError):return _provider_error(exc)[0]
    if isinstance(exc,APIStatusError) and exc.status_code in (408,504):return 'provider_timeout'
    if isinstance(exc,BaseExceptionGroup):
        for child in exc.exceptions:
            found=_find_provider_timeout(child)
            if found:return found
    return None

async def collect(*coroutines):
    # TaskGroup cancels and drains siblings before closing the HTTP client on failure.
    async with asyncio.TaskGroup() as group:
        tasks=[group.create_task(c) for c in coroutines]
    return [task.result() for task in tasks]

def _pack_for_substage(name):
    if name.startswith('sights-'):return 'places-core'
    if name.startswith('experiences-'):return 'places-experiences'
    if name.startswith('restaurants-') or name=='chains':return 'places-food'
    if name.startswith('day-'):return 'itinerary-'+name
    if name in ('menu-guide','snacks','preparation'):return 'modules-practical'
    if name.startswith(('keywords-','phrases-','note-')):return 'modules-language-notes'
    return name


def _provider_error(exc):
    if isinstance(exc,APITimeoutError):
        cause=getattr(exc,'__cause__',None)
        return ('connect_timeout' if cause and 'connect' in type(cause).__name__.lower() else 'read_timeout',None)
    if isinstance(exc,APIStatusError):return ('provider_timeout' if exc.status_code in (408,504) else 'provider_error',exc.status_code)
    if isinstance(exc,APIConnectionError):return ('connection_reset',None)
    return ('provider_error',None)


def _transient_provider_error(exc):
    error_type,status_code=_provider_error(exc)
    return error_type in ('connect_timeout','read_timeout','connection_reset','provider_timeout') or bool(status_code and status_code>=500)


class ChunkRunner:
    def __init__(self,client,system,preferences,model,state_callback=None,store=None):
        self.client=client;self.system=system;self.preferences=preferences;self.model=model
        self.semaphore=asyncio.Semaphore(3)
        self.records={}
        self.state_callback=state_callback
        self.store=store

    async def ask(self,name,model,instruction,context=None,validate=None,canonicalize=None,schema_override=None):
        async with self.semaphore:
            schema=schema_override or model.model_json_schema()
            prompt={'stage':name,'instruction':instruction,'schema':schema,'preferences':self.preferences.model_dump(mode='json'),'context':context}
            messages=[{'role':'system','content':self.system+'\n当前为有界子阶段：只输出用户给出的本阶段schema，不要输出其他模块。'},
                      {'role':'user','content':json.dumps(prompt,ensure_ascii=False)}]
            request_ids=[]; token_input=0; token_output=0; last_data=None; provider_retries=0
            retry_config=provider_retry_config()
            for attempt in range(2):
                if attempt and self.state_callback:self.state_callback('repairing',name)
                prompt_chars=sum(len(m['content']) for m in messages)
                estimated_tokens=max(1,(prompt_chars+2)//3)
                count_match=re.search(r'(?:正好|建议)(\d+)\s*(?:个|种|条|项|卡)',instruction)
                target_item_count=int(count_match.group(1)) if count_match else None
                expected_output_tokens=min(4096,max(256,(target_item_count or 2)*500))
                checkpoint_material={'model':self.model,'messages':messages,'schema':schema,
                    'manifest':getattr(self.store,'manifest',{}) if self.store else {}}
                checkpoint_key=hashlib.sha256(json.dumps(checkpoint_material,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                request_key=None
                checkpoint=None
                if self.store and hasattr(self.store,'get_subrequest_checkpoint'):
                    checkpoint=self.store.get_subrequest_checkpoint(_pack_for_substage(name),name,checkpoint_key)
                if checkpoint:
                    data=checkpoint['payload'];raw=json.dumps(data,ensure_ascii=False);last_data=data
                    if checkpoint.get('provider_request_id'):request_ids.append(checkpoint['provider_request_id'])
                    token_input+=checkpoint.get('token_input',0);token_output+=checkpoint.get('token_output',0)
                    provider_retries+=checkpoint.get('provider_retry_count',0)
                elif self.store and hasattr(self.store,'start_subrequest'):
                    request_key=self.store.start_subrequest(_pack_for_substage(name),name,self.model,prompt_chars,estimated_tokens,
                                                            1,1 if attempt else 0,checkpoint_key,4096,target_item_count,expected_output_tokens)
                if not checkpoint:
                    response=None; request_retry_count=0
                    while True:
                        try:
                            response=await self.client.chat.completions.create(model=self.model,response_format={'type':'json_object'},
                                messages=messages,temperature=.3,max_tokens=4096)
                            break
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            error_type,status_code=_provider_error(exc)
                            if _transient_provider_error(exc) and request_retry_count<int(retry_config['max_retries']):
                                request_retry_count+=1;provider_retries+=1
                                if self.store and request_key and hasattr(self.store,'mark_subrequest_retry'):
                                    self.store.mark_subrequest_retry(request_key,request_retry_count,error_type,status_code)
                                await asyncio.sleep(random.uniform(float(retry_config['backoff_min']),float(retry_config['backoff_max'])))
                                continue
                            if self.store and request_key:self.store.finish_subrequest(request_key,'timeout' if 'timeout' in error_type else 'failed',
                                error_type=error_type,error_status_code=status_code,provider_retry_count=request_retry_count)
                            raise
                    request_id=getattr(response,'id',None)
                    if request_id:request_ids.append(str(request_id))
                    usage=getattr(response,'usage',None)
                    request_token_input=int(getattr(usage,'prompt_tokens',0) or 0)
                    request_token_output=int(getattr(usage,'completion_tokens',0) or 0)
                    token_input+=request_token_input;token_output+=request_token_output
                    raw=response.choices[0].message.content or ''
                try:
                    cleaned=raw.strip().lstrip('\ufeff')
                    if cleaned.startswith('```'):
                        cleaned=re.sub(r'^```(?:json)?\s*|\s*```$', '', cleaned, flags=re.I)
                    cleaned=re.sub(r',\s*([}\]])', r'\1', cleaned)
                    data=json.loads(cleaned)
                    last_data=data
                    if self.state_callback:self.state_callback('validating',name)
                    if canonicalize:
                        data=canonicalize(data)
                    errors=schema_issues(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(data))
                    if not errors:
                        model.model_validate(data)
                        if re.search(r'购物|伴手礼|纪念品|退税|souvenir|shopping', json.dumps(data,ensure_ascii=False),re.I):
                            errors.append({'pointer':'/','message':'删除所有购物相关内容和词汇，替换为参观、用餐或公共交通场景。'})
                        if validate:
                            errors.extend(validate(data))
                    if not errors:
                        self.records[name]={'attempts':attempt+1,'validated':True,'model_request_id':','.join(request_ids) or None,
                            'token_input':token_input,'token_output':token_output,
                            'provider_retry_count':provider_retries,'checkpoint_reused':bool(checkpoint),
                            'repair_result':'succeeded' if attempt else 'not_needed','source_payload':data}
                        if self.store and request_key:self.store.finish_subrequest(request_key,'completed',str(request_id) if request_id else None,
                            payload=data,token_input=request_token_input,token_output=request_token_output,provider_retry_count=request_retry_count)
                        if self.state_callback:self.state_callback('running',name)
                        return data
                except (ValueError,TypeError) as e:
                    errors=[{'pointer':'/','message':str(e)[:1500]}]
                if self.store and request_key:self.store.finish_subrequest(request_key,'failed',str(request_id) if request_id else None,
                    error_type='semantic_validation',payload=last_data,token_input=request_token_input,
                    token_output=request_token_output,provider_retry_count=request_retry_count)
                if attempt==0:
                    messages += [{'role':'assistant','content':raw},{'role':'user','content':'修复下面错误，仅返回完整的本阶段JSON：'+json.dumps(errors,ensure_ascii=False)}]
            obs={'model_request_id':','.join(request_ids) or None,'token_input':token_input,'token_output':token_output,
                 'provider_retry_count':provider_retries,'repair_result':'failed'}
            self.records[name]={'attempts':2,'validated':False,**obs,'source_payload':last_data}
            raise StageValidationError(name,errors,1,1,obs,last_data)

def _persist(store, pack_id, payload, runner=None, stage=None, warnings=None):
    deps=dependencies(pack_id,store.days); sig=store.expected_signature(pack_id,deps)
    def belongs(name):
        if pack_id=='places-core':return name.startswith('sights-')
        if pack_id=='places-experiences':return name.startswith('experiences-')
        if pack_id=='places-food':return name.startswith('restaurants-') or name=='chains'
        if pack_id=='modules-practical':return name in ('menu-guide','snacks','preparation')
        if pack_id=='modules-language-notes':return name.startswith(('keywords-','phrases-','note-'))
        return name==(stage or pack_id)
    records=[] if runner is None else [value for name,value in runner.records.items() if belongs(name)]
    record={
        'model_request_id':','.join(filter(None,(r.get('model_request_id') for r in records))) or None,
        'token_input':sum(r.get('token_input',0) for r in records),
        'token_output':sum(r.get('token_output',0) for r in records),
        'provider_retry_count':sum(r.get('provider_retry_count',0) for r in records),
        'repair_result':'succeeded' if any(r.get('repair_result')=='succeeded' for r in records) else 'not_needed',
    }
    exact=runner.records.get(stage or pack_id,{}) if runner else {}
    attempts=(0,0) if runner is None else (1 if records else 0,sum(max(0,r.get('attempts',1)-1) for r in records))
    try:
        source_payload=payload if pack_id.startswith('places-') else exact.get('source_payload',payload if runner is None else None)
        store.save_valid(pack_id,payload,sig,*attempts,validation_warnings=warnings,observability=record,
                         source_payload=source_payload)
    except TypeError as exc:
        # Lightweight test/dry-run stores may implement the original persistence protocol.
        if 'unexpected keyword argument' not in str(exc): raise
        store.save_valid(pack_id,payload,sig,*attempts,validation_warnings=warnings)

async def build_chunks(client,t,progress,system,model,store=None):
    runner=ChunkRunner(client,system,t,model,lambda status,stage:progress(stage,None,status),store)
    place_resolver=(PlaceResolver(store.connect,store.job_id)
                    if store and getattr(store,'connect',None) and getattr(store,'manifest',{}).get('poi_provider')=='amap'
                    else None)
    route_service=(AmapRouteService(place_resolver.client,store.connect)
                   if place_resolver and place_resolver.client and getattr(store,'manifest',{}).get('route_provider')=='amap'
                   else None)
    official_service=(OfficialFactService(store.connect,store.job_id)
                      if store and getattr(store,'connect',None) else None)
    pack_started={}
    async def within_pack(pack_id, awaitable, substage=None):
        started=pack_started.setdefault(pack_id,time.monotonic()); configured=pack_timeout(pack_id)
        remaining=configured-(time.monotonic()-started)
        if remaining<=0:
            if hasattr(awaitable,'close'):awaitable.close()
            elapsed=time.monotonic()-started
            if store:store.save_timed_out(pack_id,'pack_timeout',configured,elapsed)
            raise StageTimeoutError(pack_id,'pack_timeout',configured,elapsed,substage)
        try:
            async with asyncio.timeout(remaining):return await awaitable
        except APITimeoutError as exc:
            elapsed=time.monotonic()-started; error_type=_provider_error(exc)[0]
            if store:store.save_timed_out(pack_id,error_type,configured,elapsed)
            raise StageTimeoutError(pack_id,error_type,configured,elapsed,substage) from exc
        except APIStatusError as exc:
            if exc.status_code not in (408,504):raise
            elapsed=time.monotonic()-started
            if store:store.save_timed_out(pack_id,'provider_timeout',configured,elapsed)
            raise StageTimeoutError(pack_id,'provider_timeout',configured,elapsed,substage) from exc
        except BaseExceptionGroup as exc:
            error_type=_find_provider_timeout(exc)
            if not error_type:raise
            elapsed=time.monotonic()-started
            if store:store.save_timed_out(pack_id,error_type,configured,elapsed)
            raise StageTimeoutError(pack_id,error_type,configured,elapsed,substage) from exc
        except TimeoutError as exc:
            elapsed=time.monotonic()-started
            if store:store.save_timed_out(pack_id,'pack_timeout',configured,elapsed)
            raise StageTimeoutError(pack_id,'pack_timeout',configured,elapsed,substage) from exc
    trip_constraints=compile_trip_constraints(t)
    if trip_constraints.unparsed_notes:
        def validate_fallback(data):
            try:
                merge_llm_fallback(t,trip_constraints,ConstraintFallbackDecision.model_validate(data))
            except Exception as exc:
                return [{'pointer':'/constraints','code':'INVALID_USER_CONSTRAINT','message':str(exc)}]
            return []
        fallback=await within_pack('framing',runner.ask('framing-constraints',ConstraintFallbackDecision,
            '仅把未解析的用户旅行要求转换成结构化城市停留、抵达或离开约束。不得生成行程或地点；没有明确约束时返回空数组。',
            {'destinations':t.destinations,'start_date':t.startDate,'end_date':t.endDate,
             'unparsed_notes':trip_constraints.unparsed_notes},validate_fallback), 'framing-constraints')
        trip_constraints=merge_llm_fallback(t,trip_constraints,ConstraintFallbackDecision.model_validate(fallback))
    city_at=lambda index:day_city(t,index,trip_constraints)
    if store:
        _persist(store,'framing',{'preferences':t.model_dump(mode='json'),'constraints':trip_constraints.model_dump(mode='json')},runner,'framing-constraints')
    specs=[]
    for ci,city in enumerate(t.destinations):
        city_days=city_day_counts(t,trip_constraints)[city]
        remaining=sights_for_city(city_days); j=0
        while remaining:
            count=min(4,remaining)
            specs.append((f'sights-{ci}-{j}',city,'sight',count,f'ID用s{ci}-{j}-前缀；第{j+1}组。选择不同于其他组的景点，兼顾核心景点与同区备选。'))
            remaining-=count; j+=1
    for ci,city in enumerate(t.destinations):
        city_days=city_day_counts(t,trip_constraints)[city]
        restaurant_target=food_target_per_city(city_days)
        specs.append((f'restaurants-{ci}',city,'restaurant',restaurant_target,f'ID用r{ci}-前缀，为{city}提供不同餐饮场景。'))
        experience_target=experiences_for_city(city_days)
        type_counts=[experience_target//3+(1 if j<experience_target%3 else 0) for j in range(3)]
        for j,(kind,count) in enumerate(zip(['culture','nature','food_workshop'],type_counts)):
            if count:specs.append((f'experiences-{ci}-{j}',city,'experience',count,f'ID用e{ci}-{j}-前缀；experience_type={kind}，地点和活动须具体，不默认有课程服务。'))
    chain_count=1 if t.days<=2 else 2
    specs.append(('chains',t.mainDestination,'chain',chain_count,'ID用c-前缀，真实当地常见连锁作为未指定分店的备选。'))
    progress('places-core',10)
    places=[]
    cached_packs={}
    if store:
        for pid in ('places-core','places-experiences','places-food'):
            value=store.get_valid(pid,store.expected_signature(pid,dependencies(pid,store.days)))
            if value: cached_packs[pid]=value
    for value in cached_packs.values():places.extend(value['places'])
    missing_packs={'places-core','places-experiences','places-food'}-set(cached_packs)
    def research_pack_for(kind):
        return 'places-core' if kind=='sight' else ('places-experiences' if kind=='experience' else 'places-food')
    pack_types={'places-core':('sight',),'places-experiences':('experience',),'places-food':('restaurant','chain')}
    def finalize_place(p):
        normalized=normalize_place_names(p,provider_output=True);p.clear();p.update(normalized)
        p['official_url']=None;p['map_url']=None if p['entity_kind']=='experience_concept' and not p['linked_place_id'] else map_url(p['city'],p['display_name']);p['knowledge_status']='model_knowledge'
        p['coordinates']=None;p['rating']=None;p['review_count']=None;p['recheck_note']='出发前请复核'
        p['hours_note']='开放与接待时间尚未核实，出发前请复核。'
        p['ticket_note']='门票、消费及收费规则尚未核实，出发前请复核。'
        p['reservation_note']='是否需要预约及确认方式尚未核实，出发前请复核。'
    for p in places:
        normalized=normalize_place_names(p);p.clear();p.update(normalized)
        if p.get('entity_kind')!='experience_concept' or p.get('linked_place_id'):
            p['map_url']=map_url(p['city'],p['display_name'])
    enrich_places(places)
    if store:
        for pid,types in pack_types.items():
            if pid not in cached_packs:continue
            payload={'places':[p for p in places if p['type'] in types]}
            store.save_valid(pid,payload,store.expected_signature(pid,dependencies(pid,store.days)),0,0,
                             observability={'repair_result':'semantic_enrichment'},source_payload=payload)
    # A research pack is a checkpoint boundary. Finish and persist it before the next pack starts.
    for current_research_pack in ('places-core','places-experiences','places-food'):
        if current_research_pack not in missing_packs:continue
        if store:store.mark_running(current_research_pack)
        progress(current_research_pack,10 if current_research_pack=='places-core' else (18 if current_research_pack=='places-experiences' else 26))
        pack_specs=[spec for spec in specs if research_pack_for(spec[2])==current_research_pack]
        for name,city,kind,count,detail in pack_specs:
            if kind=='restaurant':
                Batch=food_batch_model(city)
            else:
                Batch=research_batch_model(city,kind,count)
            def validate_places(data,city=city,kind=kind,count=count):
                items=data['places']; errors=[]
                count_valid=(FOOD_MIN_PER_CITY<=len(items)<=FOOD_MAX_PER_CITY) if kind=='restaurant' else len(items)==count
                if not count_valid or any(p['city']!=city or p['type']!=kind for p in items):
                    expected=f'{FOOD_MIN_PER_CITY}到{FOOD_MAX_PER_CITY}个' if kind=='restaurant' else f'正好{count}个'
                    errors.append({'pointer':'/places','message':f'必须返回{expected}{city}的{kind}。'})
                all_items=places+items
                if len({(p['type'],p['city'],p['display_name']) for p in all_items})!=len(all_items):
                    errors.append({'pointer':'/places','message':'同类型地点名称不可与本批或已有地点重复。'})
                return errors
            city_days=city_day_counts(t,trip_constraints)[city]
            instruction=(food_batch_instruction(city,city_days) if kind=='restaurant' else f'只生成{city}的{count}个{kind}。')
            instruction+=f'{detail} context是已经生成的地点列表，本批必须选择未出现过的名称。餐厅用cuisine区分具体餐饮场景，不要全部填写同一城市菜系。国内地点的canonical_name、local_name、display_name使用中文官方常用名，english_name无可靠值时为null，不得自行英译。体验若没有可核验的真实场所，只能作为experience_concept，linked_place_id=null且booking_status=unverified。'
            canonicalize=((lambda data,city=city:compact_research_batch(normalize_food_batch(data,city)))
                          if kind=='restaurant' else compact_research_batch)
            batch=await within_pack(current_research_pack,runner.ask(name,Batch,instruction,
                [{'id':p['id'],'display_name':p['display_name'],'cuisine':p['cuisine']} for p in places],validate_places,canonicalize),name)
            for index,p in enumerate(batch['places']):
                p['id']=f'{name}-{index+1}';finalize_place(p)
            enrich_places(batch['places']);places.extend(batch['places'])
        pack_places=[p for p in places if p['type'] in pack_types[current_research_pack]]
        resolution_warnings=[]
        if place_resolver:
            _,resolution_warnings=await place_resolver.resolve_places(pack_places,current_research_pack)
        payload={'places':pack_places}
        if store:_persist(store,current_research_pack,payload,runner,pack_specs[-1][0] if pack_specs else current_research_pack,
                          resolution_warnings)
    # Cached packs and earlier provider failures must pass through the same
    # route-readiness gate. This is the final identity boundary before any POI
    # IDs are exposed to itinerary planning.
    resolution_gate_warnings=[]
    if place_resolver:
        gate=await enforce_pre_schedule_resolution(places,place_resolver)
        resolution_gate_warnings=gate.warnings
        if store:
            for pid,types in pack_types.items():
                payload={'places':[p for p in places if p['type'] in types]}
                _persist(store,pid,payload,runner,pid,resolution_gate_warnings)
    relevant_interests=set(normalize_interests(t.preferences.interests))
    compact=[{**{k:p[k] for k in ('id','type','city','display_name','description','cuisine','experience_type','semantic_tags')},
              'interest_affinity':{k:v for k,v in p.get('interest_affinity',{}).items() if k in relevant_interests}}
             for p in places]
    progress('itinerary',35)
    plan=None; plan_id='itinerary-plan'; plan_sig=None
    day_pools=build_day_pools(t,places,lambda _trip,index:city_at(index))
    if store:
        plan_sig=store.expected_signature(plan_id,dependencies(plan_id,store.days))
        plan=store.get_valid(plan_id,plan_sig)
    if plan is None:
        if store: store.mark_running(plan_id)
        progress(plan_id,30); coverage_warnings=[]
        def validate_plan(data):
            errors,warnings=validate_decision(data,day_pools,places,t.preferences.interests,t.preferences.mustGo,t.preferences.avoid)
            coverage_warnings[:]=warnings
            return errors
        plan_decision=await within_pack(plan_id,runner.ask(plan_id,ItineraryPlanDecision,
            '只为全程做精简骨架决策。days顺序对应context中的天；不要生成日期、城市、地点名称、地址、坐标、营业时间、餐厅、兴趣覆盖声明或详细时刻。primary_anchor_id只能从当天allowed_primary_anchor_ids选择；candidate_place_ids和backup_candidate_ids只能从当天allowed_candidate_place_ids选择，三组ID互不重复；优先候选建议2到4个，备用候选建议2到4个。结合preference_strategy与地点affinity设计全程，但不为凑标签牺牲路线质量。intent仅写自然语言主题。',
            {'days':day_pools,'candidate_catalog':compact,'preference_strategy':{'selected_interest_ids':normalize_interests(t.preferences.interests),'rule':'trip_level_soft_preference'}},validate_plan,
            lambda data:normalize_day_candidate_pools(canonicalize_decision(data)),decision_schema(day_pools,t.preferences.interests)),plan_id)
        plan=hydrate_decision(plan_decision,day_pools)
        ItineraryPlan.model_validate(plan)
        if store:_persist(store,plan_id,plan,runner,plan_id,coverage_warnings)
    # Historical declarations are retained only as input history, never as planning truth.
    if store:
        store.save_valid(plan_id,plan,plan_sig,0,0,observability={'repair_result':'interest_declarations_removed'},source_payload=plan)
    allocation_id='itinerary-allocation'; allocation=None; allocation_sig=None
    if store:
        allocation_sig=store.expected_signature(allocation_id,dependencies(allocation_id,store.days))
        allocation=store.get_valid(allocation_id,allocation_sig)
    if allocation is None:
        if store:store.mark_running(allocation_id)
        try:allocation=allocate_trip(plan,places,must_go=t.preferences.mustGo,interests=t.preferences.interests)
        except AllocationError as exc:raise StageValidationError(allocation_id,exc.errors,0,0) from None
        if store:store.save_valid(allocation_id,allocation,allocation_sig,0,0)
    # The research gate can resolve unique names, while this second gate can
    # disambiguate branches using the day skeleton and neighboring POIs.
    contextual_mapping={}
    if place_resolver:
        referenced=set();contexts={}
        for plan_day in plan['days']:
            outline=allocation_outline(allocation,plan_day)
            ordered=[outline['primary_anchor_id'],*outline.get('candidate_place_ids',[]),
                     *([outline['fixed_meal_stop_id']] if outline.get('fixed_meal_stop_id') else []),
                     *outline.get('backup_candidate_ids',[])]
            referenced.update(ordered)
            contexts.update(build_day_resolution_contexts(places,ordered,outline.get('area_labels',[])))
        contextual_gate=await enforce_pre_schedule_resolution(places,place_resolver,referenced,contexts)
        contextual_mapping=contextual_gate.id_mapping
        resolution_gate_warnings.extend(contextual_gate.warnings)
        if contextual_gate.id_mapping:
            plan,allocation=apply_resolution_mapping(contextual_gate.id_mapping,plan,allocation)
            if store:
                store.apply_reference_mapping(contextual_gate.id_mapping)
                for pid,types in pack_types.items():
                    _persist(store,pid,{'places':[p for p in places if p['type'] in types]},runner,pid,resolution_gate_warnings)
                plan_sig=store.expected_signature(plan_id,dependencies(plan_id,store.days))
                store.save_valid(plan_id,plan,plan_sig,0,0,observability={'repair_result':'reference_remap'},source_payload=plan)
                allocation_sig=store.expected_signature(allocation_id,dependencies(allocation_id,store.days))
                store.save_valid(allocation_id,allocation,allocation_sig,0,0,observability={'repair_result':'reference_remap'})
    async def build_day(i):
        outline=allocation_outline(allocation,plan['days'][i])
        pack_id=f'itinerary-day-{i+1}'
        if store:
            sig=store.expected_signature(pack_id,dependencies(pack_id,store.days)); saved=store.get_valid(pack_id,sig)
            if saved:return saved
            store.mark_running(pack_id); progress(pack_id,35+round((i/max(1,t.days))*18))
        context=day_catalog(outline,places)
        source=store.get_source_payload(pack_id) if store and hasattr(store,'get_source_payload') else None
        if source and 'optional_stop_order' in source:
            decision=canonicalize_day_decision(source)
            errors=validate_day_decision(decision,outline)
            if errors:source=None
        if not source:
            decision=await within_pack(pack_id,runner.ask(f'day-{i+1}',DayDecision,
                '只决定当天可选地点的取舍偏好。optional_stop_order可为空，也可从优先或备用候选中选择；不能输出主锚点、固定用餐、日期、城市、到达时间、距离或路线事实。day_intent写当天主题，practical_notes提供保守的现场调整建议。最终地点顺序和时间由后端调度。',
                context,lambda data:validate_day_decision(data,outline),canonicalize_day_decision,day_decision_schema(outline)),f'day-{i+1}')
        day=await schedule_day_with_routes(decision,outline,places,t,i,route_service,
                                           store.connect if store and getattr(store,'connect',None) else None,
                                           store.job_id if store else None,trip_constraints=trip_constraints)
        strengths=derive_day_interests(day,places,t.preferences.interests)
        day['derived_interests']=list(strengths);day['day_interest_strength']=strengths
        required=(outline['primary_anchor_id'],outline.get('fixed_meal_stop_id'))
        semantic=semantic_day(day,places,expected_date=outline['date'],expected_city=outline['city'],required_ids=required)
        if semantic: raise StageValidationError(f'day-{i+1}',semantic)
        if store:_persist(store,pack_id,day,runner,f'day-{i+1}')
        return day
    itinerary=await collect(*(build_day(i) for i in range(t.days)))
    # Expensive official fact enrichment is scoped to places that survived
    # allocation and scheduling. Research-only candidates keep their identity
    # evidence but do not consume search/fetch/extraction budget.
    if official_service:
        scheduled_ids={stop['place_id'] for day in itinerary for stop in day.get('stops',[])}
        scheduled_places=[place for place in places if place['id'] in scheduled_ids]
        resolution_gate_warnings.extend(await official_service.resolve_places(scheduled_places))
        if store:
            for pid,types in pack_types.items():
                _persist(store,pid,{'places':[p for p in places if p['type'] in types]},runner,pid,
                         resolution_gate_warnings)
    if store:
        try:
            allocation=finalize_selected(allocation,itinerary,places)
            ownership_errors=validate_cross_day_ownership(allocation,places,itinerary)
        except AllocationError as exc:
            raise StageValidationError(allocation_id,exc.errors,0,0) from None
        if ownership_errors:raise StageValidationError(allocation_id,ownership_errors,0,0)
        store.save_valid(allocation_id,allocation,allocation_sig,0,0)
        store.mark_running('itinerary-coherence'); progress('itinerary-coherence',54)
        coherence=validate_trip_coherence(itinerary,plan,places,t.preferences.model_dump(mode='json'))
        interest_errors,interest_warnings=validate_actual_interest_coverage(itinerary,plan,places,t.preferences.interests)
        coherence.extend(interest_errors)
        if coherence:
            for pid in {e['pack_id'] for e in coherence}: store.invalidate(pid,[e for e in coherence if e['pack_id']==pid])
            raise StageValidationError('itinerary-coherence',coherence)
        for i,day in enumerate(itinerary,1):
            pid=f'itinerary-day-{i}';store.save_valid(pid,day,store.expected_signature(pid,dependencies(pid,store.days)),0,0,
                observability={'repair_result':'derived_interests'},source_payload=store.get_source_payload(pid))
        _persist(store,'itinerary-coherence',{'passed':True,'errors':[]},warnings=interest_warnings)
    progress('modules-practical',55)
    cached_practical=store.get_valid('modules-practical',store.expected_signature('modules-practical',dependencies('modules-practical',store.days))) if store else None
    budget,CompactMenu,BudgetSnacks,BudgetPreparation=practical_request_models(t.days)
    enrichment_warnings=list(resolution_gate_warnings); enrichment_state={'practical':'complete','language_notes':'complete','pending_packs':[]}
    if cached_practical:
        menu=cached_practical['food']['menu_guide']; snacks={'local_snacks':cached_practical['food']['local_snacks']}; prep=cached_practical['preparation']
    else:
        if store: store.mark_running('modules-practical')
        try:
            menu, snacks, prep=await within_pack('modules-practical',collect(
                runner.ask('menu-guide',CompactMenu,f"目的地菜单入门：正好{budget['menu_cards']}卡和{budget['menu_terms']}个词典项，不包含价格。",compact),
                runner.ask('snacks',BudgetSnacks,f"生成正好{budget['snacks']}种当地小吃和点餐说明。",compact),
                runner.ask('preparation',BudgetPreparation,f"必备物品和预约确认合计正好{budget['checklist']}项，ID和标题唯一，关联实际日期、同行人与行程。",itinerary)),'modules-practical')
        except BaseException as exc:
            if not _find_timeout(exc):raise
            enrichment_state['practical']='deferred';enrichment_state['pending_packs'].append('modules-practical')
            enrichment_warnings.append('行前准备与餐饮补充内容生成超时，已保留核心行程，可继续生成。')
            menu={'title':'菜单入门待补充','intro':'补充内容尚未完成，实际点餐与消费信息出发前请复核。','cards':[],'dictionary':[]}
            snacks={'local_snacks':[]};prep={'essentials':[],'confirm_ahead':[]}
    exp=[]
    for kind,title in [('culture','文化与艺术'),('nature','自然观察'),('food_workshop','饮食文化')]:
        items=[{'place_id':p['id']} for p in places if p['type']=='experience' and p['experience_type']==kind]
        if items:exp.append({'title':title,'items':items})
    progress('modules-language-notes',70)
    cached_language=store.get_valid('modules-language-notes',store.expected_signature('modules-language-notes',dependencies('modules-language-notes',store.days))) if store else None
    if store and not cached_language: store.mark_running('modules-language-notes')
    scenarios=['地点与方位','点餐与食材','公共交通','参观礼仪','支付与求助']
    GroupModel,NoteModel=language_request_models(t.days)
    active_scenarios=scenarios[:budget['language_groups']]
    try:
        lg=[] if cached_language else await within_pack('modules-language-notes',collect(*(runner.ask(f'{family}-{i}',GroupModel,f"中文场景标题：{scene}。生成{budget['language_items']}条不同的{label}，原文放text，中文含义meaning，pronunciation不需要时空字符串。")
            for family,label in [('keywords','关键词'),('phrases','实用短语')] for i,scene in enumerate(active_scenarios))),'language-groups')
    except BaseException as exc:
        if not _find_timeout(exc):raise
        lg=[]; enrichment_state['language_notes']='deferred';enrichment_state['pending_packs'].append('modules-language-notes')
        enrichment_warnings.append('语言与旅行贴士生成超时，已保留核心行程，可继续生成。')
    for i,group in enumerate(lg): group['title']=scenarios[i%5]
    async def build_note(cat,title):
        def validate_note(data):
            return [] if data['category']==cat else [{'pointer':'/category','message':f'category必须是{cat}。'}]
        return await runner.ask(f'note-{cat}',NoteModel,f"只生成category={cat}，中文标题{title}，{budget['travel_note_items']}个主题写清具体地点情境与行动，避免实时数字。",compact,validate_note)
    travel_notes=[]
    if cached_language:travel_notes=cached_language['travel_notes']
    elif enrichment_state['language_notes']=='complete':
        try:
            travel_notes=await within_pack('modules-language-notes',collect(*(build_note(cat,title)
                for cat,title in [('weather','天气'),('culture','文化礼仪'),('transport','交通'),('safety','安全'),('payment','支付')][:budget['travel_note_categories']])), 'travel-notes')
        except BaseException as exc:
            if not _find_timeout(exc):raise
            lg=[];travel_notes=[];enrichment_state['language_notes']='deferred';enrichment_state['pending_packs'].append('modules-language-notes')
            enrichment_warnings.append('语言与旅行贴士生成超时，已保留核心行程，可继续生成。')
    food={'menu_guide':menu,'local_snacks':snacks['local_snacks'],'dedicated_trip':[{'place_id':p['id']} for p in places if p['type']=='restaurant'],
          'reliable_chains':[{'place_id':p['id']} for p in places if p['type']=='chain']}
    # Food/place references are a deterministic view over the current canonical pool.
    # Preserve generated prose/checklists while never retaining an obsolete place ID.
    practical=({'experiences':exp,'food':food,'preparation':prep} if not cached_practical else
               {**cached_practical,'experiences':exp,'food':food})
    split=len(active_scenarios)
    language_notes=cached_language or {'language':{'edition':'当地用语与普通话沟通','keyword_groups':lg[:split],'phrase_groups':lg[split:]},'travel_notes':travel_notes}
    if store:
        if enrichment_state['practical']=='complete' and (not cached_practical or contextual_mapping):
            _persist(store,'modules-practical',practical,runner,'preparation')
        if not cached_language and enrichment_state['language_notes']=='complete':_persist(store,'modules-language-notes',language_notes,runner,'note-payment')
    return {'places-core':{'places':[p for p in places if p['type']=='sight']},
            'places-experiences':{'places':[p for p in places if p['type']=='experience']},
            'places-food':{'places':[p for p in places if p['type'] in ('restaurant','chain')]},
            'itinerary':{'itinerary':itinerary},'modules-practical':practical,
            'modules-language-notes':language_notes,'enrichment':enrichment_state,
            'enrichment_warnings':dedupe_warnings(('enrichment', enrichment_warnings))}

