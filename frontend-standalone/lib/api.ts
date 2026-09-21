import type { Trip, TripDraft } from '@/types/trip';
import { normalizeErrorDetails } from '@/lib/error-details';
export { normalizeErrorDetails } from '@/lib/error-details';

export interface Place {
  id: string; type: 'sight'|'experience'|'restaurant'|'chain'; city: string;
  canonical_name:string;local_name:string;english_name:string|null;display_name:string;
  name_source:'official'|'map_provider'|'offline_reference'|'model_knowledge'|'derived';
  provider:'amap'|'llm_generated'|'manual'|'offline_reference';provider_place_id:string|null;
  district:string|null;address:string|null;latitude:number|null;longitude:number|null;
  category:string;subcategory:string|null;place_type:'sight'|'restaurant'|'experience'|'transport'|'hotel'|'generated_experience'|'chain'|null;
  source_confidence:number|null;source_metadata:Record<string,unknown>;fact_provenance:Record<string,unknown>;
  official_facts?:Record<string,{value:unknown;source_url:string|null;source_type:string|null;extracted_at:string|null;
    confidence:number;raw_evidence:string;status:'verified'|'partially_verified'|'needs_recheck'|'unavailable'|'conflicting';
    sources:{value:unknown;source_url:string;source_type:string;extracted_at:string;confidence:number;raw_evidence:string}[]}>;
  verification_status:'verified'|'partially_verified'|'unverified'|'generated';entity_kind:'poi'|'experience_concept';
  experience_title:string|null;linked_place_id:string|null;
  booking_status:'verified_bookable'|'unverified'|'not_bookable'|'not_applicable';
  description: string;
  official_url: string|null; map_url: string|null; coordinates: null; rating: null;
  hours_note: string; ticket_note: string; reservation_note: string;
  cuisine: string; signature_dishes: string;
  semantic_tags?:string[];interest_affinity?:Record<string,number>;semantic_source?:string[];
  semantic_confidence?:Record<string,'low'|'medium'|'high'>;affinity_reasons?:Record<string,string[]>;
  semantic_version?:string;
  experience_status?:'verified_experience'|'suggested_experience'|null;
  user_created?:boolean;custom_activity?:{activity_id:string;title:string;note:string;start_time_preference:string|null;
    stay_minutes:number;location_optional:string|null;linked_place_id:string|null;user_created:boolean}|null;
}
export interface ProfileWarning {
  code:string;message:string;severity:'warning';day:number|null;place_id:string|null;
  metadata:Record<string,unknown>;pointer?:string|null;invalid_value?:unknown;
}
type Ref = {place_id:string};
type Note = {title:string;note:string};
type LanguageGroup = {title:string;items:{text:string;meaning:string;pronunciation:string}[]};
export interface Profile {
  schema_version:'seven-v1'; id:string; display_name:string;
  trip: TripDraft;
  generation:{mode:'llm'|'offline'|'replay'|'mock';degraded:boolean;warnings:ProfileWarning[];diagnostics?:ProfileWarning[];created_at:string};
  places:Place[];
  itinerary:{date:string;city:string;theme:string;summary:string;
    periods:Record<'morning'|'afternoon'|'evening',{title:string;description:string}>;
    photo_advice:Note[];
    derived_interests?:string[];day_interest_strength?:Record<string,number>;
    total_travel_minutes?:number;total_visit_minutes?:number;schedule_warnings?:ProfileWarning[];
    density_evaluation?:{available_minutes:number;scheduled_minutes:number;visit_minutes:number;meal_minutes:number;
      total_route_minutes:number;fallback_schedule_minutes?:number;configured_buffer_minutes:number;utilization:number;target_range:{minimum:number;maximum:number};
      stop_count:number;attraction_count:number;meal_count:number;idle_gap_minutes:number;longest_idle_gap_minutes:number;
      fill_attempt_count:number;added_place_ids:string[];remaining_candidate_count:number;reduced_window:boolean;issues:string[]}|null;
    planning_notes?:ProfileWarning[];
    segments?:{segment_id:string;from_place_id:string;to_place_id:string;mode:'walking'|'public_transit'|'driving';
      distance_meters:number|null;duration_minutes:number|null;buffer_minutes:number;fallback_schedule_minutes:number;
      idle_minutes:number;route_status:'verified'|'estimated_by_distance'|'unresolved';route_provider?:'amap'|'deterministic_estimate'|null}[];
    stops:{place_id:string;arrival_time:string;dwell_minutes:number;practical_note:string;time_guard:string;cost_note:string;
      transfer_minutes:number|null;distance_km:number|null;transport_mode:string;
      route_minutes?:number|null;buffer_minutes?:number;fallback_schedule_minutes?:number;idle_minutes?:number;
      preferred_start_time?:string|null;hard_start_time?:string|null;locked?:boolean;must_keep?:boolean;user_created?:boolean;
      note?:string;priority?:number;transport_to_next?:'auto'|'walking'|'public_transit'|'driving'|'taxi'|null;
      meal_role?:'breakfast'|'lunch'|'dinner'|'snack'|'cafe'|'flexible'|null;
      meal_window_preferred_start?:string|null;meal_window_preferred_end?:string|null;
      meal_window_acceptable_start?:string|null;meal_window_acceptable_end?:string|null;
      meal_window_status?:'preferred'|'acceptable'|'violation'|'not_applicable';
      meal_timing_penalty?:number;meal_timing_reason?:string;
      route_status?:'not_applicable'|'verified'|'estimated_by_distance'|'unresolved';route_provider?:'amap'|'deterministic_estimate'|null;route_queried_at?:string|null}[];}[];
  module_groups:{
    sights:{scheduled:Ref[];optional:Ref[]};
    experiences:{title:string;items:Ref[]}[];
    food:{menu_guide:{title:string;intro:string;cards:Note[];dictionary:{term:string;meaning:string;ordering_note:string}[]};local_snacks:{name:string;description:string;where_to_find:string;ordering_note:string}[];dedicated_trip:Ref[];reliable_chains:Ref[]};
    preparation:{essentials:{id:string;title:string;note:string}[];confirm_ahead:{id:string;title:string;note:string}[]};
    language:{edition:string;keyword_groups:LanguageGroup[];phrase_groups:LanguageGroup[]};
    travel_notes:{category:string;title:string;summary:string;items:Note[]}[];
  };
  preference_evaluation?:{selected_interest_ids:string[];interest_coverage:number;interest_strength:Record<string,number>;interest_diversity:number;preference_fit_score:number;warnings:{code:string;message:string}[]};
  quality_evaluation?:{food:{recommended_scene_target:number;actual_scene_count:number;food_city_coverage:number;food_scene_diversity:number;local_food_relevance:number;food_interest_match:number;meal_schedule_coverage:number;food_quality_score:number};warnings:{pointer:string;code:string;message:string}[]};
  fact_verification?:{version:string;verified_place_count:number;partially_verified_place_count:number;
    unverified_place_count:number;generated_experience_count:number;verified_place_rate:number;route_leg_count:number;
    verified_route_count:number;estimated_route_count:number;unresolved_route_count:number;verified_route_rate:number;
    real_poi_resolution_rate:number;scheduled_real_poi_count:number;resolved_real_poi_count:number;
    scheduled_real_poi_resolution_rate:number;ambiguous_real_poi_count:number;unresolved_real_poi_count:number;
    llm_place_count_in_final_itinerary:number;route_segment_count:number;fallback_route_count:number;route_coverage_rate:number;
    official_source_count:number;verified_dynamic_fact_count:number;conflicting_dynamic_fact_count:number;unavailable_dynamic_fact_count:number;
    medium_facts_needing_recheck:number;verified_experience_count:number;suggested_experience_count:number;
    generated_experience_in_schedule_count:number;scheduled_place_fact_coverage:number;official_fact_coverage:number;
    provider_fact_coverage:number;needs_recheck_count:number;scheduled_fact_details:{place_id:string;place_name:string;needs_recheck:string[]}[];
    hard_fact_errors:{pointer:string;code:string;message:string}[];
    warnings:{pointer:string;code:string;message:string}[];verified_at:string};
  enrichment?:{practical:'complete'|'deferred';language_notes:'complete'|'deferred';pending_packs:string[]};
}
export type BuildErrorCategory='provider'|'validation'|'persistence'|'compiler'|'worker_runtime'|'unknown_internal'|'configuration';
export interface Job {job_id:string;status:'queued'|'running'|'repairing'|'validating'|'paused'|'done'|'done_with_warnings'|'failed';stage:string;progress:number;degraded:boolean;warnings:ProfileWarning[];error:{message:string;category?:BuildErrorCategory;error_id?:string|null}|null;current_pack?:string|null;completed_packs?:number;total_packs?:number;repairing?:boolean;validation_errors?:{pointer:string;code?:string;message:string}[];validation_warnings?:{pointer:string;code?:string;message:string}[];poi_resolution_metrics?:{poi_resolution_rate:number;verified_poi_rate:number;ambiguous_poi_rate:number;generated_experience_rate:number};route_metrics?:{route_leg_count:number;route_success_rate:number;route_estimated_rate:number;route_unresolved_rate:number;schedule_feasibility_rate:number;avg_daily_travel_minutes:number;avg_daily_stop_count:number;avg_day_utilization:number;underfilled_day_count:number;long_idle_gap_day_count:number;fill_added_place_count:number};fact_verification_metrics?:{verified_place_rate:number;verified_route_rate:number;real_poi_resolution_rate:number;scheduled_real_poi_count:number;resolved_real_poi_count:number;scheduled_real_poi_resolution_rate:number;ambiguous_real_poi_count:number;unresolved_real_poi_count:number;llm_place_count_in_final_itinerary:number;route_segment_count:number;fallback_route_count:number;route_coverage_rate:number;unverified_place_count:number;generated_experience_count:number;estimated_route_count:number;unresolved_route_count:number;official_source_count:number;verified_dynamic_fact_count:number;conflicting_dynamic_fact_count:number;unavailable_dynamic_fact_count:number;medium_facts_needing_recheck:number;verified_experience_count:number;suggested_experience_count:number;generated_experience_in_schedule_count:number;scheduled_place_fact_coverage:number;official_fact_coverage:number;provider_fact_coverage:number;needs_recheck_count:number}|null}
export type TripSummary=Pick<Trip,'id'|'title'|'startDate'|'endDate'|'travelers'|'budget'|'updatedAt'|'status'|'destinations'> & {degraded?:boolean};

