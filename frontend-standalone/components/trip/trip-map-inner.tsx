"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { load } from "@amap/amap-jsapi-loader";
import type { Activity, DayRouteSegment, Trip, TripDay } from "@/types/trip";

type LngLat = [number, number];
type Overlay = object;
type Marker = Overlay & { on: (event: string, handler: () => void) => void };
type MapInstance = { add:(value:Overlay|Overlay[])=>void; remove:(value:Overlay|Overlay[])=>void; addControl:(value:object)=>void; setFitView:(value?:Overlay[], immediate?:boolean, avoid?:number[], maxZoom?:number)=>void; setZoomAndCenter:(zoom:number, center:LngLat, immediate?:boolean, duration?:number)=>void; destroy:()=>void };
type AMapApi = { Map:new(container:HTMLElement, options:Record<string,unknown>)=>MapInstance; Marker:new(options:Record<string,unknown>)=>Marker; Polyline:new(options:Record<string,unknown>)=>Overlay; Pixel:new(x:number,y:number)=>object; Scale:new(options?:Record<string,unknown>)=>object; ToolBar:new(options?:Record<string,unknown>)=>object };
type DrawableSegment = DayRouteSegment & { fromActivityId:string; toActivityId:string; path:LngLat[] };

declare global { interface Window { _AMapSecurityConfig?: { serviceHost:string } } }

function hasMapCoordinate(activity:Activity) {
  const [lat,lng]=activity.coord;
  return !activity.coordinatesUnknown && Number.isFinite(lat) && Number.isFinite(lng) && lat>=-90 && lat<=90 && lng>=-180 && lng<=180 && !(lat===0&&lng===0);
}
function toLngLat(activity:Activity):LngLat { return [activity.coord[1],activity.coord[0]]; }
function markerContent(day:number,index:number,color:string,selected:boolean,title:string,onClick:()=>void) {
  const button=document.createElement("button"); button.type="button"; button.className=`amap-numbered-marker${selected?" selected":""}`; button.style.setProperty("--marker-color",color); button.setAttribute("aria-label",`Day ${day} 第 ${index+1} 站：${title}`);
  button.addEventListener("click",event=>{event.stopPropagation();onClick()});
  const label=document.createElement("span"); label.textContent=String(index+1); button.appendChild(label); return button;
}
function drawableSegments(day:TripDay):DrawableSegment[] {
  const byPlace=new Map(day.activities.filter(hasMapCoordinate).map(item=>[item.placeId,item]));
  const explicit=(day.segments??[]).flatMap(segment=>{const from=byPlace.get(segment.fromPlaceId);const to=byPlace.get(segment.toPlaceId);return from&&to?[{...segment,fromActivityId:from.id,toActivityId:to.id,path:[toLngLat(from),toLngLat(to)]}]:[];});
  if(explicit.length||day.activities.length<2)return explicit;
  return day.activities.slice(0,-1).flatMap((from,index)=>{const to=day.activities[index+1];if(!hasMapCoordinate(from)||!hasMapCoordinate(to))return[];return [{id:`${from.id}->${to.id}`,fromPlaceId:from.placeId??from.id,toPlaceId:to.placeId??to.id,fromName:from.title,toName:to.title,mode:"walking" as const,durationMinutes:null,distanceMeters:null,bufferMinutes:0,fallbackScheduleMinutes:0,idleMinutes:0,routeStatus:"unresolved" as const,routeProvider:null,fromActivityId:from.id,toActivityId:to.id,path:[toLngLat(from),toLngLat(to)]}];});
}

