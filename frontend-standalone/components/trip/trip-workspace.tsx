"use client";
import { confirmReplanPreview, deleteTripStop, editTripStop, getReplanOptions, loadTrip, legacyFetch, replanTrip, setSegmentTransport, undoDayReplan } from "@/lib/api";
import type { EditablePreview, LocalReplanRequest, ReplanOptions } from "@/lib/api";

import { Fragment, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { closestCenter, DndContext, DragEndEvent, KeyboardSensor, PointerSensor, useDroppable, useSensor, useSensors } from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { ArrowCounterClockwise, Bed, CalendarDots, CaretDown, Check, CloudRain, DownloadSimple, Lock, MagnifyingGlass, MapPin, Plus, ShareNetwork, SlidersHorizontal, Sparkle, Train, Warning, X } from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import clsx from "clsx";
import { createMockTrip } from "@/mocks/trip";
import { LocalGuideWidgets, SightDetail } from "@/components/guide/local-guide";
import { ActivityCard } from "./activity-card";
import { EditableAddModal, EditableDayReplanModal, EditableStopModal, EditableTripReplanModal } from "./editable-replan-modals";
import type { DemoAddDraft } from "./editable-replan-modals";
import { RouteSegmentCard } from "./route-segment-card";
import { TripMap } from "./trip-map";
import { AppHeader, PaperPage } from "@/components/app-chrome";
import { Button, Field, Modal, Segmented, Ticket } from "@/components/ui";
import { TransportModeIcon } from "@/components/transport-mode-icon";
import { getAllocatedBudget, getEstimatedTripCost, getTripSpendByCategory, useTripStore } from "@/store/trip-store";
import type { Activity, Budget, TransportSegment, Trip, TripDay } from "@/types/trip";
import { weatherImpactNote } from "@/features/weather/weather-impact";
import type { HotelChangeImpact, HotelOption, ManualChangeImpact } from "@/types/ui-api";

type ImpactState = { title: string; issue: string; affected: string[]; preserved: string[]; direct?: () => void | Promise<void>; reflow: () => void | Promise<void> };
type EditorState = { mode: "add"; dayId: string } | { mode: "edit"; item: Activity } | null;
type ReplanRequest = { scope: "day" | "trip"; opinion: string };
type ReplacementTarget = { day: number; item: Activity };
type ManualMutation =
  | { type:"reorder"; dayId:string; activityId:string; overActivityId:string }
  | { type:"move"; activityId:string; targetDayId:string; targetIndex?:number }
  | { type:"add"; targetDayId:string; activity:Omit<Activity,"id"|"dayId"> }
  | { type:"edit"; activityId:string; targetDayId:string; patch:Partial<Omit<Activity,"id"|"dayId">> }
  | { type:"delete"; activityId:string }
  | { type:"set_lock"; activityId:string; locked:boolean }
  | { type:"update_budget"; budget:Budget };

function DayDropTab({ id, active, children, onClick }: { id: string; active: boolean; children: React.ReactNode; onClick: () => void }) {
  const { setNodeRef, isOver } = useDroppable({ id: `drop-${id}`, data: { dayId: id } });
  return <button ref={setNodeRef} type="button" aria-pressed={active} className={clsx("day-tab", active && "active", isOver && "drop-target")} onClick={onClick}>{children}</button>;
}

export function TripWorkspace({ tripId }: { tripId: string }) {
  const reduce = useReducedMotion();
  const hydrated = useTripStore((state) => state.hydrated);
  const setHydrated = useTripStore((state) => state.setHydrated);
  const trip = useTripStore((state) => state.trips.find((item) => item.id === tripId));
  const activeDayId = useTripStore((state) => state.activeDayId);
  const selectedActivityId = useTripStore((state) => state.selectedActivityId);
  const setCurrentTrip = useTripStore((state) => state.setCurrentTrip);
  const syncTrip = useTripStore((state) => state.syncTrip);
  const setActiveDay = useTripStore((state) => state.setActiveDay);
  const selectActivity = useTripStore((state) => state.selectActivity);
  const applyHotelChange = useTripStore((state) => state.applyHotelChange);
  const applyManualChange = useTripStore((state) => state.applyManualChange);
  const applyAgentReplan = useTripStore((state) => state.applyAgentReplan);
  const toast = useTripStore((state) => state.toast);
  const setToast = useTripStore((state) => state.setToast);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(tripId !== "demo-trip");
  useEffect(() => {
    if(tripId === "demo-trip") return;
    const controller=new AbortController(); setLoading(true); setLoadError("");
    loadTrip(tripId,controller.signal).then(syncTrip).catch(e=>{if(!controller.signal.aborted)setLoadError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[tripId,syncTrip]);
  const [modal, setModalRaw] = useState<"budget" | "transport" | "hotel" | "share" | null>(null);
  const setModal = (value: "budget"|"transport"|"hotel"|"share"|null) => { if(tripId !== "demo-trip" && value) {setToast("后续开放：本轮支持生成与阅读");return;} setModalRaw(value); };
  const [detail, setDetail] = useState<Activity | null>(null);
  const [editor, setEditorRaw] = useState<EditorState>(null);
  const setEditor=(value:EditorState)=>{if(tripId!=="demo-trip"&&value){setToast("后续开放：活动编辑");return;}setEditorRaw(value);};
  const [impact, setImpact] = useState<ImpactState | null>(null);
  const [replanRequest, setReplanRaw] = useState<ReplanRequest | null>(null);
  const setReplanRequest=(value:ReplanRequest|null)=>{if(tripId!=="demo-trip"&&value){setToast("后续开放：重新规划");return;}setReplanRaw(value);};
  const [replacementTarget,setReplacementTarget]=useState<ReplacementTarget|null>(null);
  const [editableAddDay,setEditableAddDay]=useState<TripDay|null>(null);
  const [editableStop,setEditableStop]=useState<{day:TripDay;item:Activity}|null>(null);
  const [editableDayReplan,setEditableDayReplan]=useState<TripDay|null>(null);
  const [settingsOpen,setSettingsOpen]=useState(false);
  const [sheet, setSheet] = useState<"collapsed" | "half" | "expanded">("half");
  const [manualAdjusting, setManualAdjusting] = useState(false);
  const [serverReady, setServerReady] = useState(false);
  const bootstrappedTripId = useRef<string | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 7 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));

  useEffect(() => { setHydrated(true); setCurrentTrip(tripId); }, [setCurrentTrip, setHydrated, tripId]);
  useEffect(() => {
    if (hydrated && !trip && tripId === "demo-trip") syncTrip(createMockTrip());
  }, [hydrated, trip, tripId, syncTrip]);
  useEffect(() => {
    if (tripId !== "demo-trip" || !hydrated || !trip || bootstrappedTripId.current === trip.id) return;
    bootstrappedTripId.current = trip.id;
    setServerReady(false);
    void legacyFetch("/api/trips/state", { method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ trip }) })
      .then(async (response) => {
        const payload = await response.json() as { trip?:Trip; message?:string };
        if (!response.ok || !payload.trip) throw new Error(payload.message || "行程同步失败");
        syncTrip(payload.trip);
        setServerReady(true);
      })
      .catch((error) => { bootstrappedTripId.current = null; setToast(error instanceof Error ? error.message : "行程同步失败，请刷新后重试"); });
  }, [hydrated, setToast, syncTrip, trip]);
  useEffect(() => {
    if (!selectedActivityId) return;
    document.querySelector(`[data-activity-id="${selectedActivityId}"]`)?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "nearest" });
  }, [reduce, selectedActivityId]);
  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(id);
  }, [setToast, toast]);

  const activeDay = trip?.days.find((day) => day.id === activeDayId);
  const selectedItem = trip?.days.flatMap((day) => day.activities).find((item) => item.id === selectedActivityId);
  const showDay = (dayId: string | "overview") => {
    setActiveDay(dayId);
    selectActivity(null);
  };
  const planned = trip ? getEstimatedTripCost(trip) : 0;
  const overBudget = trip ? planned > trip.budget.total : false;

  const recalculateManual = async (mutation: ManualMutation) => {
    if(tripId!=="demo-trip"){setToast("后续开放：行程编辑");return false;}
    if (manualAdjusting) return false;
    if (!serverReady) { setToast("行程仍在同步，请稍后再试"); return false; }
    setManualAdjusting(true);
    try {
      const response = await legacyFetch("/api/trips/recalculate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tripId:trip!.id, expectedRevision:trip!.revision ?? 1, mutation }) });
      const payload = await response.json() as { trip?: Trip; impact?: ManualChangeImpact; message?: string };
      if (!response.ok || !payload.trip || !payload.impact) throw new Error(payload.message || "路线与时间重新计算失败");
      const detail = [payload.impact.verifiedPlaces.length ? `已核验 ${payload.impact.verifiedPlaces.length} 个地点` : "", payload.impact.updatedRoutes.length ? `更新 ${payload.impact.updatedRoutes.length} 段交通` : "", payload.impact.shiftedActivities.length ? `顺延 ${payload.impact.shiftedActivities.length} 项活动` : ""].filter(Boolean).join("，");
      applyManualChange(payload.trip, detail || "行程修改已保存并通过校验");
      return true;
    } catch (error) {
      setToast(error instanceof Error ? error.message : "行程修改失败，原行程未改变");
      return false;
    } finally {
      setManualAdjusting(false);
    }
  };

  const commitRemoteReplan=async(mutation:LocalReplanRequest,message:string,focusDay?:number)=>{
    if(tripId==="demo-trip"||manualAdjusting)return false;
    setManualAdjusting(true);
    try{
      const key=typeof crypto!=="undefined"&&"randomUUID" in crypto?crypto.randomUUID():`${Date.now()}-${Math.random()}`;
      await replanTrip(tripId,mutation,key);
      const refreshed=await loadTrip(tripId);
      applyAgentReplan(refreshed);
      selectActivity(null);
      if(focusDay)setActiveDay(refreshed.days[focusDay-1]?.id??"overview");
      setToast(message);
      return true;
    }catch(error){
      setToast(error instanceof Error?error.message:"重规划失败，原行程未改变");
      return false;
    }finally{setManualAdjusting(false);}
  };

  const refreshRemote=async(message:string,focusDay?:number)=>{
    const refreshed=await loadTrip(tripId);applyAgentReplan(refreshed);selectActivity(null);
    if(focusDay)setActiveDay(refreshed.days[focusDay-1]?.id??"overview");setToast(message);
  };

  const showEditablePreview=(preview:EditablePreview,title:string)=>{
    const diff=preview.diff;
    const prefix=(item:{day?:number})=>item.day?`Day ${item.day} · `:"";
    const affected=[...diff.added.map(item=>`${prefix(item)}新增：${item.name}${item.time?`（${item.time}）`:""}`),...diff.removed.map(item=>`${prefix(item)}移出：${item.name}`),...diff.moved.map(item=>`${prefix(item)}调整时间：${item.name} ${item.before} → ${item.after}`),...diff.transport_changed.map(item=>`${prefix(item)}调整交通：${item.name}`)];
    for(const action of preview.parsed_constraints?.actions??[]){if(action.type==='set_pace'&&action.pace)affected.push(`节奏调整：${action.pace}`);if(action.type==='set_transport'&&action.mode)affected.push(`交通偏好：${action.mode}`);}
    if(preview.unplaced_place_ids?.length)affected.push(`${preview.unplaced_place_ids.length} 个地点暂无合适日期，将保留为备选`);
    for(const warning of preview.warnings??[])affected.push(`需复核：${warning.message}`);
    const issue=preview.scope==='whole_trip'?'这项调整将重新规划整趟旅行。确认前原行程不会改变。':preview.scope==='affected_days'?`这项调整会影响其他日期（Day ${preview.affected_day_ids.join('、Day ')}）。确认前原行程不会改变。`:`这是 Day ${preview.day} 的拟议日程，确认后才会保存。`;
    const unchanged=trip?.days.filter(day=>!preview.affected_day_ids.includes(day.dayNumber)).map(day=>`Day ${day.dayNumber} · ${day.city} 保持不变`)??[];
    const preserved=preview.scope==='whole_trip'?['已锁定地点','必须保留的地点','用户创建的受保护内容',...unchanged]:['已通过的研究资料','未受影响的路线',...unchanged];
    setEditableAddDay(null);setEditableDayReplan(null);
    setImpact({title:preview.scope==='affected_days'?'预览跨天调整':preview.scope==='whole_trip'?'预览全局调整':title,issue,affected:affected.length?affected:["所列日期的顺序与时间重新计算"],preserved,reflow:async()=>{await confirmReplanPreview(tripId,preview.preview_id);await refreshRemote(`Day ${preview.affected_day_ids.join('、')} 的修改已保存`,preview.day);}});
  };

  const previewDemoAdd=(draft:DemoAddDraft)=>{
    if(!editableAddDay)return;
    const day=editableAddDay;
    const start=draft.preferredStartTime??"18:00";const [hour,minute]=start.split(':').map(Number);const endMinute=hour*60+minute+draft.stayMinutes;const end=`${String(Math.floor(endMinute/60)%24).padStart(2,'0')}:${String(endMinute%60).padStart(2,'0')}`;
    setEditableAddDay(null);
    setImpact({title:"预览加入当天日程",issue:`Day ${day.dayNumber} 将新增“${draft.title}”${draft.preferredStartTime?`，希望 ${draft.preferredStartTime} 开始`:""}。确认前原行程不会改变。`,affected:[`新增：${draft.title}`,`停留 ${draft.stayMinutes} 分钟`,"当天时间与相邻交通将重新计算"],preserved:["当天已有安排默认保留","其他日期","示例行程原始数据"],reflow:async()=>{await recalculateManual({type:"add",targetDayId:day.id,activity:{title:draft.title,city:day.city,kind:"attraction",start,end,duration:`建议 ${draft.stayMinutes} 分钟`,cost:0,costStatus:"unknown",reason:draft.note||"用户新增安排",coord:[0,0],coordinatesUnknown:true,locked:false,stayMinutes:draft.stayMinutes,preferredStartTime:draft.preferredStartTime,hardStartTime:null,priority:3,transportToNext:null,mealRole:null,mealWindow:null,mealTimingReason:""}});}});
  };

  const previewDemoTripInstruction=(instruction:string)=>{
    setSettingsOpen(false);
    setImpact({title:"预览全程调整",issue:`Agent 将按这项意见分析整趟旅程：“${instruction}”。示例模式会保留原始地点资料，确认前不会修改行程。`,affected:["真正需要变化的日期","相关活动时间与交通方式","旅行节奏与每日密度"],preserved:["已锁定地点","用户手动添加内容","无需变化的日期"],reflow:async()=>{setToast("示例行程已演示全程 Preview；真实行程会在确认后提交统一 Replan 流程");}});
  };

  const requestRemoteRemove=async(item:Activity)=>{
    const day=trip?.days.find(value=>value.activities.some(activity=>activity.id===item.id));
    if(!day||!item.placeId)return setToast("该活动没有可调整的地点引用");
    try{
      const options=await getReplanOptions(tripId,day.dayNumber);
      const stop=options.stops.find(value=>value.place_id===item.placeId);
      if(!stop?.removable)return setToast("主景点和固定用餐需要保留，可使用“重排这一天”调整其他地点");
      setImpact({title:"确认删除这个地点",issue:`将从 Day ${day.dayNumber} 删除“${item.title}”，并重新计算当天路线与时间。`,affected:["当前地点与相邻交通段","当天活动时间与密度","最终行程档案"],preserved:["其他日期","地点研究资料","主景点与固定用餐"],reflow:async()=>{await deleteTripStop(tripId,day.id,item.placeId!);await refreshRemote(`已删除“${item.title}”并重排 Day ${day.dayNumber}`,day.dayNumber);}});
    }catch(error){setToast(error instanceof Error?error.message:"暂时无法读取可调整范围");}
  };

  const requestRemoteDayReplan=()=>{
    const day=activeDay??trip?.days[0]; if(!day)return;
    setEditableDayReplan(day);
  };

  const toggleRemoteLock=async(item:Activity,day:TripDay)=>{
    if(!item.placeId)return setToast("该活动没有可编辑的地点引用");
    if(manualAdjusting)return;setManualAdjusting(true);
    try{await editTripStop(tripId,day.id,item.placeId,{locked:!item.locked});await refreshRemote(item.locked?"活动已解锁":"活动已锁定",day.dayNumber);}
    catch(error){setToast(error instanceof Error?error.message:"锁定状态保存失败");}
    finally{setManualAdjusting(false);}
  };

  const changeSegmentTransport=async(day:TripDay,origin:string,destination:string,mode:import("@/lib/api").EditableTransportMode)=>{
    if(tripId==="demo-trip")return setToast("示例行程暂不保存交通调整");
    if(manualAdjusting)return;setManualAdjusting(true);
    try{await setSegmentTransport(tripId,day.id,origin,destination,mode);await refreshRemote(`已重新计算 ${origin} → ${destination} 的路线`,day.dayNumber);}
    catch(error){setToast(error instanceof Error?error.message:"交通方式保存失败");}
    finally{setManualAdjusting(false);}
  };

  const onDragEnd = (event: DragEndEvent) => {
    const activeId = String(event.active.id);
    const overId = event.over ? String(event.over.id) : "";
    if (!overId || activeId === overId || !trip || manualAdjusting) return;
    const sourceDay = trip.days.find((day) => day.activities.some((item) => item.id === activeId));
    if (!sourceDay) return;
    if (overId.startsWith("drop-")) {
      const targetDayId = overId.replace("drop-", "");
      if (targetDayId === sourceDay.id) return;
      if (!trip.days.some((day) => day.id === targetDayId)) return;
      void recalculateManual({type:"move",activityId:activeId,targetDayId}).then((saved) => { if (saved) setActiveDay(targetDayId); });
      return;
    }
    const from = sourceDay.activities.findIndex((item) => item.id === activeId);
    const to = sourceDay.activities.findIndex((item) => item.id === overId);
    if (from < 0 || to < 0) return;
    void recalculateManual({type:"reorder",dayId:sourceDay.id,activityId:activeId,overActivityId:overId});
  };

  const undoServerChange=async()=>{
    if(tripId!=="demo-trip"){
      const day=activeDay??trip?.days[0];if(!day)return setToast("请先选择要撤销的日期");
      setManualAdjusting(true);try{await undoDayReplan(tripId,day.id);await refreshRemote(`已撤销 Day ${day.dayNumber} 最近一次修改`,day.dayNumber);}catch(error){setToast(error instanceof Error?error.message:"撤销失败，请稍后重试");}finally{setManualAdjusting(false);}return;
    }
    if(!serverReady){setToast("行程仍在同步，请稍后再试");return;}
    try{
      const response=await legacyFetch("/api/trips/undo",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({tripId:trip!.id,expectedRevision:trip!.revision??1})});
      const payload=await response.json() as {trip?:Trip;message?:string};
      if(!response.ok||!payload.trip)throw new Error(payload.message||"撤销失败");
      applyManualChange(payload.trip,"已恢复到上一次修改前的行程");
    }catch(error){setToast(error instanceof Error?error.message:"撤销失败，请稍后重试");}
  };

  if (loading) return <><AppHeader/><PaperPage><div className="workspace-skeleton"><span/><span/><span/></div></PaperPage></>;
  if (loadError) return <><AppHeader/><PaperPage><div className="prep-wrap"><p role="alert">{loadError}</p><Button onClick={()=>window.location.reload()}>重试</Button></div></PaperPage></>;
  if (hydrated && !trip && tripId !== "demo-trip") return <><AppHeader /><PaperPage><div className="prep-wrap"><h1 className="display-title">没有找到这份行程</h1><p>这份行程可能已被删除，或保存在另一个浏览器中。</p><Link href="/trips" className="button button-secondary">查看我的行程</Link><Link href="/create" className="button button-primary">创建新旅程</Link></div></PaperPage></>;
  if (!hydrated || !trip) return <><AppHeader /><PaperPage><div className="workspace-skeleton"><span /><span /><span /></div></PaperPage></>;

  return <>
    <AppHeader />
    <PaperPage className="trip-page">
      <DndContext sensors={sensors} collisionDetection={closestCenter} autoScroll={false} onDragEnd={onDragEnd}>
        <header className="trip-heading">
          <div><p>{trip.startDate} 至 {trip.endDate}</p><h1>{trip.destinations.join(" · ")}</h1><div className="trip-facts"><span>{trip.travelers.adults} 成人</span><span>{trip.travelers.children} 儿童</span><span>{trip.travelers.seniors} 老人</span><span>预算 ￥{trip.budget.total.toLocaleString()}</span></div></div>
          <div className="trip-heading-actions"><Button variant="ghost" onClick={()=>void undoServerChange()}><ArrowCounterClockwise size={18} />撤销</Button><Button variant="secondary" onClick={() => setModal("share")}><ShareNetwork size={18} />分享</Button><Link href={`/trip/${trip.id}/export`} onClick={e=>{if(trip.profile){e.preventDefault();setToast("后续开放：导出行程");}}}><Button><DownloadSimple size={18} />导出</Button></Link></div>
        </header>

        <nav className="day-tabs" aria-label="按天查看行程">
          <button type="button" aria-pressed={activeDayId === "overview"} className={clsx("day-tab", activeDayId === "overview" && "active")} onClick={() => showDay("overview")}>总览</button>
          {trip.days.map((day) => <DayDropTab key={day.id} id={day.id} active={activeDayId === day.id} onClick={() => showDay(day.id)}><span style={{ background: day.routeColor }} />Day {day.dayNumber}<small>{day.city}</small></DayDropTab>)}
        </nav>

        <section className="workspace-grid">
          <div className={clsx("itinerary-panel", `sheet-${sheet}`)}>
            <div className="sheet-grab"><button aria-label="切换行程面板高度" onClick={() => setSheet((value) => value === "collapsed" ? "half" : value === "half" ? "expanded" : "collapsed")}><span /><CaretDown size={17} /></button></div>
            {trip.dataWarnings?.length ? <details className="trip-data-warnings"><summary><Warning size={16}/>有 {trip.dataWarnings.length} 项实时数据提醒</summary><ul>{trip.dataWarnings.map((warning,index)=><li key={`${warning}-${index}`}>{warning}</li>)}</ul></details> : null}
            {activeDayId === "overview" ? <OverviewPanel trip={trip} onDay={showDay} /> : activeDay && <>
              <div className="day-heading"><div><p>Day {activeDay.dayNumber}，{trip.profile ? activeDay.weather.label : `${activeDay.weather.label} ${activeDay.weather.low} 至 ${activeDay.weather.high}℃`}</p><h2>{activeDay.city}</h2></div><Button variant="secondary" onClick={() => setEditableAddDay(activeDay)}><Plus size={17} />添加地点/活动</Button></div>
              <div className="weather-note"><CloudRain size={19} /><span><b>天气影响</b>{trip.profile ? "尚未查询实时天气，出发前请复核；雨天按当天开放情况调整户外活动。" : weatherImpactNote(activeDay.weather)}</span></div>
              {manualAdjusting && <div className="manual-change-status" role="status"><span />正在重新计算地点、交通与时间，原行程会保留到校验通过。</div>}
              <SortableContext items={activeDay.activities.map((item) => item.id)} strategy={verticalListSortingStrategy}>
                <div className="activity-list">{activeDay.activities.map((item,index) => <Fragment key={item.id}><ActivityCard item={item} selected={item.id === selectedActivityId} onSelect={() => selectActivity(item.id)} onEdit={() => tripId==="demo-trip"?setEditor({ mode: "edit", item }):setEditableStop({day:activeDay,item})} onRemove={() => { if (item.locked) { setToast("请先解锁该活动"); return; } if(tripId==="demo-trip")void recalculateManual({type:"delete",activityId:item.id});else void requestRemoteRemove(item); }} onToggleLock={() => tripId==="demo-trip"?void recalculateManual({type:"set_lock",activityId:item.id,locked:!item.locked}):void toggleRemoteLock(item,activeDay)} />{activeDay.segments?.[index]&&<RouteSegmentCard segment={activeDay.segments[index]} disabled={manualAdjusting} onChange={mode=>void changeSegmentTransport(activeDay,activeDay.segments![index].fromPlaceId,activeDay.segments![index].toPlaceId,mode)}/>}</Fragment>)}</div>
              </SortableContext>
              <div className="drag-help">使用“编辑”设置地点时间、停留时长与锁定；点击两站之间的交通段可单独修改方式。</div>
            </>}

            <div className="workspace-actions">
              <button onClick={() => setModal("budget")}><SlidersHorizontal size={20} /><span><b>预计花费</b>{trip.profile ? "费用待复核" : `￥${planned.toLocaleString()}`} / ￥{trip.budget.total.toLocaleString()}</span>{overBudget && <Warning size={18} className="warning" />}</button>
              <Link href={`/trip/${trip.id}/preparation`}><CalendarDots size={20} /><span><b>行前准备</b>按天气与同行人生成</span></Link>
              <button onClick={() => setModal("transport")}><Train size={20} /><span><b>调整交通边界</b>抵达、跨城与离开时间</span></button>
              <button onClick={() => setModal("hotel")}><Bed size={20} /><span><b>更换酒店</b>位置会影响每日路线</span></button>
            </div>

            <LocalGuideWidgets trip={trip} />
            <div className="replan-zone"><div><Sparkle size={22} weight="duotone" /><span><b>想调整这趟旅行？</b>确认影响范围后，只重新计算需要变化的部分。</span></div><div><Button variant="secondary" disabled={manualAdjusting} onClick={() => tripId==="demo-trip"?setReplanRequest({ scope:"day", opinion:"" }):requestRemoteDayReplan()}>重排这一天</Button><Button variant="ghost" disabled={manualAdjusting} onClick={() => setSettingsOpen(true)}>调整全程</Button></div></div>
          </div>

          <div className="map-panel"><TripMap trip={trip} activeDayId={activeDayId} selectedActivityId={selectedActivityId} onSelect={selectActivity} />{selectedItem && <AnimatePresence><motion.div className="selected-poi" initial={reduce ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}><button onClick={() => selectActivity(null)} aria-label="关闭地点卡"><X size={16} /></button><span>{selectedItem.start}，{selectedItem.duration}</span><strong>{selectedItem.title}</strong><small><TransportModeIcon mode={selectedItem.transport?.mode}/>{trip.profile ? (()=>{const url=trip.profile.places.find(p=>p.id===selectedItem.placeId)?.map_url;return url?<a target="_blank" rel="noopener noreferrer" href={url}>在地图查看 · 交通与费用出发前请复核 ↗</a>:<span>体验尚未关联可核验地点</span>;})() : `${selectedItem.transport?.mode} ${selectedItem.transport?.durationMinutes==null?"交通时间待计算":`${selectedItem.transport.durationMinutes} 分钟`}，￥${selectedItem.cost}`}</small>{selectedItem.kind==="attraction"?<button className="map-detail-button" onClick={()=>setDetail(selectedItem)}>查看地点详情</button>:null}</motion.div></AnimatePresence>}</div>
        </section>
      </DndContext>
    </PaperPage>

    {detail && <SightDetail item={detail} trip={trip} onClose={() => setDetail(null)} />}
    {modal === "budget" && <BudgetModal open budget={trip.budget} spending={getTripSpendByCategory(trip)} onClose={() => setModal(null)} onSave={(next) => { setModal(null); setImpact({ title: "预算变动需要处理", issue: "新的分类预算会影响住宿与餐饮推荐。", affected: ["超出分类的推荐", "相关活动费用", "剩余可用金额"], preserved: ["已锁定活动", "日期与城市顺序", "未受影响日期"], direct: async() => {await recalculateManual({type:"update_budget",budget:next});}, reflow: async() => {await recalculateManual({type:"update_budget",budget:next});setToast("预算已保存；需要 Agent 重排时请使用下方调整入口");} }); }} />}
    {modal === "transport" && <TransportModal open segments={trip.transportSegments} onClose={() => setModal(null)} onSave={async (segmentId, mode, scheduleId, customTime) => {
      if(!serverReady)throw new Error("行程仍在同步，请稍后再试");
      const command={tripId:trip.id,expectedRevision:trip.revision??1,segmentId,mode,scheduleId,customTime};
      const response=await legacyFetch("/api/trips/transport-impact",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...command,commit:false})});
      const payload=await response.json() as {trip?:Trip;impact?:ManualChangeImpact&{before:string;after:string;segment:string};message?:string};
      if(!response.ok||!payload.trip||!payload.impact)throw new Error(payload.message||"交通影响分析失败");
      const analysis=payload.impact;
      setModal(null);
      setImpact({title:"确认交通时间调整",issue:`${analysis.before} → ${analysis.after}。已重新检查相邻地点的真实交通时间。`,affected:[analysis.segment,...analysis.updatedRoutes,...analysis.shiftedActivities.map((item)=>`顺延：${item}`),...analysis.warnings],preserved:["锁定活动","其他日期","住宿与预算偏好"],reflow:async()=>{const saved=await legacyFetch("/api/trips/transport-impact",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...command,commit:true})});const result=await saved.json() as {trip?:Trip;message?:string};if(!saved.ok||!result.trip)throw new Error(result.message||"交通调整保存失败");applyManualChange(result.trip,"交通约束已应用，相邻路线和时间已经重算");}});
    }} />}
    {modal === "hotel" && <HotelModal open trip={trip} initialCity={activeDay?.city ?? trip.mainDestination} onClose={() => setModal(null)} onSelect={async (city, hotel) => {
      if(!serverReady)throw new Error("行程仍在同步，请稍后再试");
      const command={tripId:trip.id,expectedRevision:trip.revision??1,city,hotel};
      const response=await legacyFetch("/api/trips/hotel-impact",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...command,commit:false})});
      const payload=await response.json() as {trip?:Trip;impact?:HotelChangeImpact;message?:string};
      if(!response.ok||!payload.trip||!payload.impact) throw new Error(payload.message||"酒店影响分析失败");
      const analysis=payload.impact;
      const routeLines=analysis.routeChanges.map((item)=>`${item.dayLabel}：${item.leg} ${item.beforeMinutes} → ${item.afterMinutes} 分钟${item.deltaMinutes===0?"":item.deltaMinutes>0?`（增加 ${item.deltaMinutes} 分钟）`:`（减少 ${Math.abs(item.deltaMinutes)} 分钟）`}`);
      const budgetLine=analysis.budgetDelta===0?`住宿预算不变（${analysis.nights} 晚）`:analysis.budgetDelta>0?`住宿预算增加 ￥${analysis.budgetDelta.toLocaleString()}`:`住宿预算减少 ￥${Math.abs(analysis.budgetDelta).toLocaleString()}`;
      setModal(null);
      setImpact({ title:`确认更换${city}酒店`, issue:`${analysis.previousHotel} → ${analysis.nextHotel}。系统已用高德重新计算与酒店相关的交通时间。`, affected:[...routeLines,...analysis.addedActivities.map((item)=>`新增节点：${item}`),...analysis.shiftedActivities.map((item)=>`顺延活动：${item}`),budgetLine], preserved:["已锁定的非酒店活动", "城际交通班次", "其他城市的酒店与行程"], reflow:async()=>{const saved=await legacyFetch("/api/trips/hotel-impact",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...command,commit:true})});const result=await saved.json() as {trip?:Trip;message?:string};if(!saved.ok||!result.trip)throw new Error(result.message||"酒店更换保存失败");applyHotelChange(result.trip);} });
    }} />}
    {editor && <ActivityEditor state={editor} days={trip.days.map((day) => ({ id: day.id, label: `Day ${day.dayNumber} ${day.city}`, city:day.city }))} onClose={() => setEditor(null)} onSave={async (data, targetDayId) => {
      if(editor.mode==="edit"){
        const saved=await recalculateManual({type:"edit",activityId:editor.item.id,targetDayId,patch:data});
        if(saved)setEditor(null);
        return saved;
      }else{
        const saved=await recalculateManual({type:"add",targetDayId,activity:data as Omit<Activity,"id"|"dayId">});
        if(saved)setEditor(null);
        return saved;
      }
    }} />}
    {editableAddDay&&<EditableAddModal tripId={trip.id} day={editableAddDay} onClose={()=>setEditableAddDay(null)} onPreview={value=>showEditablePreview(value,"确认加入当天日程")} onDemoPreview={tripId==="demo-trip"?previewDemoAdd:undefined}/>} 
    {editableStop&&<EditableStopModal tripId={trip.id} day={editableStop.day} item={editableStop.item} onClose={()=>setEditableStop(null)} onSaved={async()=>{const day=editableStop.day;setEditableStop(null);await refreshRemote("活动设置已保存并重新排程",day.dayNumber);}} onReplace={()=>{const value=editableStop;setEditableStop(null);setReplacementTarget({day:value.day.dayNumber,item:value.item});}}/>}
    {editableDayReplan&&<EditableDayReplanModal tripId={trip.id} day={editableDayReplan} onClose={()=>setEditableDayReplan(null)} onPreview={value=>showEditablePreview(value,"确认重新规划这一天")} onSaved={async()=>{const day=editableDayReplan;setEditableDayReplan(null);await refreshRemote("当天交通偏好已保存并重新排程",day.dayNumber);}}/>}
    {modal === "share" && <ShareModal open tripId={trip.id} shareId={trip.shareId} shared={trip.shared} onClose={() => setModal(null)} />}
    {replacementTarget&&<ReplacementModal tripId={trip.id} target={replacementTarget} onClose={()=>setReplacementTarget(null)} onChoose={(candidate)=>{const target=replacementTarget;setReplacementTarget(null);setImpact({title:"确认替换地点",issue:`Day ${target.day}：${target.item.title} → ${candidate.display_name}。系统将重新计算当天路线与时间。`,affected:["被替换的地点","当天路线、顺序与时间","最终行程档案"],preserved:["其他日期","主景点与固定用餐","地点研究资料"],reflow:async()=>{await commitRemoteReplan({action:"replace_stop",day:target.day,place_id:target.item.placeId!,replacement_place_id:candidate.place_id},`已将“${target.item.title}”替换为“${candidate.display_name}”`,target.day);}});}}/>}
    {settingsOpen&&<EditableTripReplanModal tripId={trip.id} anchorDay={activeDay??trip.days[0]} onClose={()=>setSettingsOpen(false)} onPreview={value=>showEditablePreview(value,"预览全程调整")} onDemoInstruction={tripId==="demo-trip"?previewDemoTripInstruction:undefined}/>} 
    {replanRequest && <ReplanRequestModal value={replanRequest} onClose={() => setReplanRequest(null)} onSubmit={(opinion) => { const scope=replanRequest.scope; const dayId=scope === "day" ? activeDay?.id ?? trip.days[0].id : undefined; setReplanRequest(null); setImpact({ title: scope === "day" ? "重新规划这一天" : "重新规划全程", issue: `Agent 将依据你的调整意见重新评估${scope === "day" ? "当天" : "全部日期"}：${opinion}`, affected: scope === "day" ? ["当前日期的未锁定活动", "活动之间的交通方式与时间", "当日预计费用"] : ["全部未锁定活动", "活动顺序与市内路线", "分类预算使用情况"], preserved: scope === "day" ? ["全部锁定活动", "交通与酒店节点", "其他日期"] : ["全部锁定活动", "交通与酒店节点", "日期、城市与人员约束"], reflow: async () => { if(!serverReady)throw new Error("行程仍在同步，请稍后再试");const response=await legacyFetch("/api/trips/replan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({tripId:trip.id,expectedRevision:trip.revision??1,scope,dayId,instruction:opinion})}); const payload=await response.json() as {trip?:Trip;message?:string}; if(!response.ok||!payload.trip) throw new Error(payload.message||"重规划失败，请稍后重试"); applyAgentReplan(payload.trip); } }); }} />}
    {impact && <ImpactModal value={impact} onClose={() => setImpact(null)} onDone={() => setImpact(null)} />}
    <AnimatePresence>{toast && <motion.div role="status" className="toast" initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 10 }}><Check size={18} />{toast}</motion.div>}</AnimatePresence>
  </>;
}

function OverviewPanel({ trip, onDay }: { trip: NonNullable<ReturnType<typeof useTripStore.getState>["trips"]>[number]; onDay: (id: string) => void }) {
  const cities=[...new Set(trip.days.map((day)=>day.city))];
  const pace={relaxed:"轻松",balanced:"均衡",intensive:"紧凑"}[trip.preferences.pace];
  return <div className="overview-panel"><div className="day-heading"><div><p>路线总览</p><h2>{trip.days.length} 天，{cities.length} 座城市</h2></div><span className="stamp">{trip.status==="completed"?"已规划":"草稿"}</span></div><p className="overview-intro">从{trip.origin}出发，依次游览{trip.destinations.join("、")}。全程采用{pace}节奏，{trip.preferences.longDistance==="flight"?"城市间优先飞机":trip.preferences.longDistance==="train"?"城市间优先普通列车":"城市间优先高铁或 Agent 推荐方案"}。</p><div className="overview-days">{trip.days.map((day) => <button key={day.id} onClick={() => onDay(day.id)}><span className="overview-number" style={{ borderColor: day.routeColor, color: day.routeColor }}>{day.dayNumber}</span><span><b>{day.city}</b><small>{day.activities.slice(0,2).map((item) => item.title).join("、")}</small></span><strong>{trip.profile ? "待复核" : `￥${day.activities.reduce((sum,item) => sum + item.cost,0).toLocaleString()}`}</strong></button>)}</div><Ticket className="overview-note"><MapPin size={21} /><div><b>主要目的地：{trip.mainDestination}</b><span>{trip.profile ? "时间为建议安排；交通与住宿请自行确认" : "住宿位置已参与每日首尾路线计算"}</span>{trip.profile&&<details className="guide-fold"><summary>备选景点 · {trip.profile.module_groups.sights.optional.length}</summary>{trip.profile.module_groups.sights.optional.map(r=>{const p=trip.profile!.places.find(x=>x.id===r.place_id);return p?<p key={p.id}>{p.map_url?<a href={p.map_url} target="_blank" rel="noopener noreferrer">{p.display_name} ↗</a>:p.display_name}：{p.description}</p>:null;})}</details>}</div></Ticket></div>;
}

function BudgetModal({ open, budget, spending, onClose, onSave }: { open: boolean; budget: Budget; spending: Record<Exclude<keyof Budget,"total">,number>; onClose: () => void; onSave: (budget: Budget) => void }) {
  const [value, setValue] = useState(budget);
  const keys: { key: Exclude<keyof Budget,"total">; label: string }[] = [{ key:"accommodation",label:"住宿"},{key:"food",label:"餐饮"},{key:"transportation",label:"交通"},{key:"activities",label:"活动"},{key:"shopping",label:"购物"},{key:"other",label:"其他"}];
  const allocated = getAllocatedBudget(value); const remaining = value.total - allocated; const spent=Object.values(spending).reduce((sum,item)=>sum+item,0); const overs=keys.filter(({key})=>spending[key]>value[key]);
  return <Modal open={open} title="调整预算" onClose={onClose}><div className="budget-modal"><Field label="总预算"><div className="money-input"><span>￥</span><input type="number" value={value.total} onChange={(event) => setValue({ ...value, total: Number(event.target.value) })} /></div></Field><div className={clsx("budget-balance", remaining < 0 && "over")}><span>已分配 ￥{allocated.toLocaleString()}</span><strong>{remaining >= 0 ? `未分配 ￥${remaining.toLocaleString()}` : `超出 ￥${Math.abs(remaining).toLocaleString()}`}</strong></div><div className={clsx("budget-spend-summary",spent>value.total&&"over")}><span>当前行程预计花费</span><strong>￥{spent.toLocaleString()} / ￥{value.total.toLocaleString()}</strong></div>{keys.map(({key,label}) => <label className={clsx("budget-row",spending[key]>value[key]&&"over")} key={key}><span>{label}<b>预算 ￥{value[key].toLocaleString()} · 预计 ￥{spending[key].toLocaleString()}</b></span><input type="range" min={0} max={value.total} step={10} value={value[key]} onChange={(event) => setValue({ ...value, [key]: Number(event.target.value) })} /></label>)}{remaining < 0 && <p className="inline-warning"><Warning size={18} />分类预算之和不能超过总预算。</p>}{spent>value.total&&<p className="inline-warning"><Warning size={18}/>当前预计花费已超出总预算 ￥{(spent-value.total).toLocaleString()}，仍可保存预算草稿。</p>}{overs.length>0&&<p className="inline-warning"><Warning size={18}/>{overs.map(({label,key})=>`${label}超出 ￥${(spending[key]-value[key]).toLocaleString()}`).join("；")}</p>}<div className="modal-actions"><Button variant="secondary" onClick={onClose}>取消</Button><Button onClick={() => onSave(value)} disabled={remaining < 0}>查看影响</Button></div></div></Modal>;
}

function TransportModal({ open, segments, onClose, onSave }: { open: boolean; segments: TransportSegment[]; onClose: () => void; onSave: (id: string, mode: "schedule"|"custom_time", scheduleId?: string, customTime?: string) => Promise<void> }) {
  const [segmentId, setSegmentId] = useState(segments[0]?.id ?? "");
  const segment = segments.find((item) => item.id === segmentId) ?? segments[0];
  const [mode, setMode] = useState<"schedule"|"custom_time">(segment?.selectionMode ?? "schedule");
  const [scheduleId, setScheduleId] = useState<string | undefined>(segment?.selectedScheduleId ?? segment?.schedules[0]?.id);
  const [customTime, setCustomTime] = useState(segment?.customTime ?? "09:00");
  const [saving,setSaving]=useState(false);
  const [saveError,setSaveError]=useState<string|null>(null);
  if (!segment) return null;
  const boundaryOnly = segment.schedules.length === 0 || segment.providerRef?.startsWith("user-boundary:");
  const agentIntercity = segment.kind === "intercity";
  const chooseSegment = (item: TransportSegment) => { setSegmentId(item.id); setMode(item.selectionMode); setScheduleId(item.selectedScheduleId ?? item.schedules[0]?.id); setCustomTime(item.customTime ?? (item.kind === "return" ? "18:30" : "09:00")); };
  const save=async()=>{setSaving(true);setSaveError(null);try{await onSave(segment.id,mode,scheduleId,customTime);}catch(error){setSaveError(error instanceof Error?error.message:"交通影响分析失败");}finally{setSaving(false);}};
  return <Modal open={open} title="调整交通与时间约束" onClose={onClose} wide><div className="transport-modal"><div className="segment-tabs">{segments.map((item) => <button key={item.id} className={item.id === segment.id ? "active" : ""} onClick={() => chooseSegment(item)}><small>{item.kind === "outbound" ? "首站抵达" : item.kind === "return" ? "末站返程" : "城市之间"}</small><b>{item.label}</b></button>)}</div><div className="transport-choice">{!boundaryOnly&&!agentIntercity&&<Segmented ariaLabel="交通时间输入方式" value={mode} onChange={setMode} options={[{value:"schedule",label:"当前跨城班次"},{value:"custom_time",label:segment.kind === "return" ? "填写最晚离开时间" : "填写预计到达时间"}]} />}{!boundaryOnly&&(agentIntercity||mode === "schedule") ? <div className="result-schedules">{segment.schedules.map((schedule) => <label key={schedule.id} className={scheduleId === schedule.id ? "selected" : ""}><input type="radio" name="result-schedule" checked={scheduleId === schedule.id} onChange={() => setScheduleId(schedule.id)} disabled={agentIntercity}/><div><span><TransportModeIcon mode={schedule.mode} size={17}/>{schedule.mode}{schedule.recommended && <small>Agent 推荐 · 尚未购票</small>}</span><b>{schedule.code}</b></div><div className="schedule-time"><strong>{schedule.departAt}</strong><i /><strong>{schedule.arriveAt}</strong><small>{schedule.duration}</small></div><div><b>￥{schedule.price}</b><small>{schedule.origin} 至 {schedule.destination}</small></div></label>)}</div> : <><Field label={segment.kind === "return" ? "计划离开时间" : "预计到达时间"} helper="此时间是你确认的硬边界，系统会局部重排受影响日期"><input type="time" value={customTime} onInput={(event) => setCustomTime(event.currentTarget.value)} /></Field><p className="hotel-source-note">这里只记录时间边界，不代表系统查询、推荐或确认了具体车次和航班。</p></>}</div><div className="transport-preference-note"><Train size={20} /><span><b>{segment.kind === "intercity" ? "跨城高铁已由 Agent 真实查询" : "市内路线仍由 Agent 计算"}</b>{segment.kind === "intercity" ? "想换晚一点、上午或更便宜的班次，可在“调整这趟旅行”中直接告诉 Agent；已购票时请写明车次。" : "首站抵达和末站返程只使用你填写的时间与地点，不自动猜测班次。"}</span></div>{saveError&&<p className="inline-warning"><Warning size={18}/>{saveError}</p>}<div className="modal-actions">{agentIntercity ? <Button onClick={onClose}>知道了</Button> : <><Button variant="secondary" onClick={onClose} disabled={saving}>取消</Button><Button disabled={saving||(!boundaryOnly&&mode==="schedule"?!scheduleId:!customTime)} onClick={()=>void save()}>{saving?"正在检查真实影响…":"检查影响"}</Button></>}</div></div></Modal>;
}

function HotelModal({ open, trip, initialCity, onClose, onSelect }: { open: boolean; trip: Trip; initialCity: string; onClose: () => void; onSelect: (city: string, hotel: HotelOption) => Promise<void> }) {
  const hotelCities=[...new Set([...(trip.hotelStays?.map((item)=>item.city) ?? []),...trip.days.filter((day)=>day.activities.some((item)=>item.kind==="hotel")).map((day)=>day.city)])];
  const [city,setCity]=useState(hotelCities.includes(initialCity)?initialCity:hotelCities[0]??initialCity);
  const [view,setView]=useState<"recommended"|"search"|"locate">("recommended");
  const [query,setQuery]=useState("");
  const [hotels,setHotels]=useState<HotelOption[]>([]);
  const [loading,setLoading]=useState(true);
  const [analyzingId,setAnalyzingId]=useState<string|null>(null);
  const [error,setError]=useState<string|null>(null);
  const currentStay=trip.hotelStays?.find((item)=>item.city===city) ?? (()=>{const item=trip.days.flatMap((day)=>day.activities).find((activity)=>activity.city===city&&activity.kind==="hotel");return item?{name:item.title,pricePerNight:item.cost}:undefined;})();

  useEffect(()=>{
    let ignore=false;
    void legacyFetch("/api/hotels/search",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({destination:city,checkInDate:trip.startDate,checkOutDate:trip.endDate,keyword:"酒店"})})
      .then(async(response)=>{const payload=await response.json() as {items?:HotelOption[];message?:string};if(!response.ok)throw new Error(payload.message||"酒店查询失败");if(!ignore)setHotels(payload.items??[]);})
      .catch((reason)=>{if(!ignore)setError(reason instanceof Error?reason.message:"酒店查询失败");})
      .finally(()=>{if(!ignore)setLoading(false);});
    return()=>{ignore=true;};
  },[city,trip.endDate,trip.startDate]);

  const search=async()=>{
    if(!query.trim())return;
    setLoading(true);setError(null);setHotels([]);
    try{const endpoint=view==="locate"?"/api/hotels/locate":"/api/hotels/search";const body=view==="locate"?{city,keyword:query.trim()}:{destination:city,checkInDate:trip.startDate,checkOutDate:trip.endDate,keyword:query.trim()};const response=await legacyFetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const payload=await response.json() as {items?:HotelOption[];message?:string};if(!response.ok)throw new Error(payload.message||"酒店查询失败");setHotels(payload.items??[]);}
    catch(reason){setError(reason instanceof Error?reason.message:"酒店查询失败");}
    finally{setLoading(false);}
  };
  const choose=async(hotel:HotelOption)=>{setAnalyzingId(hotel.id);setError(null);try{await onSelect(city,hotel);}catch(reason){setError(reason instanceof Error?reason.message:"酒店影响分析失败");}finally{setAnalyzingId(null);}};
  return <Modal open={open} title="更换酒店" onClose={onClose} wide><div className="hotel-picker">
    <div className="hotel-context"><Field label="选择住宿城市"><select value={city} onChange={(event)=>{setLoading(true);setError(null);setHotels([]);setCity(event.target.value);}}>{hotelCities.map((item)=><option key={item}>{item}</option>)}</select></Field><div><small>当前酒店</small><b>{currentStay?.name??"尚未识别"}</b><span>{currentStay?.pricePerNight?`￥${currentStay.pricePerNight} / 晚`:"价格待确认"}</span></div></div>
    <div className="hotel-picker-tabs"><button className={view==="recommended"?"active":""} onClick={()=>setView("recommended")}>实时推荐</button><button className={view==="search"?"active":""} onClick={()=>setView("search")}>FlyAI 搜索</button><button className={view==="locate"?"active":""} onClick={()=>setView("locate")}>高德定位</button></div>
    {view !== "recommended" && <div className="hotel-search"><label><MagnifyingGlass size={19}/><input autoFocus value={query} onChange={(event)=>setQuery(event.target.value)} onKeyDown={(event)=>{if(event.key==="Enter"){event.preventDefault();void search();}}} placeholder={view==="locate"?"输入已预订酒店、民宿全名或地址":"输入酒店名、品牌或商圈"}/><Button disabled={!query.trim()||loading} onClick={()=>void search()}>{loading?"查询中…":"查询"}</Button></label><small>{view==="locate"?"结果来自高德位置服务，适合 FlyAI 未收录的住宿；房价需另行确认。":"结果来自 FlyAI；价格与余量请以来源页为准。"} 选择后都会先计算每日首尾交通变化。</small></div>}
    {error&&<p className="inline-warning"><Warning size={18}/>{error}</p>}
    {loading?<div className="hotel-loading"><span/><span/><span/></div>:<div className="hotel-options">{hotels.filter((hotel)=>view==="locate"?hotel.source==="amap":hotel.source==="flyai").map((hotel)=><Ticket key={hotel.id} className="hotel-option"><div className="hotel-photo" style={{backgroundImage:`url('${hotel.imageUrl??"/destinations/西湖.png"}')`}}/><div><small>{hotel.address||hotel.nearby||city}</small><h3>{hotel.name}</h3><p>{hotel.source==="amap"?"高德真实位置":hotel.score?`评分 ${hotel.score}`:"暂无评分"}{hotel.review?`，${hotel.review}`:hotel.star?`，${hotel.star}`:""}</p><div><strong>{hotel.pricePerNightCny===undefined?"价格待确认":`￥${hotel.pricePerNightCny} / 晚`}</strong><Button disabled={Boolean(analyzingId)} onClick={()=>void choose(hotel)}>{analyzingId===hotel.id?"计算交通影响…":"查看换店影响"}</Button></div></div></Ticket>)}{hotels.filter((hotel)=>view==="locate"?hotel.source==="amap":hotel.source==="flyai").length===0&&!error&&<div className="hotel-no-result"><Bed size={30} weight="duotone"/><h3>没有找到可用的真实酒店结果</h3><p>{view==="locate"?"请尝试输入酒店完整名称、道路或行政区。":"可切换到“高德定位”，查找已预订酒店或未被 FlyAI 收录的住宿。"}</p></div>}</div>}
  </div></Modal>;
}

function ActivityEditor({ state, days, onClose, onSave }: { state: Exclude<EditorState,null>; days: {id:string;label:string;city:string}[]; onClose: () => void; onSave: (data: Partial<Activity>|Omit<Activity,"id"|"dayId">, dayId: string) => Promise<boolean> }) {
  const base = state.mode === "edit" ? state.item : null;
  const initialDayId = base?.dayId ?? (state.mode === "add" ? state.dayId : days[0]?.id);
  const [title,setTitle] = useState(base?.title ?? ""); const [dayId,setDayId] = useState(initialDayId); const [start,setStart] = useState(base?.start ?? "10:00"); const [end,setEnd] = useState(base?.end ?? "11:30"); const [cost,setCost] = useState(base?.cost ?? 0); const [reason,setReason] = useState(base?.reason ?? "手动加入的行程活动");
  const [transportMode,setTransportMode] = useState(base?.transport?.mode ?? "步行");
  const [transportMinutes,setTransportMinutes] = useState(base?.transport?.durationMinutes ?? 12);
  const [transportDistance,setTransportDistance] = useState(base?.transport?.distanceKm ?? 1.1);
  const [saving,setSaving] = useState(false);
  const [saveError,setSaveError] = useState<string|null>(null);
  const durationMinutes = Math.max(0, (Number(end.slice(0,2))*60+Number(end.slice(3))) - (Number(start.slice(0,2))*60+Number(start.slice(3))));
  const durationLabel = durationMinutes >= 60 ? `${Math.floor(durationMinutes/60)} 小时${durationMinutes%60 ? ` ${durationMinutes%60} 分钟` : ""}` : `${durationMinutes} 分钟`;
  const data = { title, city: base?.city ?? days.find((day) => day.id === dayId)?.city ?? "", kind: base?.kind ?? "attraction" as const, start, end, duration: durationLabel, cost, reason, coord: base?.coord ?? [0,0] as [number,number], locked: base?.locked ?? false, transport: { ...base?.transport, mode:transportMode, durationMinutes:transportMinutes, distanceKm:transportDistance } };
  const save=async()=>{setSaving(true);setSaveError(null);try{await onSave(data,dayId);}catch(error){setSaveError(error instanceof Error?error.message:"活动保存失败");}finally{setSaving(false);}};
  return <Modal open={Boolean(state)} title={state.mode === "edit" ? "编辑活动" : "新增活动"} onClose={onClose}><div className="activity-editor"><Field label="活动名称" helper={state.mode==="add"?"保存时会通过高德核验地点并取得真实坐标":undefined}><input value={title} onChange={(event) => setTitle(event.target.value)} /></Field><Field label="安排到哪一天"><select value={dayId} onChange={(event) => setDayId(event.target.value)}>{days.map((day) => <option key={day.id} value={day.id}>{day.label}</option>)}</select></Field><div className="two-fields"><Field label="开始时间"><input type="time" value={start} onInput={(event) => setStart(event.currentTarget.value)} /></Field><Field label="结束时间"><input type="time" value={end} onInput={(event) => setEnd(event.currentTarget.value)} /></Field></div><Field label="预计费用"><div className="money-input"><span>￥</span><input aria-label="预计费用（人民币）" type="number" min={0} value={cost} onChange={(event) => setCost(Number(event.target.value))} /></div></Field><div className="transport-editor"><Field label="前往此活动的交通方式"><select value={transportMode} onChange={(event) => setTransportMode(event.target.value)}><option>步行</option><option>公交</option><option>地铁</option><option>打车</option><option>自驾</option><option>骑行</option><option>包车</option></select></Field><Field label="交通时长（分钟）" helper="保存后会重新查询所选方式的真实路线"><input type="number" min={0} value={transportMinutes} onChange={(event) => setTransportMinutes(Number(event.target.value))}/></Field><Field label="距离（公里）" helper="保存后会按高德结果更新"><input type="number" min={0} step="0.1" value={transportDistance} onChange={(event) => setTransportDistance(Number(event.target.value))}/></Field></div><Field label="备注"><textarea value={reason} onChange={(event) => setReason(event.target.value)} /></Field>{end <= start && <p className="inline-warning"><Warning size={18} />结束时间必须晚于开始时间。</p>}{saveError&&<p className="inline-warning"><Warning size={18}/>{saveError}</p>}<div className="modal-actions"><Button variant="secondary" onClick={onClose} disabled={saving}>取消</Button><Button disabled={!title || end <= start || saving} onClick={()=>void save()}>{saving?"核验并重算中…":"保存并重算"}</Button></div></div></Modal>;
}

function ShareModal({ open, tripId, shareId, shared, onClose }: { open: boolean; tripId:string; shareId?:string; shared:boolean; onClose:()=>void }) {
  const toggleShare = useTripStore((state) => state.toggleShare); const setToast = useTripStore((state) => state.setToast);
  const url = typeof window !== "undefined" ? `${window.location.origin}/share/${shareId ?? "preview"}` : `/share/${shareId ?? "preview"}`;
  return <Modal open={open} title="只读分享" onClose={onClose}><div className="share-modal">{shared ? <><p>拥有链接的人可以查看当前快照，不能编辑你的原行程。</p><div className="share-url"><input readOnly value={url}/><Button onClick={() => { navigator.clipboard?.writeText(url); setToast("分享链接已复制"); }}>复制链接</Button></div><Button variant="danger" onClick={() => {toggleShare(tripId,false);onClose();}}>停止分享</Button></> : <><p>分享已经停止，原链接不可继续访问。</p><Button onClick={() => {toggleShare(tripId,true);onClose();}}>重新开启分享</Button></>}</div></Modal>;
}

function ReplacementModal({tripId,target,onClose,onChoose}:{tripId:string;target:ReplacementTarget;onClose:()=>void;onChoose:(candidate:ReplanOptions['replacement_candidates'][number])=>void}){
  const [options,setOptions]=useState<ReplanOptions|null>(null);
  const [selected,setSelected]=useState("");
  const [error,setError]=useState("");
  useEffect(()=>{const controller=new AbortController();setOptions(null);setSelected("");setError("");getReplanOptions(tripId,target.day,controller.signal).then(value=>{setOptions(value);setSelected(value.replacement_candidates[0]?.place_id??"");}).catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:"候选地点读取失败");});return()=>controller.abort();},[target.day,tripId]);
  const current=options?.stops.find(item=>item.place_id===target.item.placeId);
  const candidate=options?.replacement_candidates.find(item=>item.place_id===selected);
  return <Modal open title="替换这个地点" onClose={onClose}><div className="replan-request"><p>Day {target.day} · {target.item.title}</p>{!options&&!error&&<p role="status">正在读取当天合法候选…</p>}{error&&<p className="inline-warning"><Warning size={18}/>{error}</p>}{options&&current&&!current.replaceable&&<p className="inline-warning"><Warning size={18}/>这是当天的主景点或固定用餐，不能在这里替换。</p>}{options&&current?.replaceable&&options.replacement_candidates.length===0&&<p className="inline-warning"><Warning size={18}/>当天没有剩余的合适候选。可以使用“重排这一天”重新分配候选。</p>}{options&&current?.replaceable&&options.replacement_candidates.length>0&&<Field label="替换为" helper="这里只显示当天已分配且尚未使用的合法候选"><select value={selected} onChange={event=>setSelected(event.target.value)}>{options.replacement_candidates.map(item=><option key={item.place_id} value={item.place_id}>{item.display_name}{item.pool==='backup'?'（备用）':''}</option>)}</select></Field>}{candidate?.description&&<p>{candidate.description}</p>}<div className="modal-actions"><Button variant="secondary" onClick={onClose}>取消</Button><Button disabled={!candidate||!current?.replaceable} onClick={()=>candidate&&onChoose(candidate)}>查看影响</Button></div></div></Modal>;
}

