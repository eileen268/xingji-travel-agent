"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { AppHeader, PaperPage } from "@/components/app-chrome";
import { Button, Ticket } from "@/components/ui";
import { Copy, DownloadSimple, FolderOpen, Plus, Trash } from "@phosphor-icons/react";
import { listTrips, type TripSummary } from "@/lib/api";
import { historySeed } from "@/mocks/trip";
export function TripHistory(){
 const [trips,setTrips]=useState<TripSummary[]>([]); const [loading,setLoading]=useState(true); const [message,setMessage]=useState('');
 useEffect(()=>{const controller=new AbortController(); listTrips(controller.signal).then(r=>setTrips(r.items)).catch(e=>{if(!controller.signal.aborted)setMessage(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});return()=>controller.abort();},[]);
 const items=[...trips,historySeed[0]];
 const later=()=>setMessage('后续开放：本轮支持生成与阅读');
 return <><AppHeader/><PaperPage><div className="history-wrap"><header className="history-heading"><div><p className="eyebrow">你的旅行档案</p><h1 className="display-title">我的行程</h1><p>新建档案保存在本机后端，示例行程可随时打开体验。</p></div><Link href="/create"><Button><Plus size={18}/>创建新旅程</Button></Link></header>{message&&<p className="field-error" role="status">{message}</p>}{loading&&<p role="status">正在读取旅行档案…</p>}{!loading&&trips.length===0&&<Ticket className="history-empty"><FolderOpen size={44}/><h2>还没有新建的旅程</h2><p>创建第一份行程后，它会出现在这里。</p></Ticket>}<div className="history-list">{items.map(trip=><Ticket className="history-ticket" key={trip.id}><div className="history-photo" style={{backgroundImage:"url('/destinations/西湖.png')"}}/><div className="history-main"><span className="status status-completed">{trip.id==='demo-trip'?'内置示例':'已完成'}</span><h2>{trip.title}</h2><p>{trip.startDate} 至 {trip.endDate}</p><small>{trip.travelers.adults+trip.travelers.children+trip.travelers.seniors} 人，预算 ￥{trip.budget.total.toLocaleString()}，最近修改 {new Date(trip.updatedAt).toLocaleDateString('zh-CN')}</small></div><div className="history-actions"><Link className="button button-primary" href={`/trip/${trip.id}`}>打开</Link><button onClick={later}><Copy size={17}/>复制</button>{trip.id==='demo-trip'?<Link href={`/trip/${trip.id}/export`}><DownloadSimple size={17}/>导出</Link>:<button onClick={later}><DownloadSimple size={17}/>导出</button>}<button onClick={later} className="danger-link"><Trash size={17}/>删除</button></div></Ticket>)}</div></div></PaperPage></>;
}
