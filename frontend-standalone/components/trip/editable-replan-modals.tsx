"use client";

import { useState } from "react";
import { Warning } from "@phosphor-icons/react";
import { Button, Field, Modal, Segmented } from "@/components/ui";
import { ReplanApiError, addCustomActivity, editTripStop, previewDayReplan, previewTripReplan, searchAddStop, setDayTransport } from "@/lib/api";
import type { AmapPlaceCandidate, EditablePreview, EditableTransportMode, ReplanScopeNotice } from "@/lib/api";
import type { Activity, TripDay } from "@/types/trip";

export type DemoAddDraft={kind:"place"|"activity";title:string;note:string;preferredStartTime:string|null;stayMinutes:number};

export function EditableAddModal({tripId,day,onClose,onPreview,onDemoPreview}:{tripId:string;day:TripDay;onClose:()=>void;onPreview:(value:EditablePreview)=>void;onDemoPreview?:(value:DemoAddDraft)=>void}) {
  const [kind,setKind]=useState<"place"|"activity">("place");
  const [query,setQuery]=useState(""); const [title,setTitle]=useState(""); const [note,setNote]=useState("");
  const [time,setTime]=useState(""); const [minutes,setMinutes]=useState(90);
  const [candidates,setCandidates]=useState<AmapPlaceCandidate[]>([]); const [selected,setSelected]=useState("");
  const [busy,setBusy]=useState(false); const [error,setError]=useState("");
  const run=async()=>{setBusy(true);setError("");try{
    if(onDemoPreview){
      if(kind==="place"&&!selected){
        const pool=(day.backupPois??[]).filter(item=>!query.trim()||item.name.includes(query.trim())).slice(0,5);
        setCandidates((pool.length?pool.map(item=>({provider_place_id:item.id,name:item.name,address:`${item.city} · 示例候选`,district:null,city:item.city,latitude:item.coord[0],longitude:item.coord[1],category:item.category,subcategory:null})):[{provider_place_id:`demo-${query.trim()}`,name:query.trim(),address:`${day.city} · 示例地点`,district:null,city:day.city,latitude:null,longitude:null,category:"示例地点",subcategory:null}]));
        setSelected(pool[0]?.id??`demo-${query.trim()}`);return;
      }
      const candidate=candidates.find(item=>item.provider_place_id===selected);
      onDemoPreview({kind,title:kind==="place"?(candidate?.name??query.trim()):title.trim(),note,preferredStartTime:time||null,stayMinutes:minutes});return;
    }
    if(kind==="activity") { const value=await addCustomActivity(tripId,day.id,{title,note,start_time_preference:time||null,stay_minutes:minutes});onPreview(value);return; }
    if(!selected){const result=await searchAddStop(tripId,day.id,{query,city:day.city,preferred_time:time||null,stay_minutes:minutes});
      if("requires_confirmation" in result){setCandidates(result.candidates);setSelected(result.candidates[0]?.provider_place_id??"");return;}}
    const value=await searchAddStop(tripId,day.id,{query,city:day.city,preferred_time:time||null,stay_minutes:minutes,provider_place_id:selected});
    if("requires_confirmation" in value){setCandidates(value.candidates);return;} onPreview(value);
  }catch(reason){setError(reason instanceof Error?reason.message:"新增失败，请稍后重试");}finally{setBusy(false);}};
  return <Modal open title="添加地点或活动" onClose={onClose}><div className="activity-editor">
    <Segmented ariaLabel="新增类型" value={kind} onChange={(value)=>{setKind(value);setCandidates([]);setSelected("");}} options={[{value:"place",label:"真实地点"},{value:"activity",label:"自定义活动"}]}/>
    {kind==="place"?<><Field label="地点名称" helper="先通过高德搜索，确认真实地点后再加入当天日程"><input autoFocus value={query} onChange={event=>{setQuery(event.target.value);setCandidates([]);setSelected("");}} placeholder="例如：武康大楼"/></Field>
      {candidates.length>0&&<Field label="选择搜索结果"><select value={selected} onChange={event=>setSelected(event.target.value)}>{candidates.map(item=><option key={item.provider_place_id} value={item.provider_place_id}>{item.name}{item.address?` · ${item.address}`:""}</option>)}</select></Field>}</>
      :<><Field label="活动名称" helper="休息、自由活动、咖啡时间等无需绑定真实 POI"><input autoFocus value={title} onChange={event=>setTitle(event.target.value)} placeholder="例如：回酒店休息"/></Field><Field label="备注"><textarea value={note} onChange={event=>setNote(event.target.value)} placeholder="补充你希望如何安排"/></Field></>}
    <div className="two-fields"><Field label="希望开始时间（可选）"><input type="time" value={time} onChange={event=>setTime(event.target.value)}/></Field><Field label="停留时长（分钟）"><input type="number" min={15} max={480} step={15} value={minutes} onChange={event=>setMinutes(Number(event.target.value))}/></Field></div>
    {error&&<p className="inline-warning"><Warning size={18}/>{error}</p>}<div className="modal-actions"><Button variant="secondary" disabled={busy} onClick={onClose}>取消</Button><Button disabled={busy||(kind==="place"?!query.trim():!title.trim())} onClick={()=>void run()}>{busy?"正在核验与排程…":kind==="place"?(candidates.length?"预览加入日程":"搜索地点"):"预览加入日程"}</Button></div>
  </div></Modal>;
}