export class ReplanApiError extends Error {
  constructor(public code:string,public userTitle:string,message:string,public scope:string='current_day',public affectedDayIds:number[]=[],public details:unknown[]=[]){super(message);this.name='ReplanApiError';}
}

async function request<T>(path:string, init:RequestInit={}, timeoutMs=20000):Promise<T> {
  const timeout=AbortSignal.timeout(timeoutMs);let response:Response;
  try{response=await fetch(path,{...init,cache:'no-store',signal:init.signal ? AbortSignal.any([init.signal,timeout]) : timeout});}
  catch(reason){
    if(reason instanceof DOMException&&(reason.name==='TimeoutError'||reason.name==='AbortError'))throw new Error(path.includes('replan')?'重规划预览超时，请稍后重试':'请求超时，请稍后重试');
    throw new Error('无法连接行程服务');
  }
  const data=await response.json().catch(()=>null);
  if(!response.ok) {
    const err=data?.error??data?.detail;
    const detailItems=normalizeErrorDetails(err?.details);
    const details=detailItems.map((value)=>{const x=value as {pointer?:string;message?:string};return x?.message?` ${x.pointer?`${x.pointer}: `:''}${x.message}`:'';}).join('');
    if(err?.code?.startsWith?.('REPLAN_')&&err?.user_title&&err?.user_message){
      throw new ReplanApiError(err.code,err.user_title,err.user_message,err.scope,err.affected_day_ids,detailItems);
    }
    const byCode:Record<string,string>={REPLAN_INSTRUCTION_PARSE_FAILED:'没有完全理解你的调整要求',REPLAN_PROVIDER_UNAVAILABLE:'智能调整服务暂时不可用'};
    const byStatus:Record<number,string>={404:'重规划接口不可用',422:'调整请求格式错误',500:'重规划处理发生内部错误',503:'模型服务暂时不可用'};
    throw new Error((byCode[err?.code]??err?.message??byStatus[response.status]??`行程服务返回错误（${response.status}）`)+details);
  }
  return data as T;
}
export function buildTrip(draft:TripDraft) {
  const {origin,destinations,mainDestination,startDate,endDate,adults,children,seniors,travelStyle,budget,preferences,notes,outboundPeriod,returnPeriod}=draft;
  const broadBudget={total:budget.total,accommodation:0,food:0,transportation:0,activities:0,shopping:0,other:0};
  const intakePreferences={...preferences,interests:preferences.interests.filter((value)=>!/购物|美妆|时尚|买手店/.test(value)),longDistance:'agent',localTransport:['transit','taxi'],accommodation:''};
  return request<{job_id:string}>('/api/build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({origin,destinations,mainDestination,startDate,endDate,adults,children,seniors,travelStyle,budget:broadBudget,preferences:intakePreferences,notes,outboundPeriod:'flexible',returnPeriod:'flexible'})});
}
export const getJob=(id:string,signal?:AbortSignal)=>request<Job>(`/api/build/${encodeURIComponent(id)}`,{signal});
export const resumeBuild=(id:string)=>request<{job_id:string;status:'queued'}>(`/api/build/${encodeURIComponent(id)}/resume`,{method:'POST'});
export const getProfile=(id:string,signal?:AbortSignal)=>request<Profile>(`/api/build/${encodeURIComponent(id)}/result`,{signal});
export const listTrips=(signal?:AbortSignal)=>request<{items:TripSummary[]}>('/api/trips',{signal});
export type LocalReplanRequest =
  | {action:'remove_stop';day:number;place_id:string}
  | {action:'replace_stop';day:number;place_id:string;replacement_place_id:string}
  | {action:'regenerate_day';day:number}
  | {action:'change_pace';pace:'relaxed'|'balanced'|'intensive'}
  | {action:'change_transport';local_transport:('transit'|'taxi'|'self_drive'|'cycling')[]};
export type LocalReplanResult={job_id:string;replan_id:string;status:string;affected_days:number[];
  affected_packs:string[];replan_version:string;idempotent_replay:boolean};
export type ReplanOptions={job_id:string;day:number;city:string;pace:'relaxed'|'balanced'|'intensive';
  local_transport:('transit'|'taxi'|'self_drive'|'cycling')[];
  stops:{place_id:string;display_name:string;role:string;removable:boolean;replaceable:boolean}[];
  replacement_candidates:{place_id:string;display_name:string;place_type:string|null;description:string;pool:'preferred'|'backup'}[]};
export const replanTrip=(id:string,mutation:LocalReplanRequest,idempotencyKey?:string)=>request<LocalReplanResult>(
  `/api/trips/${encodeURIComponent(id)}/replan`,{method:'POST',headers:{'Content-Type':'application/json',
    ...(idempotencyKey?{'Idempotency-Key':idempotencyKey}:{})},body:JSON.stringify(mutation)},120000);
export const getReplanOptions=(id:string,day:number,signal?:AbortSignal)=>request<ReplanOptions>(
  `/api/trips/${encodeURIComponent(id)}/replan-options?day=${day}`,{signal});
export const listReplans=(id:string,signal?:AbortSignal)=>request<{items:{replan_id:string;action:string;day:number|null;
  status:string;affected_packs:string[];error:{code:string;message:string}|null;created_at:string;completed_at:string|null}[]}>(
  `/api/trips/${encodeURIComponent(id)}/replans`,{signal});

export type EditableTransportMode='auto'|'walking'|'public_transit'|'driving'|'taxi';
export type AmapPlaceCandidate={provider_place_id:string;name:string;address:string|null;district:string|null;city:string|null;
  latitude:number|null;longitude:number|null;category:string;subcategory:string|null};
export type ReplanDiffItem={place_id:string;name:string;day?:number;time?:string;before?:string|null;after?:string|null};
export type EditableReplanDiff={added:ReplanDiffItem[];removed:ReplanDiffItem[];moved:ReplanDiffItem[];
  transport_changed:ReplanDiffItem[];before?:{time:string;name:string}[];after?:{time:string;name:string}[];
  affected_day_ids?:number[];days?:Array<EditableReplanDiff&{day:number}>};
export type EditablePreview={preview_id:string;day:number;action:string;scope:'current_day'|'affected_days'|'whole_trip';
  affected_day_ids:number[];unplaced_place_ids?:string[];diff:EditableReplanDiff;
  parsed_constraints?:{actions?:Array<{type:string;pace?:string;mode?:string;level?:string}>};
  warnings?:Array<{code:string;message:string;severity?:'warning';day?:number|null;place_id?:string|null}>;expires_at:string};
export type ReplanScopeNotice={requires_scope_confirmation:true;code:'REPLAN_SCOPE_AFFECTED_DAYS'|'REPLAN_SCOPE_WHOLE_TRIP';
  user_title:string;user_message:string;scope:'affected_days'|'whole_trip';affected_day_ids:number[];
  details:{preview:EditablePreview;unplaced_place_ids:string[]}};
export type EditableResult={job_id:string;day:number;revision_id:string;day_schedule_version:number;status:string;
  affected_day_ids?:number[];revision_ids?:string[];diff:EditableReplanDiff;replan_version:string};

export const searchAddStop=(tripId:string,dayId:string,input:{query:string;city:string;preferred_time?:string|null;
  stay_minutes?:number|null;provider_place_id?:string|null})=>request<EditablePreview|{requires_confirmation:true;candidates:AmapPlaceCandidate[]}>(
  `/api/trips/${encodeURIComponent(tripId)}/days/${encodeURIComponent(dayId)}/stops/search-add`,
  {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)},120000);
