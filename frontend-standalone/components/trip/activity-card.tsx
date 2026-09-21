"use client";
/* eslint-disable react-hooks/refs -- dnd-kit exposes callback refs and reactive transform values by design. */

import { CSS } from "@dnd-kit/utilities";
import { useSortable } from "@dnd-kit/sortable";
import { ArrowsOutCardinal, Clock, Lock, LockOpen, PencilSimple, Trash } from "@phosphor-icons/react";
import clsx from "clsx";
import type { Activity } from "@/types/trip";

export function ActivityCard({ item, selected, onSelect, onEdit, onRemove, onToggleLock }: { item: Activity; selected: boolean; onSelect: () => void; onEdit: () => void; onRemove: () => void; onToggleLock: () => void }) {
  const sortable = useSortable({ id: item.id, disabled: item.locked, data: { dayId: item.dayId } });
  const style = { transform: CSS.Transform.toString(sortable.transform), transition: sortable.transition };
  return <article ref={sortable.setNodeRef} style={style} className={clsx("activity-card", selected && "selected", sortable.isDragging && "dragging", item.locked && "locked")} onClick={onSelect} data-activity-id={item.id}>
    <button className="drag-handle" onClick={(event) => event.stopPropagation()} aria-label={item.locked ? "锁定项目不能拖拽" : `拖拽${item.title}`} {...sortable.attributes} {...sortable.listeners} disabled={item.locked}><ArrowsOutCardinal size={17} /></button>
    <div className="activity-time"><strong>{item.start}</strong><span>{item.end}</span></div>
    <div className="activity-main"><div className="activity-title"><span className={`kind-mark kind-${item.kind}`} /> <h3><button className="activity-detail-link" onClick={(event) => { event.stopPropagation(); onSelect(); }}>{item.title}</button></h3></div><p>{item.reason}</p><div className="activity-meta"><span><Clock size={14} />{item.duration}</span>{item.mealRole&&item.mealWindow?<span title={item.mealTimingReason}>{({breakfast:"早餐",lunch:"午餐",dinner:"晚餐",snack:"小吃",cafe:"下午茶",flexible:"灵活用餐"} as const)[item.mealRole]} · {item.mealWindow.preferredStart}–{item.mealWindow.preferredEnd}</span>:null}<span className="activity-cost">{item.costStatus === "unknown" ? "费用待复核" : `￥${item.cost}`}</span></div></div>
    <div className="activity-actions"><button onClick={(event) => { event.stopPropagation(); onToggleLock(); }} aria-label={item.locked ? "解锁活动" : "锁定活动"}>{item.locked ? <Lock size={17} weight="fill" /> : <LockOpen size={17} />}</button><button onClick={(event) => { event.stopPropagation(); onEdit(); }} aria-label="编辑活动"><PencilSimple size={17} /></button><button onClick={(event) => { event.stopPropagation(); onRemove(); }} aria-label="删除活动" disabled={item.locked}><Trash size={17} /></button></div>
  </article>;
}

