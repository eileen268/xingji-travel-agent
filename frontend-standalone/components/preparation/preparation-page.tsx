"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowLeft, Check, FirstAid, IdentificationCard, MapTrifold, Plug, ShirtFolded, Train, Umbrella } from "@phosphor-icons/react";
import { AppHeader, PaperPage } from "@/components/app-chrome";
import { Button, Ticket } from "@/components/ui";
import { useTripStore } from "@/store/trip-store";

const groups = [
  { title:"证件与票务", icon:IdentificationCard, items:["身份证和儿童证件","高铁班次信息","酒店预订确认","景点预约凭证"] },
  { title:"衣物用品", icon:ShirtFolded, items:["轻薄外套","舒适步行鞋","儿童替换衣物","薄款雨衣"] },
  { title:"健康与药品", icon:FirstAid, items:["常用药与过敏信息","儿童退热用品","创可贴","补水杯"] },
  { title:"电子设备", icon:Plug, items:["手机与充电线","充电宝","相机和存储卡","耳机"] },
  { title:"交通准备", icon:Train, items:["确认首站抵达方式","下载城市地图","检查公交乘车码","返程前预留进站时间"] },
  { title:"天气提醒", icon:Umbrella, items:["绍兴日准备雨具","午后阵雨时缩短游船","关注出发前 24 小时预报","晴天准备防晒"] },
];

export function PreparationPage({ tripId }: { tripId:string }) {
  const trip = useTripStore((state) => state.trips.find((item) => item.id === tripId) ?? state.trips[0]);
  const [checked,setChecked] = useState<string[]>(["身份证和儿童证件","高铁班次信息","舒适步行鞋","手机与充电线","充电宝"]);
  if (!trip) return null;
  const total = groups.flatMap((group) => group.items).length;
  return <><AppHeader /><PaperPage><div className="prep-wrap"><header className="subpage-heading"><div><Link href={`/trip/${trip.id}`} className="back-link"><ArrowLeft size={18}/>返回行程</Link><h1 className="display-title">行前准备清单</h1><p>{trip.destinations.join("、")}，根据天气、交通、人员与活动生成。</p></div><div className="prep-progress"><strong>{checked.length} / {total}</strong><span>已经准备</span></div></header><div className="prep-grid">{groups.map((group) => { const Icon=group.icon; return <Ticket className="prep-card" key={group.title}><header><Icon size={25} weight="duotone"/><h2>{group.title}</h2></header><div>{group.items.map((item) => <label key={item} className={checked.includes(item)?"checked":""}><input type="checkbox" checked={checked.includes(item)} onChange={() => setChecked((current) => current.includes(item)?current.filter((value)=>value!==item):[...current,item])}/><span>{checked.includes(item)&&<Check size={14} weight="bold"/>}</span>{item}</label>)}</div></Ticket>})}<Ticket className="prep-special"><MapTrifold size={28}/><div><h2>这趟旅程特别注意</h2><p>老人和儿童连续步行不超过 90 分钟。绍兴阵雨日保留室内备选；象山往返路程较长，晚间不再增加活动。</p></div></Ticket></div><footer className="subpage-footer"><Link href={`/trip/${trip.id}`}><Button variant="secondary">返回调整行程</Button></Link><Link href={`/trip/${trip.id}/export`}><Button>下一步：导出行程</Button></Link></footer></div></PaperPage></>;
}
