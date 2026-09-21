"use client";
import { ProfilePreparation } from "./profile-guide";
import { useEffect, useState } from "react";
import { Check, Backpack, CalendarCheck, Translate, Compass } from "@phosphor-icons/react";
import { Ticket } from "@/components/ui";
import type { Trip } from "@/types/trip";
import { cityNotes } from "./guide-data";
const tabs=[{id:"packing",title:"必备物品",icon:Backpack},{id:"booking",title:"需要预约和确认",icon:CalendarCheck},{id:"language",title:"语言随行锦囊",icon:Translate},{id:"tips",title:"旅游贴士",icon:Compass}] as const;
type Tab=typeof tabs[number]["id"];
const noShopping=(text:string)=>!/购物|退税|伴手礼|纪念品|古玩/.test(text);
export function PreparationSections({trip}:{trip:Trip}) {
 if(trip.profile)return <ProfilePreparation profile={trip.profile}/>;
 return <LegacyPreparation trip={trip}/>;
}
function LegacyPreparation({trip}:{trip:Trip}) {
 const [tab,setTab]=useState<Tab>("packing");
 const [checked,setChecked]=useState<string[]>([]);
 const [loadedKey,setLoadedKey]=useState("");
 const key=`journey-preparation:${trip.id}`;
 useEffect(()=>{let saved:string[]=[];try{const value=JSON.parse(localStorage.getItem(key)??"[]");if(Array.isArray(value))saved=value.filter(item=>typeof item==="string");}catch{}setChecked(saved);setLoadedKey(key);},[key]);
 const toggle=(id:string)=>{const next=checked.includes(id)?checked.filter(item=>item!==id):[...checked,id];setChecked(next);try{localStorage.setItem(key,JSON.stringify(next));}catch{}};
 const packing=[{title:"证件与随身物品",items:["身份证与所需旅行证件","证件电子备份","手机、充电线与充电宝","饮水杯、纸巾与湿巾"]},{title:"衣物与日常用品",items:["舒适防滑的步行鞋","按逐日天气准备外套、雨具和防晒用品","个人常用药与必要的用药说明",...(trip.travelers.children?["儿童替换衣物与随身用品"]:[])]},...(trip.preparationItems??[]).filter(group=>noShopping(group.title)).map(group=>({...group,items:group.items.filter(item=>noShopping(item)&&!/预约|预订|确认|班次|闭馆/.test(item))})).filter(group=>group.items.length)];
 const bookings=[{title:"出发前 · 交通与住宿",items:["确认出发与返程日期、车次或航班","确认酒店地址、入住与退房时间","准备乘车码与离线地图"]},...trip.days.map(day=>({title:`Day ${day.dayNumber} · ${day.city}`,items:day.activities.filter(item=>item.kind==="attraction").map(item=>`核对${item.title}的开放时段与预约要求`)})).filter(group=>group.items.length),{title:"每日出门前",items:["查看当天预报与临时开放公告","核对餐厅营业时间","为返程交通预留缓冲时间"]}];
 const groups=tab==="packing"?packing:bookings;
 const ids=groups.flatMap(group=>group.items.map(item=>`${tab}:${group.title}:${item}`));
 const cities=cityNotes(trip);
 return <div className="preparation-layout"><nav className="preparation-menu" aria-label="行前准备栏目">{tabs.map(({id,title,icon:Icon},index)=><button key={id} aria-current={tab===id?"page":undefined} onClick={()=>setTab(id)}><small>0{index+1}</small><Icon size={21} weight="duotone"/><span>{title}</span></button>)}<p>出发前，翻一翻<br/>把安心装进行囊。</p></nav><section className="preparation-content" aria-label={tabs.find(item=>item.id===tab)?.title}>
 <header className="guide-section-heading"><div><span className="guide-eyebrow">旅途备忘录 / 0{tabs.findIndex(item=>item.id===tab)+1}</span><h2>{tabs.find(item=>item.id===tab)?.title}</h2></div>{(tab==="packing"||tab==="booking")&&<span>{ids.filter(id=>checked.includes(id)).length} / {ids.length} 已完成</span>}</header>
 {(tab==="packing"||tab==="booking")?<><p className="guide-muted">{tab==="packing"?"按需勾选随身物品，进度会保存在这台设备。":"按行程逐项核对；勾选仅记录你的确认进度，不会自动预约或购票。"}</p><div className="prep-grid">{groups.map((group,index)=><Ticket className="prep-card" key={`${group.title}-${index}`}><header><h2>{group.title}</h2></header><div>{group.items.map(item=>{const id=`${tab}:${group.title}:${item}`;return <label key={id} className={checked.includes(id)?"checked":""}><input type="checkbox" disabled={loadedKey!==key} checked={checked.includes(id)} onChange={()=>toggle(id)}/><span>{checked.includes(id)&&<Check size={14} weight="bold"/>}</span>{item}</label>;})}</div></Ticket>)}</div></>:tab==="language"?<><p className="guide-muted">地名、面型和当地叫法，随手翻开就能用。</p>{cities.map(({city,notes})=><details className="guide-fold" key={city} open><summary>{city} · 当地叫法<span>{notes.words.length} 条</span></summary><div className="guide-note-grid">{notes.words.length?notes.words.map(word=><div key={word.title}><h3>{word.title}</h3><p>{word.text}</p></div>):<p>当地词汇暂未收录。问路时出示完整地点名称与地图定位。</p>}</div></details>)}<details className="guide-fold"><summary>通用沟通锦囊<span>3 条</span></summary><div className="guide-note-grid"><p>问路：请问这个地点的入口在哪里？</p><p>点餐：请少辣，我有这些食物过敏，能否避开？</p><p>住宿：我有预订，可以先寄存行李吗？</p></div></details></>:<><p className="guide-muted">了解当地习惯，让每一次到访都多一分体谅。</p>{cities.map(({city,notes})=><details className="guide-fold" key={city} open><summary>{city} · 文化与礼仪</summary><div className="guide-note-grid">{notes.tips.length?notes.tips.map(note=><div key={note.title}><h3>{note.title}</h3><p>{note.text}</p></div>):<p>尊重当地着装与参观要求，拍摄居民或宗教活动前先征得同意。</p>}</div></details>)}<details className="guide-fold" open><summary>天气、交通与旅途提醒</summary><div className="guide-note-grid">{(trip.travelTips?.filter(noShopping).length?trip.travelTips.filter(noShopping):["每天出发前复核天气，按体力安排步行与休息。","出发前核对地图入口与交通时间，返程留出缓冲。"]).map((tip,index)=><p key={index}>{tip}</p>)}</div></details></>}
 </section></div>;
}