export const addCustomActivity=(tripId:string,dayId:string,input:{title:string;note?:string;start_time_preference?:string|null;
  stay_minutes:number;location_optional?:string|null;linked_place_id?:string|null})=>request<EditablePreview>(
  `/api/trips/${encodeURIComponent(tripId)}/days/${encodeURIComponent(dayId)}/custom-activities`,
  {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)},120000);
export const editTripStop=(tripId:string,dayId:string,placeId:string,input:{stay_minutes?:number;preferred_start_time?:string|null;
  hard_start_time?:string|null;locked?:boolean;must_keep?:boolean;note?:string;priority?:number;transport_to_next?:EditableTransportMode|null})=>request<EditableResult>(
  `/api/trips/${encodeURIComponent(tripId)}/days/${encodeURIComponent(dayId)}/stops/${encodeURIComponent(placeId)}`,
  {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)},120000);
export const deleteTripStop=(tripId:string,dayId:string,placeId:string)=>request<EditableResult>(
  `/api/trips/${encodeURIComponent(tripId)}/days/${encodeURIComponent(dayId)}/stops/${encodeURIComponent(placeId)}`,{method:'DELETE'},120000);
export const setDayTransport=(tripId:string,dayId:string,transport:EditableTransportMode)=>request<EditableResult>(
  `/api/trips/${encodeURIComponent(tripId)}/days/${encodeURIComponent(dayId)}/transport`,
  {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({transport_mode:transport})},120000);