export function EditableStopModal({tripId,day,item,onClose,onSaved,onReplace}:{tripId:string;day:TripDay;item:Activity;onClose:()=>void;onSaved:()=>Promise<void>;onReplace:()=>void}) {
  const [minutes,setMinutes]=useState(item.stayMinutes??Math.max(15,Math.round((Date.parse(`2000-01-01T${item.end}`)-Date.parse(`2000-01-01T${item.start}`))/60000)));
  const [preferred,setPreferred]=useState(item.preferredStartTime??""); const [hard,setHard]=useState(item.hardStartTime??"");
  const [locked,setLocked]=useState(item.locked); const [mustKeep,setMustKeep]=useState(Boolean(item.mustKeep));
  const [note,setNote]=useState(item.reason); const [priority,setPriority]=useState(item.priority??3);
  const [busy,setBusy]=useState(false); const [error,setError]=useState("");
  const save=async()=>{if(!item.placeId)return;setBusy(true);setError("");try{await editTripStop(tripId,day.id,item.placeId,{stay_minutes:minutes,preferred_start_time:preferred||null,hard_start_time:hard||null,locked,must_keep:mustKeep,note,priority});await onSaved();}catch(reason){setError(reason instanceof Error?reason.message:"保存失败");}finally{setBusy(false);}};
  return <Modal open title={`编辑 · ${item.title}`} onClose={onClose}><div className="activity-editor"><div className="two-fields"><Field label="停留时长（分钟）"><input type="number" min={15} max={480} step={15} value={minutes} onChange={event=>setMinutes(Number(event.target.value))}/></Field><Field label="优先级"><select value={priority} onChange={event=>setPriority(Number(event.target.value))}><option value={1}>低</option><option value={3}>普通</option><option value={5}>高</option></select></Field></div><div className="two-fields"><Field label="希望开始时间"><input type="time" value={preferred} onChange={event=>setPreferred(event.target.value)}/></Field><Field label="固定开始时间"><input type="time" value={hard} onChange={event=>setHard(event.target.value)}/></Field></div><div className="two-fields"><label><input type="checkbox" checked={locked} onChange={event=>setLocked(event.target.checked)}/> 锁定位置和时间</label><label><input type="checkbox" checked={mustKeep} onChange={event=>setMustKeep(event.target.checked)}/> 必须保留</label></div><Field label="备注"><textarea value={note} onChange={event=>setNote(event.target.value)}/></Field>{error&&<p className="inline-warning"><Warning size={18}/>{error}</p>}<div className="modal-actions"><Button variant="ghost" disabled={busy} onClick={onReplace}>替换地点</Button><Button variant="secondary" disabled={busy} onClick={onClose}>取消</Button><Button disabled={busy} onClick={()=>void save()}>{busy?"正在重排…":"保存并重排"}</Button></div></div></Modal>;
}

