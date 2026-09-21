import { createMockTrip } from "@/mocks/trip";
import type { Activity, Budget, PlaceReference, Trip, TripDraft } from "@/types/trip";
import type { HotelOption, PlanningContext } from "@/types/ui-api";

type DemoState = { trips: Map<string, Trip>; snapshots: Map<string, Trip> };
const root = globalThis as typeof globalThis & { __journeyNotesDemo?: DemoState };
const state = root.__journeyNotesDemo ??= { trips: new Map(), snapshots: new Map() };

const cityCenters: Record<string, [number, number]> = {
  北京: [39.9042, 116.4074], 上海: [31.2304, 121.4737], 杭州: [30.2741, 120.1551],
  绍兴: [30.0303, 120.5802], 宁波: [29.8683, 121.544], 成都: [30.5728, 104.0668],
  兰州: [36.0611, 103.8343], 广州: [23.1291, 113.2644], 深圳: [22.5431, 114.0579],
  西安: [34.3416, 108.9398], 重庆: [29.563, 106.5516], 南京: [32.0603, 118.7969],
};

function center(city: string): [number, number] { return cityCenters[city] ?? [30.2741, 120.1551]; }
function clone<T>(value: T): T { return structuredClone(value); }
function current(id: string): Trip { return clone(state.trips.get(id) ?? createMockTrip(undefined, id)); }
function commit(trip: Trip) {
  const previous = state.trips.get(trip.id);
  if (previous) state.snapshots.set(trip.id, clone(previous));
  trip.updatedAt = new Date().toISOString();
  trip.revision = (previous?.revision ?? trip.revision ?? 0) + 1;
  state.trips.set(trip.id, clone(trip));
  return clone(trip);
}

export function demoHotels(city: string, keyword = "", source: "flyai" | "amap" = "flyai"): HotelOption[] {
  const [latitude, longitude] = center(city);
  const label = keyword.trim() || (source === "flyai" ? `${city}城市酒店` : `${city}自选住宿`);
  return [0, 1, 2].map((index) => ({
    id: `demo-${source}-${city}-${index + 1}`,
    name: index === 0 ? label : `${city}${["湖畔雅居", "城市客舍", "旅行者酒店"][index]}`,
    address: `${city}市中心演示地址 ${index + 1} 号`, city,
    latitude: latitude + index * 0.008, longitude: longitude + index * 0.009,
    score: source === "flyai" ? 4.8 - index * 0.2 : undefined,
    pricePerNightCny: source === "flyai" ? 680 + index * 120 : undefined,
    source,
  }));
}

export function prepareDemoContext(draft: TripDraft): PlanningContext {
  const destinations = draft.destinations.map((city, index) => {
    const main = city === draft.mainDestination;
    return {
      city,
      stay: { checkInDate: draft.startDate, checkOutDate: draft.endDate },
      hotels: demoHotels(city),
      planning: { id: `destination-${city}`, name: city, role: main ? "primary" as const : "secondary" as const, targetTimeShare: main ? 0.6 : 0.4 / Math.max(1, draft.destinations.length - 1), isAccommodationBase: true, priorityWeight: main ? 1 : 0.75 },
      accommodationBaseCity: city,
    };
  });
  const first = draft.destinations[0] ?? draft.mainDestination;
  const last = draft.destinations.at(-1) ?? draft.mainDestination;
  const location = (city: string, suffix: string, kind: PlaceReference["kind"]): PlaceReference => { const [latitude, longitude] = center(city); return { name: `${city}${suffix}`, city, latitude, longitude, kind }; };
  return {
    version: 1, createdAt: new Date().toISOString(), draft: clone(draft), destinations,
    destinationPlan: destinations.map((item) => item.planning),
    dayAssignments: draft.destinations,
    boundaryDefaults: {
      arrival: location(first, "站", "train_station"), departure: location(last, "站", "train_station"),
      intercity: draft.destinations.slice(1).map((city, index) => ({ id: `intercity-${index + 1}`, origin: draft.destinations[index], destination: city, travelDate: draft.startDate })),
    },
    warnings: [{ scope: "demo", message: "当前为前端独立演示包，查询和 Agent 结果均使用 Mock 数据。" }],
  };
}

