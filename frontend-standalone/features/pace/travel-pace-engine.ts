import type { Activity, ActivityBlock, ActivityIntensity, TravelPace, TravelPaceProfile, TravelerConstraints, TripDay } from "@/types/trip";

export type DayType = "full" | "arrival" | "departure" | "transfer";

export interface WeatherPaceContext {
  condition: string;
  low: number;
  high: number;
}

export interface EffectivePacePolicy extends TravelPaceProfile {
  travelerConstraints: TravelerConstraints;
  weather: WeatherPaceContext;
}

export interface DayPaceMetrics {
  activityBlocks: ActivityBlock[];
  activeMinutes: number;
  activityLoad: number;
  transportLoad: number;
  dailyLoad: number;
  maxContinuousActivityMinutes: number;
  minimumObservedBufferMinutes?: number;
  lastActivityEnd?: string;
}

export interface PaceValidationIssue {
  code: "PACE_DENSITY" | "PACE_ACTIVE_HOURS" | "PACE_DAILY_LOAD" | "PACE_CONTINUOUS_ACTIVITY" | "PACE_BUFFER" | "PACE_LATE_END" | "PACE_WEATHER_CONFLICT" | "PACE_SPECIAL_DAY";
  message: string;
}

export const TRAVEL_PACE_PROFILES: Record<TravelPace, TravelPaceProfile> = {
  relaxed: {
    pace: "relaxed", targetActivityBlocks: { min: 2, max: 3 }, activeHours: { min: 5, max: 7 }, bufferMinutes: { min: 30, max: 60 }, mealMinutes: { min: 60, max: 90 },
    maxDailyLoad: 5, maxContinuousActivityMinutes: 180, preferMiddayBreak: true, allowEveningActivities: true, latestNormalEndTime: "20:00", endTimeIsSoftConstraint: true,
  },
  balanced: {
    pace: "balanced", targetActivityBlocks: { min: 3, max: 4 }, activeHours: { min: 7, max: 9 }, bufferMinutes: { min: 20, max: 40 }, mealMinutes: { min: 50, max: 75 },
    maxDailyLoad: 7, maxContinuousActivityMinutes: 240, preferMiddayBreak: false, allowEveningActivities: true, latestNormalEndTime: "21:00", endTimeIsSoftConstraint: true,
  },
  intensive: {
    pace: "intensive", targetActivityBlocks: { min: 4, max: 6 }, activeHours: { min: 9, max: 11 }, bufferMinutes: { min: 10, max: 25 }, mealMinutes: { min: 40, max: 60 },
    maxDailyLoad: 9, maxContinuousActivityMinutes: 300, preferMiddayBreak: false, allowEveningActivities: true, latestNormalEndTime: "22:00", endTimeIsSoftConstraint: true,
  },
};

export function normalizeTravelPace(value: unknown): TravelPace {
  if (value === "relaxed" || value === "intensive") return value;
  if (value === "packed") return "intensive";
  return "balanced";
}

export function deriveTravelerConstraints(travelers: { children: number; seniors: number }): TravelerConstraints {
  const hasChildren = travelers.children > 0;
  const hasElderly = travelers.seniors > 0;
  return {
    hasChildren,
    hasElderly,
    mobilityLevel: hasElderly ? "limited" : hasChildren ? "moderate" : "standard",
    needsFrequentBreaks: hasChildren || hasElderly,
  };
}

function clamp(value: number, minimum: number, maximum: number) { return Math.max(minimum, Math.min(maximum, value)); }
function minutes(value: string) { const [hour, minute] = value.split(":").map(Number); return hour * 60 + minute; }

export function getEffectivePacePolicy(paceValue: unknown, travelers: { children: number; seniors: number }, weather: WeatherPaceContext): EffectivePacePolicy {
  const pace = normalizeTravelPace(paceValue);
  const base = TRAVEL_PACE_PROFILES[pace];
  const travelerConstraints = deriveTravelerConstraints(travelers);
  const difficultWeather = /雨|雪|沙|霾|大风|强风|寒|冻|雷|暴/.test(weather.condition) || weather.high >= 32 || weather.low <= 0;
  const extraBuffer = (travelerConstraints.hasElderly ? 10 : 0) + (travelerConstraints.hasChildren ? 5 : 0) + (difficultWeather ? 10 : 0);
  const loadReduction = (travelerConstraints.hasElderly ? 1 : 0) + (travelerConstraints.hasChildren ? 0.5 : 0) + (difficultWeather ? 1 : 0);
  const blockReduction = Number(travelerConstraints.needsFrequentBreaks) + Number(difficultWeather);
  return {
    ...base,
    targetActivityBlocks: { min: Math.max(1, base.targetActivityBlocks.min - blockReduction), max: Math.max(2, base.targetActivityBlocks.max - blockReduction) },
    activeHours: { min: Math.max(3, base.activeHours.min - Number(difficultWeather)), max: Math.max(4, base.activeHours.max - Number(travelerConstraints.hasElderly) - Number(difficultWeather)) },
    bufferMinutes: { min: clamp(base.bufferMinutes.min + extraBuffer, 10, 75), max: clamp(base.bufferMinutes.max + extraBuffer, 25, 90) },
    mealMinutes: { min: base.mealMinutes.min, max: base.mealMinutes.max + (travelerConstraints.needsFrequentBreaks ? 10 : 0) },
    maxDailyLoad: Math.max(3, base.maxDailyLoad - loadReduction),
    maxContinuousActivityMinutes: Math.max(120, base.maxContinuousActivityMinutes - (travelerConstraints.hasElderly ? 45 : 0) - (difficultWeather ? 30 : 0)),
    travelerConstraints,
    weather,
  };
}