export function EditableDayReplanModal({tripId,day,onClose,onPreview,onSaved}:{tripId:string;day:TripDay;onClose:()=>void;onPreview:(value:EditablePreview)=>void;onSaved:()=>Promise<void>}) {
  const [instruction,setInstruction]=useState(""); const [transport,setTransportValue]=useState<EditableTransportMode>("auto");
  const [busy,setBusy]=useState(false); const [error,setError]=useState<{title:string;message:string;details:string[]}|null>(null);
  const [scopeNotice,setScopeNotice]=useState<ReplanScopeNotice|null>(null);
  const examples=["想轻松一点，少走路","保留博物馆，把其他景点安排得紧凑一些","上午安排室内，下午去外滩","午饭想吃本帮菜","不想坐公交，尽量打车","晚上想留出两小时自由活动"];
  const readableDetails=(reason:ReplanApiError)=>Array.isArray(reason.details)?reason.details.flatMap(value=>{const item=value as {message?:unknown};return typeof item?.message==="string"?[item.message]:[];}):[];
  const preview=async()=>{setBusy(true);setError(null);try{const result=await previewDayReplan(tripId,day.id,instruction.trim());if("requires_scope_confirmation" in result)setScopeNotice(result);else onPreview(result);}catch(reason){setError(reason instanceof ReplanApiError?{title:reason.userTitle,message:reason.message,details:readableDetails(reason)}:{title:"暂时无法生成调整预览",message:"原行程没有变化，请稍后再试。",details:[]});}finally{setBusy(false);}};
  const saveTransport=async()=>{setBusy(true);setError(null);try{await setDayTransport(tripId,day.id,transport);await onSaved();}catch(reason){setError(reason instanceof ReplanApiError?{title:reason.userTitle,message:reason.message,details:readableDetails(reason)}:{title:"交通偏好保存失败",message:"原行程没有变化，请稍后再试。",details:[]});}finally{setBusy(false);}};
  if(scopeNotice)return <Modal open title={scopeNotice.user_title} onClose={onClose}><div className="replan-request"><p className="impact-issue"><Warning size={20}/>{scopeNotice.user_message}</p><p>预计影响：Day {scopeNotice.affected_day_ids.join("、Day ")}</p><div className="modal-actions"><Button variant="secondary" onClick={onClose}>取消</Button><Button onClick={()=>onPreview(scopeNotice.details.preview)}>{scopeNotice.scope==="whole_trip"?"预览整体调整":"预览跨天调整"}</Button></div></div></Modal>;
  return <Modal open title="告诉 Agent 这一天怎么调" onClose={onClose}><div className="replan-request"><Field label="调整意见" helper="Agent 只理解你的要求，地点核验、路线和时间仍由后端处理"><textarea autoFocus value={instruction} onChange={event=>setInstruction(event.target.value)} placeholder="例如：想轻松一点，少走路；保留博物馆，下午加武康大楼。"/></Field><div className="replan-examples"><span>快捷要求</span>{examples.map(value=><button type="button" key={value} onClick={()=>setInstruction(value)}>{value}</button>)}</div><Field label="当天交通偏好" helper="可单独保存，不调用 LLM"><select value={transport} onChange={event=>setTransportValue(event.target.value as EditableTransportMode)}><option value="auto">自动选择</option><option value="walking">步行为主</option><option value="public_transit">公共交通</option><option value="taxi">打车</option><option value="driving">驾车</option></select></Field>{error&&<div className="inline-warning"><Warning size={18}/><span><strong>{error.title}</strong><br/>{error.message}{error.details.length>0&&<ul>{error.details.map((detail,index)=><li key={`${detail}-${index}`}>{detail}</li>)}</ul>}</span></div>}<div className="modal-actions"><Button variant="secondary" disabled={busy} onClick={onClose}>取消</Button><Button variant="secondary" disabled={busy} onClick={()=>void saveTransport()}>仅保存交通偏好</Button><Button disabled={busy||!instruction.trim()} onClick={()=>void preview()}>{busy?"正在解析与排程…":"预览调整影响"}</Button></div></div></Modal>;
}