function ReplanRequestModal({ value, onClose, onSubmit }:{ value:ReplanRequest; onClose:()=>void; onSubmit:(opinion:string)=>void }) {
  const [opinion,setOpinion]=useState(value.opinion);
  const examples=value.scope === "day" ? ["减少步行，下午安排更轻松", "把室外景点放到上午", "增加一个适合儿童的活动"] : ["整体节奏更松弛，每天晚一点出发", "减少换城市次数，优先高铁", "控制餐饮预算，保留核心景点"];
  return <Modal open title={value.scope === "day" ? "告诉 Agent 这一天怎么调" : "告诉 Agent 全程怎么调"} onClose={onClose}><div className="replan-request"><Field label="调整意见" helper="这段文字会作为下一次重规划的明确指令"><textarea autoFocus value={opinion} onChange={(event)=>setOpinion(event.target.value)} placeholder="例如：减少步行，把下午改得轻松一些，同时保留已经锁定的景点。"/></Field><div className="replan-examples"><span>可以这样说</span>{examples.map((example)=><button type="button" key={example} onClick={()=>setOpinion(example)}>{example}</button>)}</div><div className="modal-actions"><Button variant="secondary" onClick={onClose}>取消</Button><Button disabled={!opinion.trim()} onClick={()=>onSubmit(opinion.trim())}>预览调整影响</Button></div></div></Modal>;
}

