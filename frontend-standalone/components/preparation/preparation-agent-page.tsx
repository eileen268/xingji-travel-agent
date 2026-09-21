"use client";
import { loadTrip, legacyFetch } from "@/lib/api";

import Link from "next/link";
import { PreparationSections } from "@/components/guide/preparation-sections";
import { useEffect, useState } from "react";
import { ArrowLeft, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { AppHeader, PaperPage } from "@/components/app-chrome";
import { Button } from "@/components/ui";
import { useTripStore } from "@/store/trip-store";

export function PreparationAgentPage({tripId}:{tripId:string}){
 if(tripId!=="demo-trip")return <RemotePreparation tripId={tripId}/>;
 return <LegacyPreparationPage tripId={tripId}/>;
}
function LegacyPreparationPage({ tripId }: { tripId:string }) {
  const trip = useTripStore((state) => state.trips.find((item) => item.id === tripId) ?? state.trips[0]);
  const applyTravelAdvice = useTripStore((state) => state.applyTravelAdvice);
  const [loading,setLoading] = useState(() => Boolean(trip && !trip.preparationItems));
  const [error,setError] = useState("");
  const generate = async () => {
    if (!trip || loading) return;
    setLoading(true); setError("");
    try {
      const response = await legacyFetch("/api/trips/advice", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({trip}) });
      const payload = await response.json() as {groups?:Array<{title:string;items:string[]}>;tips?:string[];message?:string};
      if(!response.ok||!payload.groups||!payload.tips) throw new Error(payload.message||"生成失败");
      applyTravelAdvice(trip.id,payload.groups,payload.tips);
    } catch(reason) { setError(reason instanceof Error?reason.message:"地方特色提醒生成失败"); }
    finally { setLoading(false); }
  };
  useEffect(() => {
    if (!trip || trip.preparationItems) { setLoading(false); return; }
    setLoading(true);
    const controller = new AbortController();
    void legacyFetch("/api/trips/advice", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({trip}), signal:controller.signal })
      .then(async(response) => { const payload=await response.json() as {groups?:Array<{title:string;items:string[]}>;tips?:string[];message?:string}; if(!response.ok||!payload.groups||!payload.tips) throw new Error(payload.message||"生成失败"); return payload; })
      .then((payload) => applyTravelAdvice(trip.id,payload.groups!,payload.tips!))
      .catch((reason) => { if(reason instanceof Error&&reason.name!=="AbortError") setError(reason.message); })
      .finally(() => { if(!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [applyTravelAdvice, trip]);
  if (!trip) return null;
  return <><AppHeader /><PaperPage><div className="prep-wrap"><header className="subpage-heading"><div><Link href={`/trip/${trip.id}`} className="back-link"><ArrowLeft size={18}/>返回行程</Link><h1 className="display-title">行前准备</h1><p>{trip.destinations.join("、")}，基础清单之外，Agent 会结合当地特征、天气、同行人与活动补充提醒。</p></div></header>
    {loading&&<div className="prep-agent-status"><SpinnerGap className="spin" size={20}/><span><b>Agent 正在分析当地特征</b><small>只根据当前行程和已查询天气生成，不虚构实时风险</small></span></div>}
    {error&&<div className="prep-agent-status error"><WarningCircle size={20}/><span><b>{error}</b><small>基础清单仍可使用</small></span><Button variant="secondary" onClick={()=>void generate()}>重试</Button></div>}
    <PreparationSections trip={trip} />
    <footer className="subpage-footer"><Button variant="secondary" onClick={()=>void generate()} disabled={loading}>{loading?"生成中…":"重新生成地方提醒"}</Button><Link href={`/trip/${trip.id}`}><Button variant="secondary">返回调整行程</Button></Link><Link href={`/trip/${trip.id}/export`}><Button>下一步：导出行程</Button></Link></footer></div></PaperPage></>;
}




function RemotePreparation({tripId}:{tripId:string}){
 const [trip,setTrip]=useState<import('@/types/trip').Trip|null>(null);const [message,setMessage]=useState('');
 useEffect(()=>{const c=new AbortController();loadTrip(tripId,c.signal).then(setTrip).catch(e=>{if(!c.signal.aborted)setMessage(e.message);});return()=>c.abort();},[tripId]);
 return <><AppHeader/><PaperPage><div className="prep-wrap"><header className="subpage-heading"><div><Link href={`/trip/${tripId}`} className="back-link"><ArrowLeft size={18}/>返回行程</Link><h1 className="display-title">行前准备</h1><p>按当前旅行档案逐项准备，开放与预约出发前请复核。</p></div></header>{message&&<div className="prep-agent-status error" role="status">{message}</div>}{trip?<PreparationSections trip={trip}/>:<p>正在读取旅行档案…</p>}<footer className="subpage-footer"><Button variant="secondary" onClick={()=>setMessage('后续开放：重新生成地方提醒')}>重新生成地方提醒</Button><Link href={`/trip/${tripId}`}><Button variant="secondary">返回行程</Button></Link><Button onClick={()=>setMessage('后续开放：导出行程')}>下一步：导出行程</Button></footer></div></PaperPage></>;
}