export const setSegmentTransport=(tripId:string,dayId:string,originPlaceId:string,destinationPlaceId:string,transport:EditableTransportMode)=>request<EditableResult>(
  `/api/trips/${encodeURIComponent(tripId)}/days/${encodeURIComponent(dayId)}/segments/transport`,
  {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({origin_place_id:originPlaceId,destination_place_id:destinationPlaceId,transport_mode:transport})},120000);
export const previewDayReplan=(tripId:string,dayId:string,instruction:string)=>request<EditablePreview|ReplanScopeNotice>(
  `/api/trips/${encodeURIComponent(tripId)}/days/${encodeURIComponent(dayId)}/replan-preview`,
  {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction})},120000);
export const previewTripReplan=(tripId:string,anchorDayId:string,instruction:string)=>request<EditablePreview|ReplanScopeNotice>(
  `/api/trips/${encodeURIComponent(tripId)}/replan-preview`,
  {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,anchor_day_id:anchorDayId,ui_scope_context:'trip'})},120000);
export const confirmReplanPreview=(tripId:string,previewId:string)=>request<EditableResult>(
  `/api/trips/${encodeURIComponent(tripId)}/replan-previews/${encodeURIComponent(previewId)}/confirm`,{method:'POST'},120000);
export const undoDayReplan=(tripId:string,dayId:string)=>request<EditableResult>(
  `/api/trips/${encodeURIComponent(tripId)}/days/${encodeURIComponent(dayId)}/undo`,{method:'POST'},120000);