export function generateDemoTrip(draft: TripDraft): Trip {
  const id = `trip-${Date.now()}`;
  const trip = createMockTrip(draft, id);
  trip.title = `${draft.destinations.join(" · ")}旅行计划（前端演示）`;
  trip.revision = 1;
  state.trips.set(id, clone(trip));
  return trip;
}

type ManualMutation =
  | { type: "reorder"; dayId: string; activityId: string; overActivityId: string }
  | { type: "move"; activityId: string; targetDayId: string; targetIndex?: number }
  | { type: "add"; targetDayId: string; activity: Omit<Activity, "id" | "dayId"> }
  | { type: "edit"; activityId: string; targetDayId: string; patch: Partial<Activity> }
  | { type: "delete"; activityId: string }
  | { type: "set_lock"; activityId: string; locked: boolean }
  | { type: "update_budget"; budget: Budget };

export function applyDemoMutation(tripId: string, mutation: ManualMutation): Trip {
  const trip = current(tripId);
  const findDay = (activityId: string) => trip.days.find((day) => day.activities.some((item) => item.id === activityId));
  if (mutation.type === "reorder") {
    const day = trip.days.find((item) => item.id === mutation.dayId);
    if (day) { const from = day.activities.findIndex((item) => item.id === mutation.activityId); const to = day.activities.findIndex((item) => item.id === mutation.overActivityId); if (from >= 0 && to >= 0) day.activities.splice(to, 0, ...day.activities.splice(from, 1)); }
  } else if (mutation.type === "move") {
    const source = findDay(mutation.activityId); const target = trip.days.find((day) => day.id === mutation.targetDayId);
    const index = source?.activities.findIndex((item) => item.id === mutation.activityId) ?? -1;
    if (source && target && index >= 0) { const [activity] = source.activities.splice(index, 1); activity.dayId = target.id; target.activities.splice(mutation.targetIndex ?? target.activities.length, 0, activity); }
  } else if (mutation.type === "add") {
    trip.days.find((day) => day.id === mutation.targetDayId)?.activities.push({ ...mutation.activity, id: `demo-activity-${Date.now()}`, dayId: mutation.targetDayId });
  } else if (mutation.type === "edit") {
    const source = findDay(mutation.activityId); const target = trip.days.find((day) => day.id === mutation.targetDayId);
    const item = source?.activities.find((activity) => activity.id === mutation.activityId);
    if (item) Object.assign(item, mutation.patch);
    if (source && target && item && source.id !== target.id) { source.activities = source.activities.filter((activity) => activity.id !== item.id); item.dayId = target.id; target.activities.push(item); }
  } else if (mutation.type === "delete") {
    const day = findDay(mutation.activityId); if (day) day.activities = day.activities.filter((item) => item.id !== mutation.activityId || item.locked);
  } else if (mutation.type === "set_lock") {
    const item = findDay(mutation.activityId)?.activities.find((activity) => activity.id === mutation.activityId); if (item) item.locked = mutation.locked;
  } else if (mutation.type === "update_budget") trip.budget = mutation.budget;
  return commit(trip);
}

export function syncDemoTrip(trip: Trip): Trip { state.trips.set(trip.id, clone(trip)); return clone(trip); }
export function undoDemoTrip(id: string): Trip { const snapshot = state.snapshots.get(id); if (!snapshot) return current(id); state.trips.set(id, clone(snapshot)); state.snapshots.delete(id); return clone(snapshot); }
export function getDemoTrip(id: string): Trip { return current(id); }
export function saveDemoTrip(trip: Trip): Trip { return commit(trip); }
