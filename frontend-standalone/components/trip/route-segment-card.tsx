"use client";

import { ArrowDown, Warning } from "@phosphor-icons/react";
import { TransportModeIcon } from "@/components/transport-mode-icon";
import type { EditableTransportMode } from "@/lib/api";
import type { DayRouteSegment } from "@/types/trip";

const labels:Record<DayRouteSegment["mode"],string>={walking:"步行",public_transit:"公共交通",driving:"驾车或出租车"};

export function RouteSegmentCard({segment,disabled,onChange}:{segment:DayRouteSegment;disabled?:boolean;onChange:(mode:EditableTransportMode)=>void}) {
  const timing=segment.durationMinutes==null
    ? `交通时间待计算${segment.fallbackScheduleMinutes?` · 排程暂留 ${segment.fallbackScheduleMinutes} 分钟`:""}`
    : `${segment.durationMinutes} 分钟${segment.bufferMinutes?` · 缓冲 ${segment.bufferMinutes} 分钟`:""}`;
  return <div className="route-segment-card" aria-label={`${segment.fromName}到${segment.toName}的交通`}>
    <ArrowDown size={16}/><TransportModeIcon mode={segment.mode}/>
    <span><b>{segment.fromName} → {segment.toName}</b><small>{labels[segment.mode]} · {timing}</small></span>
    {segment.routeStatus==="unresolved"&&<Warning size={15} aria-label="路线待计算"/>}
    <select aria-label={`修改${segment.fromName}到${segment.toName}的交通方式`} value={segment.mode} disabled={disabled}
      onChange={event=>onChange(event.target.value as EditableTransportMode)}>
      <option value="walking">步行</option><option value="public_transit">公共交通</option><option value="driving">驾车或出租车</option>
    </select>
  </div>;
}