function DemoRouteCanvas({days,selectedActivityId,onSelect}:{days:TripDay[];selectedActivityId:string|null;onSelect:(id:string)=>void}) {
  const drawableDays=days.map(day=>({...day,activities:day.activities.filter(hasMapCoordinate)})).filter(day=>day.activities.length);
  const points=drawableDays.flatMap(day=>day.activities.map(item=>item.coord));
  if(!points.length)return <div className="map-empty"><strong>当前日期没有可定位的地点</strong><span>缺少坐标的活动仍会保留在左侧日程中。</span></div>;
  const lats=points.map(point=>point[0]),lngs=points.map(point=>point[1]);const minLat=Math.min(...lats),maxLat=Math.max(...lats),minLng=Math.min(...lngs),maxLng=Math.max(...lngs);
  const pos=([lat,lng]:[number,number])=>({x:90+(lng-minLng)/Math.max(.01,maxLng-minLng)*820,y:610-(lat-minLat)/Math.max(.01,maxLat-minLat)*520});
  return <div className="demo-route-canvas" aria-label="行程站点路线示意图"><svg viewBox="0 0 1000 700" role="img"><defs><filter id="paper-shadow"><feDropShadow dx="0" dy="4" stdDeviation="5" floodOpacity=".16"/></filter></defs>{drawableDays.map(day=><polyline key={day.id} points={day.activities.map(item=>{const p=pos(item.coord);return `${p.x},${p.y}`}).join(" ")} fill="none" stroke={day.routeColor} strokeWidth="8" strokeLinecap="round" strokeLinejoin="round" strokeDasharray="18 10" opacity=".72"/>)}{drawableDays.flatMap(day=>day.activities.map((item,index)=>{const p=pos(item.coord),selected=item.id===selectedActivityId;return <g key={item.id} className="demo-map-marker" transform={`translate(${p.x} ${p.y})`} onClick={()=>onSelect(item.id)}><circle r={selected?25:20} fill={day.routeColor} stroke="#fffaf0" strokeWidth="5" filter="url(#paper-shadow)"/><text textAnchor="middle" dominantBaseline="central">{index+1}</text></g>}))}</svg><span className="demo-map-label">站点路线预览</span></div>;
}