export function EditableTripReplanModal({tripId,anchorDay,onClose,onPreview,onDemoInstruction}:{tripId:string;anchorDay:TripDay;onClose:()=>void;onPreview:(value:EditablePreview)=>void;onDemoInstruction?:(instruction:string)=>void}) {
  const [instruction,setInstruction]=useState("");const [busy,setBusy]=useState(false);const [error,setError]=useState<{title:string;message:string;details:string[]}|null>(null);const [scopeNotice,setScopeNotice]=useState<ReplanScopeNotice|null>(null);
  const examples=["整趟行程轻松一点，少走路","上海这几天安排紧凑一些，苏州轻松一点","全程尽量少坐公交，多打车","多安排一些美食，减少博物馆","把自然景观集中到前几天","每天晚上尽量留一些自由时间"];
  const preview=async()=>{if(onDemoInstruction){onDemoInstruction(instruction.trim());return;}setBusy(true);setError(null);try{const result=await previewTripReplan(tripId,anchorDay.id,instruction.trim());if("requires_scope_confirmation" in result)setScopeNotice(result);else onPreview(result);}catch(reason){setError(reason instanceof ReplanApiError?{title:reason.userTitle,message:reason.message,details:Array.isArray(reason.details)?reason.details.flatMap(value=>{const item=value as {message?:unknown};return typeof item?.message==="string"?[item.message]:[]}):[]}:{title:"暂时无法生成调整预览",message:"原行程没有变化，请稍后再试。",details:[]});}finally{setBusy(false);}};
  if(scopeNotice)return <Modal open title={scopeNotice.user_title} onClose={onClose}><div className="replan-request"><p className="impact-issue"><Warning size={20}/>{scopeNotice.user_message}</p><p>预计影响：Day {scopeNotice.affected_day_ids.join("、Day ")}</p><div className="modal-actions"><Button variant="secondary" onClick={onClose}>取消</Button><Button onClick={()=>onPreview(scopeNotice.details.preview)}>{scopeNotice.scope==="whole_trip"?"预览整体调整":"预览跨天调整"}</Button></div></div></Modal>;
  return <Modal open title="告诉 Agent 这趟旅程怎么调" onClose={onClose}><div className="replan-request"><Field label="调整意见" helper="Agent 会先判断真正受影响的日期；未受影响日期保持不变"><textarea autoFocus value={instruction} onChange={event=>setInstruction(event.target.value)} placeholder="例如：整趟行程轻松一点，少走路；每天晚上尽量留一些自由时间。"/></Field><div className="replan-examples"><span>快捷要求</span>{examples.map(value=><button type="button" key={value} onClick={()=>setInstruction(value)}>{value}</button>)}</div>{error&&<div className="inline-warning"><Warning size={18}/><span><strong>{error.title}</strong><br/>{error.message}{error.details.length>0&&<ul>{error.details.map((detail,index)=><li key={`${detail}-${index}`}>{detail}</li>)}</ul>}</span></div>}<div className="modal-actions"><Button variant="secondary" disabled={busy} onClick={onClose}>取消</Button><Button disabled={busy||!instruction.trim()} onClick={()=>void preview()}>{busy?"正在分析影响范围…":"预览调整影响"}</Button></div></div></Modal>;
}
