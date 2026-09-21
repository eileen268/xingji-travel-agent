"use client";
import { buildTrip } from "@/lib/api";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { CalendarBlank, MapPin, Plus, SlidersHorizontal, Trash, UsersThree, Wallet } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "motion/react";
import { useTripStore } from "@/store/trip-store";
import type { TripPreferences } from "@/types/trip";
import { Button, Field, Segmented, Ticket } from "@/components/ui";
import { LocationSearch } from "@/components/create/location-search";
import { TRAVEL_PACE_PROFILES } from "@/features/pace/travel-pace-engine";

const interestOptions = [
  { value: "经典景点", label: "经典景点" }, { value: "美食", label: "美食" },
  { value: "自然风景", label: "自然风景" }, { value: "人文古迹", label: "历史文化" },
  { value: "城市漫游", label: "城市漫游" }, { value: "摄影", label: "拍照与氛围" },
  { value: "特色体验", label: "特色体验" }, { value: "海岛与沙滩", label: "海岛与沙滩" },
  { value: "夜生活", label: "夜生活" }, { value: "亲子", label: "亲子" },
  { value: "轻户外", label: "户外运动" }, { value: "咖啡与甜品", label: "咖啡与甜品" },
  { value: "设计与展览", label: "设计与展览" }, { value: "影视与流行文化", label: "影视与流行文化" },
];
const budgetLevels: { value: NonNullable<TripPreferences["budgetLevel"]>; label: string }[] = [
  { value: "agent", label: "由 Agent 给出主流预算" }, { value: "economy", label: "经济实用" },
  { value: "comfort", label: "舒适型" }, { value: "quality", label: "品质型" }, { value: "premium", label: "高端型" },
];