export function TripMapInner({trip,activeDayId,selectedActivityId,onSelect}:{trip:Trip;activeDayId:string|"overview";selectedActivityId:string|null;onSelect:(id:string)=>void}) {
  const containerRef=useRef<HTMLDivElement>(null),mapRef=useRef<MapInstance|null>(null),sdkRef=useRef<AMapApi|null>(null),overlaysRef=useRef<Overlay[]>([]),onSelectRef=useRef(onSelect);
  const [sdkReady,setSdkReady]=useState(false),[sdkError,setSdkError]=useState<string|null>(null);
  useEffect(()=>{onSelectRef.current=onSelect},[onSelect]);
  const visibleDays=useMemo(()=>activeDayId==="overview"?trip.days:trip.days.filter(day=>day.id===activeDayId),[activeDayId,trip.days]);
  const activities=useMemo(()=>visibleDays.flatMap(day=>day.activities.filter(hasMapCoordinate)),[visibleDays]);
  const missing=useMemo(()=>visibleDays.reduce((sum,day)=>sum+day.activities.filter(item=>!hasMapCoordinate(item)).length,0),[visibleDays]);
  const selected=useMemo(()=>activities.find(item=>item.id===selectedActivityId),[activities,selectedActivityId]);
  const routes=useMemo(()=>visibleDays.map(day=>({day,segments:drawableSegments(day)})),[visibleDays]);

  useEffect(()=>{let cancelled=false;const key=process.env.NEXT_PUBLIC_AMAP_JS_KEY;if(!key){queueMicrotask(()=>setSdkError("高德地图尚未配置，当前显示站点路线预览。"));return}if(!containerRef.current)return;window._AMapSecurityConfig={serviceHost:window.location.origin+"/amap-security/jscode"};const timeout=window.setTimeout(()=>{if(!cancelled)setSdkError("互动地图加载超时，当前显示站点路线预览。")},12000);load({key,version:"2.0",plugins:["AMap.Scale","AMap.ToolBar"]}).then((loaded:unknown)=>{if(cancelled||!containerRef.current)return;const AMap=loaded as AMapApi;sdkRef.current=AMap;const map=new AMap.Map(containerRef.current,{zoom:11,center:activities[0]?toLngLat(activities[0]):[121.47,31.23],mapStyle:"amap://styles/whitesmoke",viewMode:"2D",resizeEnable:true});map.addControl(new AMap.Scale({position:"LB"}));map.addControl(new AMap.ToolBar({position:"RB",liteStyle:true}));mapRef.current=map;setSdkReady(true);setSdkError(null)}).catch(()=>{if(!cancelled)setSdkError("互动地图暂时无法连接，当前显示站点路线预览。")}).finally(()=>window.clearTimeout(timeout));return()=>{cancelled=true;window.clearTimeout(timeout);mapRef.current?.destroy();mapRef.current=null;sdkRef.current=null;overlaysRef.current=[]}},[]);

  useEffect(()=>{const AMap=sdkRef.current,map=mapRef.current;if(!sdkReady||!AMap||!map)return;if(overlaysRef.current.length)map.remove(overlaysRef.current);const overlays:Overlay[]=[];for(const {day,segments} of routes){for(const segment of segments){const highlighted=segment.fromActivityId===selectedActivityId||segment.toActivityId===selectedActivityId,verified=segment.routeStatus==="verified";overlays.push(new AMap.Polyline({path:segment.path,strokeColor:day.routeColor,strokeWeight:highlighted?9:activeDayId==="overview"?5:7,strokeOpacity:highlighted?.96:verified?.82:.52,strokeStyle:verified?"solid":"dashed",borderWeight:2,outlineColor:"rgba(255,255,255,.78)",lineJoin:"round",lineCap:"round",showDir:verified,zIndex:highlighted?55:40}))}const mapped=day.activities.filter(hasMapCoordinate);mapped.forEach((item,index)=>{const choose=()=>onSelectRef.current(item.id);const marker=new AMap.Marker({position:toLngLat(item),content:markerContent(day.dayNumber,index,day.routeColor,item.id===selectedActivityId,item.title,choose),offset:new AMap.Pixel(-17,-17),title:`${index+1}. ${item.title}`,zIndex:item.id===selectedActivityId?120:90});marker.on("click",choose);overlays.push(marker)})}if(overlays.length){map.add(overlays);map.setFitView(overlays,false,[82,64,96,64],activeDayId==="overview"?11:15)}overlaysRef.current=overlays},[activeDayId,routes,sdkReady,selectedActivityId]);
  useEffect(()=>{if(selected&&mapRef.current)mapRef.current.setZoomAndCenter(15,toLngLat(selected),false,450)},[selected]);
  const segments=routes.reduce((sum,item)=>sum+item.segments.length,0),verified=routes.reduce((sum,item)=>sum+item.segments.filter(segment=>segment.routeStatus==="verified").length,0);const label=activeDayId==="overview"?"全部行程":visibleDays[0]?`Day ${visibleDays[0].dayNumber} · ${visibleDays[0].city}`:"当前日期";
  return <div className="trip-map" aria-label="当前行程地图"><div ref={containerRef} className="amap-canvas"/>{!sdkReady?<DemoRouteCanvas days={visibleDays} selectedActivityId={selectedActivityId} onSelect={onSelect}/>:null}{!sdkReady&&!sdkError?<div className="map-loading"><span/><p>正在铺开高德地图</p></div>:null}{sdkError&&!sdkReady?<div className="map-notice" role="status">{sdkError}</div>:null}<div className="map-context"><small>地图范围</small><strong>{label}</strong><span>{activities.length} 个可定位地点{missing?` · ${missing} 个待定位`:""}</span></div><div className="map-stats"><span>{segments?`${verified}/${segments} 段交通已核验`:`${activities.length} 个停靠点`}</span><span>路线为站点连线</span></div>{activities.length?<div className="map-stop-list" aria-label="地图站点">{visibleDays.flatMap(day=>day.activities.filter(hasMapCoordinate).map((item,index)=><button key={item.id} type="button" className={item.id===selectedActivityId?"selected":""} aria-pressed={item.id===selectedActivityId} onClick={()=>onSelect(item.id)}><i style={{background:day.routeColor}}>{index+1}</i><span><small>{item.start}</small><b>{item.title}</b></span></button>))}</div>:null}</div>;
}
