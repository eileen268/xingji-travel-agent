"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { ArrowLeft, CheckCircle, Copy, FilePdf, ImageSquare, LinkSimple, Lightbulb, MapPin, Train } from "@phosphor-icons/react";
import { toPng } from "html-to-image";
import jsPDF from "jspdf";
import { AppHeader, PaperPage } from "@/components/app-chrome";
import { TransportModeIcon } from "@/components/transport-mode-icon";
import { Button, Ticket } from "@/components/ui";
import { useTripStore } from "@/store/trip-store";
import type { TripDay } from "@/types/trip";
import { weatherImpactNote } from "@/features/weather/weather-impact";

const cityArt: Record<string,string> = { 杭州:"西湖.png", 绍兴:"苏州园林.png", 宁波:"上海.png", 象山:"漓江.png" };
const baseTips = ["热门景点尽量在开门后 30 分钟内抵达", "老人和儿童连续步行 90 分钟后安排休息", "证件、充电宝和常用药放进随身小包"];

function RouteSketch({day}:{day:TripDay}) {
  const points = day.activities.map((_,index) => ({ x:16+(index%3)*34, y:20+Math.floor(index/3)*42+(index%2)*13 }));
  const path = points.map((point,index) => `${index ? "L" : "M"}${point.x} ${point.y}`).join(" ");
  return <div className="export-day-map">
    <img src={`/destinations/${cityArt[day.city] ?? "西湖.png"}`} alt={`${day.city}手绘路线背景`}/>
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label={`第${day.dayNumber}天路线`} fill="none"><path d={path} fill="none" stroke="#405f4c" strokeWidth="1.2" strokeDasharray="3 2" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke"/></svg>
    {points.map((point,index)=><span key={day.activities[index].id} style={{left:`${point.x}%`,top:`${point.y}%`,background:day.routeColor}}>{index+1}<small>{day.activities[index].start}<br/>{day.activities[index].title}</small></span>)}
    <b className="map-day-ribbon">Day {day.dayNumber} · {day.city}</b>
  </div>;
}

function TimelineDay({day}:{day:TripDay}) {
  return <article className="export-day-plan" style={{"--day-color":day.routeColor} as React.CSSProperties}>
    <header><span>Day {day.dayNumber}</span><div><h3>{day.city} · {day.weather.label}</h3><small>{day.date} · {day.weather.low} 至 {day.weather.high}℃</small></div></header>
    <ol>{day.activities.map((item,index)=><li key={item.id}><i>{index+1}</i><time>{item.start}</time><div><b>{item.title}</b><p>{item.reason}</p>{item.transport && <small><TransportModeIcon mode={item.transport.mode} size={11}/>{item.transport.mode} · {item.transport.durationMinutes==null?"交通时间待计算":`${item.transport.durationMinutes} 分钟`}{item.cost ? ` · ￥${item.cost}` : ""}</small>}</div></li>)}</ol>
    <p className="day-note"><Lightbulb size={14}/>{weatherImpactNote(day.weather)}</p>
  </article>;
}

