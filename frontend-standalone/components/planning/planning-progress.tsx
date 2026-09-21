"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Circle, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { Button, Ticket } from "@/components/ui";
import { getJob, loadTrip, resumeBuild, type Job } from "@/lib/api";
import { useTripStore } from "@/store/trip-store";
const steps=["接收旅行偏好","整理地点资料","编排每日行程","完善体验与行前内容","校验完整档案","保存旅行档案"];
const stages:Record<string,number>={queued:0,starting:0,framing:0,"places-core":1,"places-experiences":1,"places-food":1,"itinerary-plan":2,itinerary:2,"itinerary-coherence":2,"modules-practical":3,"modules-language-notes":3,"destination-profile":4,offline:3,validating:4,done:5};
const errorTitles:Record<string,string>={provider:"模型服务响应异常",validation:"生成内容需要修复",persistence:"旅行档案保存异常",compiler:"旅行档案编译异常",worker_runtime:"任务运行状态异常",unknown_internal:"内部运行错误",configuration:"服务配置异常"};
export function PlanningProgress(){
 const [job,setJob]=useState<Job|null>(null); const [error,setError]=useState(""); const [connectionError,setConnectionError]=useState(false); const [pollRun,setPollRun]=useState(0); const router=useRouter(); const save=useTripStore(s=>s.saveGeneratedTrip);
 useEffect(()=>{
  const id=new URLSearchParams(window.location.search).get("job_id");
  if(!id){setError("没有任务编号，请从创建旅程页提交。");return;}
  const controller=new AbortController(); let timer:ReturnType<typeof setTimeout>;
  async function poll(){
   try{const status=await getJob(id!,controller.signal);if(controller.signal.aborted)return;setJob(status);setConnectionError(false);
    if(status.status==="failed"){setError(status.error?.message??"生成失败");return;}
    if(status.status==="paused"){setError(status.error?.message??"部分内容需要修复，任务已暂停");return;}
    if(status.status==="done"||status.status==="done_with_warnings"){const trip=await loadTrip(id!,controller.signal);if(!controller.signal.aborted)save(trip);return;}
    timer=setTimeout(()=>void poll(),1500);
   }catch(e){if(!controller.signal.aborted){setConnectionError(true);setError(e instanceof Error?e.message:"读取进度失败");}}
  }
  void poll();return()=>{controller.abort();clearTimeout(timer);};
 },[save,pollRun]);
 async function continueBuild(){if(!job)return;setError("");setConnectionError(false);try{await resumeBuild(job.job_id);setPollRun(x=>x+1);}catch(e){setError(e instanceof Error?e.message:"无法继续生成");}}
 const done=(job?.status==="done"||job?.status==="done_with_warnings")&&!error;const paused=job?.status==="paused";const active=stages[job?.stage??"queued"]??0;
 const count=job?.total_packs?`，资料包 ${job.completed_packs??0}/${job.total_packs}`:"";
 return <div className="planning-wrap"><div className="planning-copy"><p className="eyebrow">Planner in motion</p><h1 className="display-title">正在规划你的旅程</h1><p>根据你的偏好整理每日行程与七模块档案。营业、预约、费用与交通出发前请复核。</p></div><Ticket className="planning-ticket"><div className="planning-route-line" aria-hidden="true">{steps.map((_,i)=><span key={i} className={i<=active?"done":""}/>)}</div><ol>{steps.map((step,i)=><li key={step} className={done||i<active?"done":i===active?"active":"pending"}><span>{done||i<active?<Check size={18}/>:i===active?<SpinnerGap size={18} className={error?"":"spin"}/>:<Circle size={14}/>}</span><strong>{step}</strong><small>{done||i<active?"已完成":i===active?error?"需处理":"进行中":"等待"}</small></li>)}</ol>{error&&<div className="planning-error" role="alert"><WarningCircle size={20}/><span><b>{connectionError?"暂时无法读取进度":errorTitles[job?.error?.category??""]??"本次规划需要继续处理"}</b><small>{error}</small></span></div>}{job?.warnings.length?<div className="planning-warnings">{job.warnings.map((warning,index)=><p key={`${warning.code}-${warning.message}-${index}`}>{warning.message}</p>)}</div>:null}<div className="planning-footer"><span>{done?job?.degraded?"离线档案已完成并保存，出发前请复核":"档案已校验并保存":error?connectionError?"连接已中断，可重新连接":paused?"已通过的资料已保存，可从失败资料包继续":"任务无法恢复，请重新创建":`已处理 ${job?.progress??0}%${count}`}</span>{done&&<Button onClick={()=>router.push(`/trip/${job!.job_id}`)}>查看我的行程</Button>}{error&&(connectionError||paused)&&<Button onClick={()=>connectionError?setPollRun(x=>x+1):void continueBuild()}>{connectionError?"重新连接":"继续生成"}</Button>}{error&&<Button onClick={()=>router.push('/create')}>返回创建</Button>}</div></Ticket></div>;
}
