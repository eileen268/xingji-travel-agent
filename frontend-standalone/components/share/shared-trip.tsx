"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, CalendarBlank, Lock, MapPin, UsersThree, Wallet } from "@phosphor-icons/react";
import { Brand, PaperPage } from "@/components/app-chrome";
import { Button, Ticket } from "@/components/ui";
import { TripMap } from "@/components/trip/trip-map";
import { useTripStore } from "@/store/trip-store";

export function SharedTrip({shareId}:{shareId:string}){
  const trip=useTripStore((state)=>state.trips.find((item)=>item.shareId===shareId&&item.shared) ?? (shareId==="jn-hzsn-2026"?state.trips.find((item)=>item.id==="demo-trip"&&item.shared):undefined));
  const [dayId,setDayId]=useState<string|"overview">("overview"); const [selected,setSelected]=useState<string|null>(null);
  if(!trip) return <PaperPage><div className="share-unavailable"><Lock size={46}/><h1>这个分享已经停止</h1><p>链接对应的只读快照不可访问，请联系行程创建者。</p><Link href="/"><Button>创建自己的行程</Button></Link></div></PaperPage>;
  const day=trip.days.find((item)=>item.id===dayId);
  return <PaperPage className="share-page"><header className="share-header"><Brand/><span><Lock size={14}/>只读行程</span><Link href="/create"><Button>规划我的旅程<ArrowRight size={17}/></Button></Link></header><div className="share-wrap"><section className="share-hero"><div><p>{trip.startDate} 至 {trip.endDate}</p><h1 className="display-title">{trip.destinations.join(" · ")}</h1><span>由行迹 Journey Notes 生成的旅行计划</span></div><div className="share-facts"><Ticket><CalendarBlank size={20}/><span><b>{trip.days.length} 天</b>完整行程</span></Ticket><Ticket><UsersThree size={20}/><span><b>{trip.travelers.adults+trip.travelers.children+trip.travelers.seniors} 人</b>{trip.travelers.style}</span></Ticket><Ticket><Wallet size={20}/><span><b>￥{trip.budget.total.toLocaleString()}</b>总预算</span></Ticket></div></section><nav className="day-tabs share-tabs"><button className={dayId==="overview"?"day-tab active":"day-tab"} onClick={()=>setDayId("overview")}>总览</button>{trip.days.map((item)=><button key={item.id} className={dayId===item.id?"day-tab active":"day-tab"} onClick={()=>setDayId(item.id)}><span style={{background:item.routeColor}}/>Day {item.dayNumber} {item.city}</button>)}</nav><div className="share-content"><div className="share-itinerary">{dayId==="overview"?trip.days.map((item)=><button key={item.id} onClick={()=>setDayId(item.id)}><span style={{borderColor:item.routeColor,color:item.routeColor}}>{item.dayNumber}</span><div><h2>{item.city}</h2><p>{item.activities.slice(0,3).map((activity)=>activity.title).join("、")}</p></div></button>):day?.activities.map((item)=><article key={item.id} className={selected===item.id?"selected":""} onClick={()=>setSelected(item.id)}><time>{item.start}</time><div><h3>{item.title}</h3><p>{item.reason}</p><small><MapPin size={13}/>{item.transport?.mode} {item.transport?.durationMinutes==null?"交通时间待计算":`${item.transport.durationMinutes} 分钟`}，￥{item.cost}</small></div></article>)}</div><div className="share-map"><TripMap trip={trip} activeDayId={dayId} selectedActivityId={selected} onSelect={setSelected}/></div></div></div></PaperPage>;
}