function ImpactModal({ value, onClose, onDone }: { value: ImpactState|null; onClose:()=>void; onDone:()=>void }) {
  const [running,setRunning]=useState(false); const [error,setError]=useState<string|null>(null);
  const run=async(action:()=>void|Promise<void>)=>{setRunning(true);setError(null);try{await action();onDone();}catch(reason){setError(reason instanceof Error?reason.message:"操作失败，请稍后重试");}finally{setRunning(false);}};
  return <Modal open={Boolean(value)} title={value?.title ?? "变更影响"} onClose={onClose}><div className="impact-modal">{value && <><p className="impact-issue"><Warning size={20} />{value.issue}</p><div className="impact-columns"><div><h3>将受影响</h3>{value.affected.map((item) => <span key={item}><Warning size={15} />{item}</span>)}</div><div><h3>会保留</h3>{value.preserved.map((item) => <span key={item}><Lock size={15} />{item}</span>)}</div></div>{error&&<p className="inline-warning"><Warning size={18}/>{error}</p>}<div className="modal-actions"><Button variant="ghost" disabled={running} onClick={onClose}>取消</Button>{value.direct && <Button variant="secondary" disabled={running} onClick={()=>void run(value.direct!)}>仅应用变更</Button>}<Button disabled={running} onClick={()=>void run(value.reflow)}>{running?"Agent 正在重规划…":"确认并联动调整"}</Button></div></>}</div></Modal>;
}