export function inferActivityIntensity(value: Pick<Activity, "title" | "kind" | "reason">): ActivityIntensity {
  const text = `${value.title} ${value.reason}`;
  if (value.kind === "meal" || /博物|展览|咖啡|茶馆|餐|演出|剧院|游船|邮轮|温泉/.test(text)) return 1;
  if (/登山|徒步|长城|爬山|穿越|骑行|攀岩|峡谷|高原/.test(text)) return 3;
  return 2;
}

export function getTransportLoad(durationMinutes: number, mode = ""): number {
  if (durationMinutes <= 0 || (/步行/.test(mode) && durationMinutes < 15)) return 0;
  if (durationMinutes < 30) return 0.25;
  if (durationMinutes < 60) return 0.5;
  if (durationMinutes <= 120) return 1;
  return 2;
}

export function calculateArrivalReadyMinute(input: { arrivalMinute: number; mode?: string; hotelRouteMinutes: number; checkInMinutes?: number; bufferMinutes: number }) {
  const baggageMinutes = /飞机|flight/i.test(input.mode ?? "") ? 45 : 20;
  return input.arrivalMinute + baggageMinutes + input.hotelRouteMinutes + (input.checkInMinutes ?? 30) + input.bufferMinutes;
}

export function calculateDepartureSightseeingCutoffMinute(input: { departureMinute: number; mode?: string; hotelRouteMinutes: number; luggageMinutes?: number; bufferMinutes: number }) {
  const safetyMinutes = /飞机|flight/i.test(input.mode ?? "") ? 120 : 60;
  return input.departureMinute - safetyMinutes - input.hotelRouteMinutes - (input.luggageMinutes ?? 30) - input.bufferMinutes;
}