export function toTrip(p:Profile):Trip {
  const t=p.trip;
  const colors=['#4e633c','#a84e42','#657b83','#b28a48','#785b78'];
  return {id:p.id,title:p.display_name,origin:t.origin,destinations:t.destinations,mainDestination:t.mainDestination,
    startDate:t.startDate,endDate:t.endDate,travelers:{adults:t.adults,children:t.children,seniors:t.seniors,style:t.travelStyle},
    budget:t.budget,preferences:t.preferences,status:'completed',updatedAt:p.generation.created_at,shared:false,
    transportSegments:[],hotelStays:[],dataWarnings:p.generation.warnings.map(warning=>warning.message),profile:p,
    days:p.itinerary.map((d,i)=>({id:`${p.id}-day-${i+1}`,dayNumber:i+1,date:d.date,city:d.city,
      routeColor:colors[i%colors.length],note:d.summary,weather:{icon:'cloud',label:'天气出发前请复核',low:0,high:0},
      segments:(d.segments??d.stops.slice(1).map((s,j)=>({segment_id:`${d.stops[j].place_id}->${s.place_id}`,
        from_place_id:d.stops[j].place_id,to_place_id:s.place_id,
        mode:s.transport_mode.includes('步行')?'walking' as const:s.transport_mode.includes('公共')?'public_transit' as const:'driving' as const,
        distance_meters:s.distance_km==null?null:Math.round(s.distance_km*1000),duration_minutes:s.transfer_minutes,
        buffer_minutes:s.buffer_minutes??0,fallback_schedule_minutes:s.fallback_schedule_minutes??0,idle_minutes:s.idle_minutes??0,
        route_status:s.route_status==='not_applicable'?'unresolved' as const:(s.route_status??'unresolved'),route_provider:s.route_provider??null}))).map(segment=>({
          id:segment.segment_id,fromPlaceId:segment.from_place_id,toPlaceId:segment.to_place_id,
          fromName:p.places.find(place=>place.id===segment.from_place_id)?.display_name??segment.from_place_id,
          toName:p.places.find(place=>place.id===segment.to_place_id)?.display_name??segment.to_place_id,
          mode:segment.mode,durationMinutes:segment.duration_minutes,distanceMeters:segment.distance_meters,
          bufferMinutes:segment.buffer_minutes,fallbackScheduleMinutes:segment.fallback_schedule_minutes,
          idleMinutes:segment.idle_minutes,routeStatus:segment.route_status,routeProvider:segment.route_provider??null})),
      activities:d.stops.map((s,j)=>{
        const place=p.places.find(x=>x.id===s.place_id);
        if(!place)throw new Error('档案地点引用不完整');
        const [h,m]=s.arrival_time.split(':').map(Number); const end=h*60+m+s.dwell_minutes;
        return {id:`${p.id}-${i}-${j}`,dayId:`${p.id}-day-${i+1}`,title:place.display_name,city:d.city,
          kind:place.type==='restaurant'?'meal' as const:'attraction' as const,start:s.arrival_time,
          end:`${String(Math.floor(end/60)).padStart(2,'0')}:${String(end%60).padStart(2,'0')}`,
          duration:`建议 ${s.dwell_minutes} 分钟`,cost:0,costStatus:'unknown' as const,
          reason:s.note||s.practical_note,coord:[place.latitude??0,place.longitude??0] as [number,number],
          coordinatesUnknown:place.longitude==null||place.latitude==null,locked:Boolean(s.locked),placeId:place.id,
          mustKeep:Boolean(s.must_keep),userCreated:Boolean(s.user_created||place.user_created),stayMinutes:s.dwell_minutes,
          preferredStartTime:s.preferred_start_time??null,hardStartTime:s.hard_start_time??null,priority:s.priority??3,
          transportToNext:null,mealRole:s.meal_role??null,
          mealWindow:s.meal_window_preferred_start&&s.meal_window_preferred_end?{
            preferredStart:s.meal_window_preferred_start,preferredEnd:s.meal_window_preferred_end,
            status:s.meal_window_status??'not_applicable'}:null,
          mealTimingReason:s.meal_timing_reason??''};
      })}))};
}
export const loadTrip=async(id:string,signal?:AbortSignal)=>toTrip(await getProfile(id,signal));

// Legacy calls are permitted only on the unchanged demonstration route.
// Keeping the guard here prevents any new archive from falling back to mock data.
export async function legacyFetch(path:string, init:RequestInit={}):Promise<Response> {
  if(typeof window==='undefined'||!/^\/trip\/demo-trip(?:\/|$)/.test(window.location.pathname)) {
    return Response.json({message:'后续开放：本轮支持生成与阅读'}, {status:501});
  }
  return fetch(path.replace(/^\/api\//,'/mock-api/'),{...init,signal:init.signal ? AbortSignal.any([init.signal,AbortSignal.timeout(20000)]) : AbortSignal.timeout(20000)});
}