export function TripCreator() {
  const router = useRouter();
  const reduce = useReducedMotion();
  const draft = useTripStore((state) => state.draft);
  const updateDraft = useTripStore((state) => state.updateDraft);
  const updatePreferences = useTripStore((state) => state.updateDraftPreferences);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [attempted, setAttempted] = useState(false);
  const locationExclusions = useMemo(() => [draft.origin, ...draft.destinations], [draft.origin, draft.destinations]);
  const [addingDestination, setAddingDestination] = useState(draft.destinations.length === 0);
  const invalidDate = !draft.startDate || !draft.endDate || draft.startDate > draft.endDate;
  const canSubmit = draft.origin.trim().length > 0 && draft.destinations.length > 0 && !invalidDate;

  const errors = useMemo(() => ({
    destination: attempted && draft.destinations.length === 0 ? "请至少填写一个目的地" : undefined,
    date: attempted && invalidDate ? "正式规划必须填写有效的开始与结束日期" : undefined,
  }), [attempted, draft.destinations.length, invalidDate]);

  const toggleInterest = (value: string) => {
    const interests = draft.preferences.interests.includes(value) ? draft.preferences.interests.filter((item) => item !== value) : [...draft.preferences.interests, value];
    updatePreferences({ interests });
  };
  const submit = async () => {
    setAttempted(true);
    if (!canSubmit || submitting) return;
    setSubmitting(true); setSubmitError("");
    try { const result = await buildTrip(draft); router.push(`/planning?job_id=${result.job_id}`); }
    catch(error) { setSubmitError(error instanceof Error ? error.message : "提交失败"); setSubmitting(false); }
  };

  return (
    <div className="create-wrap">
      <header className="create-heading">
        <p className="eyebrow">1–3 分钟旅行需求问卷</p>
        <h1 className="display-title">创建你的旅程</h1>
        <p>先填写核心信息，偏好与特殊需求都可以留空，由 Agent 使用稳妥的主流方案。</p>
      </header>

      <div className="ticket-route" aria-label="根据目的地动态生成的路线摘要">
        {draft.destinations.map((city, index) => <motion.div key={city} className={`route-stop ${city === draft.mainDestination ? "major" : ""}`} initial={reduce ? false : { opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * .08 }}><span>{index === 0 ? "首站" : index === draft.destinations.length - 1 ? "末站" : "途经"}</span><strong>{city}</strong>{city === draft.mainDestination && <small>主要目的地</small>}</motion.div>)}
      </div>

      <section className="create-grid">
        <Ticket className="create-card card-route">
          <div className="card-heading"><MapPin size={24} weight="duotone" /><div><h2>从哪里出发，想去哪里</h2><p>从中国大陆固定城市库选择，支持多城市串联</p></div></div>
          <LocationSearch label="出发地" value={draft.origin} placeholder="搜索省份或城市，例如：浙江、杭州" onSelect={(origin) => updateDraft({ origin })}/>
          <div className="destination-builder">
            <div className="destination-builder-label"><span>目的地</span><small>逐个搜索并用 ＋ 串联，最多 8 座城市</small></div>
            <div className="destination-chain">{draft.destinations.map((city, index) => <div className="destination-chip" key={`${city}-${index}`}><span>{index + 1}</span><b>{city}</b><button type="button" aria-label={`移除${city}`} onClick={() => { const destinations = draft.destinations.filter((_, itemIndex) => itemIndex !== index); updateDraft({ destinations, mainDestination: destinations.includes(draft.mainDestination) ? draft.mainDestination : destinations[0] ?? "" }); }}><Trash size={15}/></button></div>)}{draft.destinations.length < 8 && <button type="button" className="destination-add" onClick={() => setAddingDestination(true)}><Plus size={17}/>添加一站</button>}</div>
            {addingDestination && draft.destinations.length < 8 && <div className="destination-search-row"><LocationSearch label="搜索下一站" placeholder="例如：杭州、绍兴、宁波" exclude={locationExclusions} onSelect={(city) => { if (!city) return; const destinations = [...draft.destinations, city]; updateDraft({ destinations, mainDestination: draft.mainDestination || city }); setAddingDestination(false); }}/><button type="button" onClick={() => setAddingDestination(false)}>取消</button></div>}
            {errors.destination && <p className="form-error">{errors.destination}</p>}
          </div>
          <Field label="主要目的地"><select value={draft.mainDestination} onChange={(event) => updateDraft({ mainDestination: event.target.value })}>{draft.destinations.map((city) => <option key={city}>{city}</option>)}</select></Field>
        </Ticket>

        <Ticket className="create-card card-date">
          <div className="card-heading"><CalendarBlank size={24} weight="duotone" /><div><h2>什么时候出发</h2><p>日期决定天气与每日可用时间</p></div></div>
          <div className="two-fields"><Field label="开始日期"><input type="date" value={draft.startDate} onChange={(event) => updateDraft({ startDate: event.target.value })} /></Field><Field label="结束日期" error={errors.date}><input type="date" value={draft.endDate} onChange={(event) => updateDraft({ endDate: event.target.value })} /></Field></div>
        </Ticket>

        <Ticket className="create-card card-people">
          <div className="card-heading"><UsersThree size={24} weight="duotone" /><div><h2>和谁一起</h2><p>人数与关系会改变节奏和推荐</p></div></div>
          <div className="count-grid">{(["adults", "children", "seniors"] as const).map((key) => <Field key={key} label={{ adults: "成人", children: "儿童", seniors: "老人" }[key]}><input type="number" min={0} max={12} value={draft[key]} onChange={(event) => updateDraft({ [key]: Number(event.target.value) })} /></Field>)}</div>
          <Field label="同行关系"><select value={draft.travelStyle} onChange={(event) => updateDraft({ travelStyle: event.target.value })}><option>普通同行</option><option>情侣或夫妻</option><option>朋友</option><option>家庭旅行</option><option>亲子</option><option>带长辈</option><option>独自旅行</option><option>其他</option></select></Field>
        </Ticket>

        <Ticket className="create-card card-budget">
          <div className="card-heading"><Wallet size={24} weight="duotone" /><div><h2>大致预算</h2><p>只需给出整趟定位，无需拆分类目</p></div></div>
          <Field label="预算定位"><select value={draft.preferences.budgetLevel ?? "agent"} onChange={(event) => updatePreferences({ budgetLevel: event.target.value as NonNullable<TripPreferences["budgetLevel"]> })}>{budgetLevels.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></Field>
          <Field label="整趟总预算（可选）"><div className="money-input"><span>￥</span><input type="number" min={0} step={500} value={draft.budget.total || ""} placeholder="留空则按预算定位安排" onChange={(event) => updateDraft({ budget: { ...draft.budget, total: Number(event.target.value) || 0 } })} /></div></Field>
        </Ticket>

        <Ticket className="create-card card-preferences">
          <div className="card-heading"><SlidersHorizontal size={24} weight="duotone" /><div><h2>最想把时间留给什么</h2><p>可选，建议选择 3–5 项</p></div></div>
          <div className="interest-grid">{interestOptions.map((option) => <button type="button" key={option.value} className={draft.preferences.interests.includes(option.value) ? "selected" : ""} onClick={() => toggleInterest(option.value)}>{option.label}</button>)}</div>
          <Field label="旅行节奏"><Segmented ariaLabel="旅行节奏" value={draft.preferences.pace} onChange={(pace) => updatePreferences({ pace })} options={[{ value: "relaxed", label: "轻松" }, { value: "balanced", label: "均衡" }, { value: "intensive", label: "充实" }]} /><p className="field-hint">未特别选择时按均衡偏轻松处理。{{ relaxed: "慢慢走，留更多自由时间", balanced: "兼顾体验与休息", intensive: "充分利用旅行时间" }[draft.preferences.pace]} · 通常活动至 {TRAVEL_PACE_PROFILES[draft.preferences.pace].latestNormalEndTime}</p></Field>
        </Ticket>

        <Ticket className="create-card card-transport">
          <div className="card-heading"><MapPin size={24} weight="duotone" /><div><h2>想去与不想去</h2><p>让地点筛选真正遵循你的取舍</p></div></div>
          <Field label="特别想去"><textarea value={draft.preferences.mustGo ?? ""} placeholder="地点、餐厅、活动都可以；留空则安排主流经典" onChange={(event) => updatePreferences({ mustGo: event.target.value })} /></Field>
          <Field label="明确不想去"><textarea value={draft.preferences.avoid ?? ""} placeholder="例如：不去主题乐园、不安排长途徒步" onChange={(event) => updatePreferences({ avoid: event.target.value })} /></Field>
        </Ticket>

        <Ticket className="create-card card-boundary">
          <div className="card-heading"><UsersThree size={24} weight="duotone" /><div><h2>需要特别照顾的地方</h2><p>健康、饮食、行动或时间限制</p></div></div>
          <Field label="限制与照顾"><textarea value={draft.preferences.constraints ?? ""} placeholder="例如：不喜欢早起、控制步行量、海鲜过敏、需要无障碍路线" onChange={(event) => updatePreferences({ constraints: event.target.value })} /></Field>
        </Ticket>

        <Ticket className="create-card card-notes">
          <div className="card-heading"><SlidersHorizontal size={24} weight="duotone" /><div><h2>其他想告诉 Agent 的内容</h2><p>没有补充也不影响生成</p></div></div>
          <Field label="其他说明"><textarea value={draft.notes} placeholder="例如：第一次去，重要景点不要遗漏；餐厅不要只推荐网红店" onChange={(event) => updateDraft({ notes: event.target.value })} /></Field>
          <p className="field-hint">已订交通或住宿可在这里写订单信息；未提供时保持待确认，不影响旅行档案生成。</p>
        </Ticket>
      </section>

      <footer className="create-submit">
        <div><strong>{draft.origin} 至 {draft.destinations.join("、")}</strong><span>{draft.startDate} 至 {draft.endDate}，预算 ￥{draft.budget.total.toLocaleString()}</span></div>
        {submitError && <p className="form-error" role="alert">{submitError}</p>}<Button disabled={submitting} onClick={()=>void submit()}>{submitting?"提交中…":"开始规划"}</Button>
      </footer>
    </div>
  );
}