function distanceKm(a: Activity, b: Activity) {
  const toRadians = (value: number) => value * Math.PI / 180;
  const latitude = toRadians(b.coord[0] - a.coord[0]);
  const longitude = toRadians(b.coord[1] - a.coord[1]);
  const x = Math.sin(latitude / 2) ** 2 + Math.cos(toRadians(a.coord[0])) * Math.cos(toRadians(b.coord[0])) * Math.sin(longitude / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
}

export function buildActivityBlocks(activities: Activity[]): ActivityBlock[] {
  const relevant = activities.filter((item) => item.kind === "attraction").sort((a, b) => a.start.localeCompare(b.start));
  const blocks: ActivityBlock[] = [];
  for (const activity of relevant) {
    const previous = relevant[relevant.indexOf(activity) - 1];
    const last = blocks.at(-1);
    const nearby = previous && last && distanceKm(previous, activity) <= 1.5 && (activity.transport?.durationMinutes ?? 999) <= 30 && minutes(activity.start) - minutes(previous.end) <= 90;
    const intensity = activity.intensity ?? inferActivityIntensity(activity);
    if (nearby) {
      last.activityIds.push(activity.id);
      last.title = `${last.title} · ${activity.title}`;
      last.intensity = Math.max(last.intensity, intensity) as ActivityIntensity;
    } else {
      const block = { id: `block-${activity.dayId}-${blocks.length + 1}`, title: activity.title, activityIds: [activity.id], intensity };
      blocks.push(block);
    }
  }
  return blocks;
}

export function calculateDayPaceMetrics(day: TripDay): DayPaceMetrics {
  const ordered = [...day.activities].sort((a, b) => a.start.localeCompare(b.start));
  const meaningful = ordered.filter((item) => item.kind === "attraction" || item.kind === "meal");
  const activityBlocks = buildActivityBlocks(ordered);
  const activityLoad = meaningful.reduce((sum, item) => sum + (item.intensity ?? inferActivityIntensity(item)), 0);
  const transportLoad = ordered.reduce((sum, item) => sum + getTransportLoad(item.transport?.durationMinutes ?? 0, item.transport?.mode), 0);
  const activeMinutes = meaningful.length ? minutes(meaningful.at(-1)!.end) - minutes(meaningful[0].start) : 0;
  let continuous = 0; let maximumContinuous = 0; let minimumBuffer: number | undefined;
  for (let index = 0; index < meaningful.length; index += 1) {
    const item = meaningful[index];
    const duration = Math.max(0, minutes(item.end) - minutes(item.start));
    const previous = meaningful[index - 1];
    const gap = previous ? minutes(item.start) - minutes(previous.end) : 0;
    if (previous) minimumBuffer = minimumBuffer === undefined ? gap : Math.min(minimumBuffer, gap);
    continuous = !previous || gap >= 45 ? duration : continuous + Math.max(0, gap) + duration;
    maximumContinuous = Math.max(maximumContinuous, continuous);
  }
  return { activityBlocks, activeMinutes, activityLoad, transportLoad, dailyLoad: activityLoad + transportLoad, maxContinuousActivityMinutes: maximumContinuous, minimumObservedBufferMinutes: minimumBuffer, lastActivityEnd: meaningful.at(-1)?.end };
}

function isMeaningfulEveningActivity(activity: Activity) { return /夜市|夜游|夜景|演出|音乐会|剧院|日落|夕阳|灯光|观星|晚餐/.test(`${activity.title} ${activity.reason}`); }

export function inferDayType(day: TripDay, index: number, totalDays: number): DayType {
  const hasArrival = day.activities.some((item) => item.kind === "transport" && minutes(item.end) <= 14 * 60);
  const hasDeparture = day.activities.some((item) => item.kind === "transport" && minutes(item.start) >= 12 * 60);
  if (index === totalDays - 1 || hasDeparture) return "departure";
  if (index === 0 || hasArrival) return index === 0 ? "arrival" : "transfer";
  return "full";
}

export function validateDayPace(day: TripDay, policy: EffectivePacePolicy, dayType: DayType): PaceValidationIssue[] {
  const metrics = calculateDayPaceMetrics(day);
  const issues: PaceValidationIssue[] = [];
  const isSpecialDay = dayType !== "full";
  if (!isSpecialDay && metrics.activityBlocks.length > policy.targetActivityBlocks.max) issues.push({ code: "PACE_DENSITY", message: `${day.date} 有 ${metrics.activityBlocks.length} 个主要体验块，高于${policy.pace}节奏建议的 ${policy.targetActivityBlocks.max} 个` });
  if (!isSpecialDay && metrics.activeMinutes > policy.activeHours.max * 60) issues.push({ code: "PACE_ACTIVE_HOURS", message: `${day.date} 活跃行程约 ${Math.round(metrics.activeMinutes / 60 * 10) / 10} 小时，超过当前节奏建议` });
  if (metrics.dailyLoad > policy.maxDailyLoad) issues.push({ code: "PACE_DAILY_LOAD", message: `${day.date} 综合体力与交通负荷 ${metrics.dailyLoad}，超过当前建议上限 ${policy.maxDailyLoad}` });
  if (metrics.maxContinuousActivityMinutes > policy.maxContinuousActivityMinutes) issues.push({ code: "PACE_CONTINUOUS_ACTIVITY", message: `${day.date} 连续活动时间过长，需要增加休息` });
  if (metrics.minimumObservedBufferMinutes !== undefined && metrics.minimumObservedBufferMinutes < policy.bufferMinutes.min) issues.push({ code: "PACE_BUFFER", message: `${day.date} 部分主要活动之间缓冲不足 ${policy.bufferMinutes.min} 分钟` });
  const lateActivities = day.activities.filter((item) => item.end > policy.latestNormalEndTime && !isMeaningfulEveningActivity(item) && item.kind !== "hotel" && item.kind !== "transport");
  if (lateActivities.length) issues.push({ code: "PACE_LATE_END", message: `${day.date} 的 ${lateActivities.map((item) => item.title).join("、")}晚于偏好结束时间；这是软提醒，不会阻止保存` });
  const difficultWeather = /雨|雪|沙|霾|大风|强风|寒|冻|雷|暴/.test(policy.weather.condition) || policy.weather.high >= 32;
  const weatherConflicts = difficultWeather && day.activities.some((item) => (item.intensity ?? inferActivityIntensity(item)) === 3 && item.kind === "attraction");
  if (weatherConflicts) issues.push({ code: "PACE_WEATHER_CONFLICT", message: `${day.date} 天气为${policy.weather.condition}，不宜安排高强度户外活动` });
  if (isSpecialDay && metrics.activityBlocks.length > policy.targetActivityBlocks.max) issues.push({ code: "PACE_SPECIAL_DAY", message: `${day.date} 是抵达、换城或返程日，主要活动数量偏多` });
  return issues;
}
