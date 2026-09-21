"use client";
import { ProfileSight, ProfileWidgets } from "./profile-guide";
import { useState } from "react";
import { Compass, ForkKnife, MapPin, ArrowUpRight } from "@phosphor-icons/react";
import { Modal, Ticket } from "@/components/ui";
import type { Activity, Trip } from "@/types/trip";
import { cityNotes, mapLink, sightNotes } from "./guide-data";

export function SightDetail({item,trip,onClose}:{item:Activity;trip:Trip;onClose:()=>void}) {
  if(trip.profile){const place=trip.profile.places.find(p=>p.id===item.placeId);if(place)return <ProfileSight place={place} onClose={onClose}/>;}
  const day=trip.days.find(day=>day.id===item.dayId);
  const description=Object.entries(sightNotes).find(([name])=>item.title.includes(name))?.[1];
  const window=item.schedulePreference?.availabilityWindow;
  const time=(minute:number)=>`${String(Math.floor(minute/60)).padStart(2,"0")}:${String(minute%60).padStart(2,"0")}`;
  return <Modal open title="景点手记" onClose={onClose} wide><article className="sight-detail">
    <div className={`sight-cover ${item.title.includes("西湖")?"sight-cover-lake":""}`}><MapPin size={60} weight="duotone"/><span>{item.city} · 旅途一站</span></div>
    <p className="guide-eyebrow">Day {day?.dayNumber} · {day?.date} · 已安排</p><h2>{item.title}</h2>
    <div className="sight-facts"><span>建议停留 {item.duration}</span><span>{item.start} — {item.end}</span><span>行程预计费用 ￥{item.cost}</span></div>
    <p className="sight-intro">{description??item.reason}</p>
    {description&&<div className="guide-note"><b>这趟行程的安排</b><p>{item.reason}</p></div>}
    <div className="guide-note"><b>开放与预约</b><p>{window?`当前行程记录的可参观时段：${time(window.startMinute)} — ${time(window.endMinute)}。`:"暂未收录已核验的开放时段。"}开放、闭馆与预约信息请在出发前通过景区官方渠道确认。</p></div>
    {item.placeMatch?.matchedAddress&&<p><MapPin size={16}/> {item.placeMatch.matchedAddress}</p>}
    <footer className="sight-links"><a href={mapLink(item)} target="_blank" rel="noopener noreferrer">在高德地图查看 <ArrowUpRight size={17}/></a></footer>
  </article></Modal>;
}
export function LocalGuideWidgets({trip}:{trip:Trip}) {
 if(trip.profile)return <ProfileWidgets profile={trip.profile}/>;
 return <LegacyWidgets trip={trip}/>;
}
function LegacyWidgets({trip}:{trip:Trip}) {
 const [view,setView]=useState<"experiences"|"food"|null>(null);
 const cities=cityNotes(trip);
 return <><div className="local-guide-widgets">{([{key:"experiences",title:"当地特色体验",text:"把目的地的独特，写进旅行",icon:Compass},{key:"food",title:"餐饮指南",text:"街巷里的地方风味",icon:ForkKnife}] as const).map(({key,title,text,icon:Icon})=><button key={key} onClick={()=>setView(key)}><Icon size={29} weight="duotone"/><span><b>{title}</b><small>{text}</small></span><ArrowUpRight size={18}/></button>)}</div>
 {view&&<Modal open wide title={view==="food"?"餐饮指南":"当地特色体验"} onClose={()=>setView(null)}><div className="guide-collection"><p>跟着这趟旅程，慢慢认识 {trip.destinations.join("、")}。</p>{cities.map(({city,notes})=><section key={city}><h3>{city}</h3><div className="guide-note-grid">{notes[view].length?notes[view].map(note=><Ticket key={note.title}><h4>{note.title}</h4><p>{note.text}</p></Ticket>):<p className="guide-empty">这个目的地的{view==="food"?"特色小吃":"当地体验"}还在整理中。</p>}</div></section>)}<small>体验与风味灵感供行程参考，营业、预约和价格请在到访前确认。</small></div></Modal>}</>;
}