export function ExportStudio({tripId}:{tripId:string}) {
  const trip = useTripStore((state)=>state.trips.find((item)=>item.id===tripId) ?? state.trips[0]);
  const toggleShare = useTripStore((state)=>state.toggleShare);
  const previewRef = useRef<HTMLDivElement>(null);
  const [busy,setBusy] = useState<"image"|"pdf"|null>(null);
  const [message,setMessage] = useState("");
  if(!trip) return null;

  const capture = async () => {
    if(!previewRef.current) throw new Error("预览尚未准备好");
    return toPng(previewRef.current,{pixelRatio:2,cacheBust:true,backgroundColor:"#eee8dc"});
  };
  const downloadImage = async () => { try{setBusy("image");const data=await capture();const a=document.createElement("a");a.download=`行迹-${trip.destinations.join("-")}-完整行程.png`;a.href=data;a.click();setMessage("完整行程长图已生成");}catch{setMessage("图片生成失败，请稍后重试");}finally{setBusy(null);} };
  const downloadPdf = async () => {
    try{
      setBusy("pdf");
      const data=await capture();
      const image=await new Promise<HTMLImageElement>((resolve,reject)=>{const value=new Image();value.onload=()=>resolve(value);value.onerror=reject;value.src=data;});
      const pdf=new jsPDF({orientation:"portrait",unit:"mm",format:"a4"});
      const pageWidth=210,pageHeight=297,margin=8,printWidth=pageWidth-margin*2,printHeight=pageHeight-margin*2;
      const slicePx=Math.floor(image.width*(printHeight/printWidth));
      let offset=0,page=0;
      while(offset<image.height){
        const height=Math.min(slicePx,image.height-offset);
        const canvas=document.createElement("canvas");canvas.width=image.width;canvas.height=height;
        canvas.getContext("2d")?.drawImage(image,0,offset,image.width,height,0,0,image.width,height);
        if(page>0) pdf.addPage();
        pdf.addImage(canvas.toDataURL("image/png"),"PNG",margin,margin,printWidth,printWidth*height/image.width);
        offset+=height;page+=1;
      }
      pdf.save(`行迹-${trip.destinations.join("-")}-完整行程.pdf`);setMessage(`PDF 已按内容生成 ${page} 页`);
    }catch{setMessage("PDF 生成失败，请稍后重试");}finally{setBusy(null);}
  };
  const shareUrl=typeof window!=="undefined"?`${window.location.origin}/share/${trip.shareId??"preview"}`:`/share/${trip.shareId??"preview"}`;
  const spent=trip.days.flatMap((day)=>day.activities).reduce((sum,item)=>sum+item.cost,0);
  const tips=trip.travelTips?.length?trip.travelTips:baseTips;

  return <><AppHeader/><PaperPage><div className="export-wrap"><header className="subpage-heading"><div><Link href={`/trip/${trip.id}`} className="back-link"><ArrowLeft size={18}/>返回行程</Link><h1 className="display-title">导出完整旅行手账</h1><p>预览包含全部日程、每日路线、交通提示、预算和旅行小贴士。</p></div></header><div className="export-layout"><section className="export-preview-shell"><div ref={previewRef} className="export-poster">
    <header className="export-cover"><div><p>行迹 · AI 旅行手账</p><h2>{trip.destinations.join(" & ")}</h2><strong>{trip.days.length} 日旅行计划</strong><span>{trip.startDate} 至 {trip.endDate} · 从 {trip.origin} 出发</span></div><div className="poster-stamp">一路好心情</div></header>
    <section className="export-overview"><div><b>行程概览</b><p>{trip.days.map((day)=>`Day ${day.dayNumber} ${day.city}`).join(" · ")}</p></div><div><b>旅行方式</b><p>{trip.travelers.style} · {trip.travelers.adults} 成人 · {trip.travelers.children} 儿童 · {trip.travelers.seniors} 老人</p></div></section>
    <div className="export-journal-grid"><div className="export-timelines">{trip.days.map((day)=><TimelineDay key={day.id} day={day}/>)}</div><div className="export-maps">{trip.days.map((day)=><RouteSketch key={day.id} day={day}/>)}</div></div>
    <footer className="export-guide-grid"><section><h3><Lightbulb size={18}/>旅行小贴士</h3>{tips.map((tip)=><p key={tip}>✓ {tip}</p>)}</section><section><h3><Train size={18}/>交通指南</h3><p>城际优先：{trip.preferences.longDistance === "high_speed_rail" ? "高铁" : "Agent 推荐"}</p><p>市内偏好：{trip.preferences.localTransport.join("、")}</p><p>每段交通前预留 20 至 40 分钟机动时间</p></section><section><h3>预算参考</h3><p>总预算 ￥{trip.budget.total.toLocaleString()}</p><p>当前预计 ￥{spent.toLocaleString()}</p><p>未使用 ￥{Math.max(0,trip.budget.total-spent).toLocaleString()}</p></section><section><h3><MapPin size={18}/>出发前检查</h3><p>证件 · 雨具 · 充电宝</p><p>舒适步行鞋 · 常用药</p><p>酒店与返程班次再次确认</p></section></footer>
  </div></section><aside className="export-options"><Ticket><ImageSquare size={30} weight="duotone"/><div><h2>完整长图</h2><p>每天的计划与路线全部保留，适合手机收藏。</p></div><Button onClick={downloadImage} disabled={Boolean(busy)}>{busy==="image"?"正在生成":"生成长图"}</Button></Ticket><Ticket><FilePdf size={30} weight="duotone"/><div><h2>分页 PDF</h2><p>自动按 A4 纵向分页，不截掉后续日期。</p></div><Button variant="secondary" onClick={downloadPdf} disabled={Boolean(busy)}>{busy==="pdf"?"正在生成":"生成 PDF"}</Button></Ticket><Ticket><LinkSimple size={30} weight="duotone"/><div><h2>网页链接</h2><p>生成只读快照，不开放编辑权限。</p></div><div className="share-url"><input readOnly value={shareUrl}/><Button variant="secondary" onClick={()=>{toggleShare(trip.id,true);navigator.clipboard?.writeText(shareUrl);setMessage("只读链接已复制");}}><Copy size={17}/>复制</Button></div></Ticket>{message&&<p className="export-message"><CheckCircle size={18}/>{message}</p>}</aside></div></div></PaperPage></>;
}
