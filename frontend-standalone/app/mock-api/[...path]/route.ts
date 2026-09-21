import type { Trip, TripDay } from "@/types/trip";
import { applyDemoMutation, demoHotels, generateDemoTrip, getDemoTrip, prepareDemoContext, saveDemoTrip, syncDemoTrip, undoDemoTrip } from "@/lib/mock-backend";

type RouteContext = { params: Promise<{ path: string[] }> };
async function body(request: Request) { try { return await request.json() as Record<string, any>; } catch { return {}; } }
function json(value: unknown, status = 200) { return Response.json(value, { status }); }

async function handle(request: Request, context: RouteContext) {
  const path = (await context.params).path.join("/");
  const input = await body(request);
  if (path === "trips/prepare") return json(prepareDemoContext(input as never));
  if (path === "trips/generate") return json({ trip: generateDemoTrip(input.context.draft) });
  if (path === "trips/state") return json({ trip: request.method === "PUT" ? syncDemoTrip(input.trip as Trip) : getDemoTrip(String(input.tripId ?? "demo-trip")) });
  if (path === "trips/undo") return json({ trip: undoDemoTrip(String(input.tripId)) });
  if (path === "trips/recalculate") {
    const trip = applyDemoMutation(String(input.tripId), input.mutation);
    return json({ trip, impact: { affectedDays: trip.days.map((day) => day.id), updatedRoutes: ["已按演示路线重新连接相邻地点"], shiftedActivities: [], verifiedPlaces: [], warnings: ["前端包使用 Mock 路线时长"] } });
  }
  if (path === "trips/replan") {
    const trip = getDemoTrip(String(input.tripId));
    const days: TripDay[] = input.scope === "day" ? trip.days.filter((day) => day.id === input.dayId) : trip.days;
    days.forEach((day) => { const movable = day.activities.filter((item) => !item.locked && (item.kind === "attraction" || item.kind === "meal")); if (movable.length > 1) { const first = movable[0]; const second = movable[1]; const a = day.activities.indexOf(first); const b = day.activities.indexOf(second); [day.activities[a], day.activities[b]] = [day.activities[b], day.activities[a]]; } day.note = `已根据调整意见重新规划：${String(input.instruction || "优化节奏")}`; });
    return json({ trip: saveDemoTrip(trip) });
  }
  if (path === "trips/transport-impact") {
    const trip = getDemoTrip(String(input.tripId)); const segment = trip.transportSegments.find((item) => item.id === input.segmentId);
    const before = segment?.customTime ?? segment?.schedules.find((item) => item.id === segment.selectedScheduleId)?.departAt ?? "原时间";
    if (segment) { segment.selectionMode = input.mode; segment.selectedScheduleId = input.scheduleId; segment.customTime = input.customTime; }
    const after = input.customTime ?? segment?.schedules.find((item) => item.id === input.scheduleId)?.departAt ?? "新时间";
    const result = input.commit ? saveDemoTrip(trip) : trip;
    return json({ trip: result, impact: { before, after, segment: segment?.label ?? "交通段", affectedDays: [], updatedRoutes: ["相邻交通已按演示时长更新"], shiftedActivities: [], verifiedPlaces: [], warnings: ["此处为前端 Mock 影响预览"] } });
  }
  if (path === "trips/hotel-impact") {
    const trip = getDemoTrip(String(input.tripId)); const hotel = input.hotel; const previous = trip.hotelStays?.find((item) => item.city === input.city); const previousName = previous?.name ?? "原酒店";
    if (previous && hotel) { previous.name = hotel.name; previous.coord = [hotel.latitude, hotel.longitude]; previous.pricePerNight = hotel.pricePerNightCny ?? previous.pricePerNight; previous.source = hotel.source; }
    const result = input.commit ? saveDemoTrip(trip) : trip;
    return json({ trip: result, impact: { city: input.city, previousHotel: previousName, nextHotel: hotel?.name ?? "新酒店", nights: 2, budgetDelta: 0, routeChanges: [{ dayId: trip.days[0]?.id, dayLabel: `Day 1 ${input.city}`, leg: "酒店 → 首个活动", beforeMinutes: 20, afterMinutes: 26, deltaMinutes: 6 }], shiftedActivities: [], addedActivities: [], warnings: ["前端演示使用 Mock 酒店路线"] } });
  }
  if (path === "trips/advice") return json({ groups: [{ title: "证件与出行", items: ["身份证", "车票信息", "酒店确认单"] }, { title: "当地适配", items: ["舒适步行鞋", "雨具", "常用药品"] }], tips: ["每天出发前查看天气变化", "热门景点建议提前预约", "保留少量机动时间"] });
  if (path === "hotels/search" || path === "hotels/locate") return json({ items: demoHotels(String(input.destination ?? input.city ?? "杭州"), String(input.keyword ?? ""), path.endsWith("locate") ? "amap" : "flyai") });
  if (path === "maps/places") { const city = String(input.city ?? "杭州"); const [latitude, longitude] = ({ 北京:[39.9042,116.4074], 上海:[31.2304,121.4737], 杭州:[30.2741,120.1551], 兰州:[36.0611,103.8343] } as Record<string,[number,number]>)[city] ?? [30.2741,120.1551]; return json({ items: [{ name: String(input.keyword || `${city}站`), city, latitude, longitude, kind: /机场/.test(String(input.keyword)) ? "airport" : "train_station" }] }); }
  if (path === "maps/trip-routes") return json({ days: (input.days ?? []).map((day: { id:string; activities:Array<{id:string;coord:[number,number]}> }) => ({ dayId: day.id, totalDistanceMeters: 0, segments: day.activities.slice(1).map((item, index) => ({ fromId: day.activities[index].id, toId: item.id, polyline: [[day.activities[index].coord[1], day.activities[index].coord[0]], [item.coord[1], item.coord[0]]], verifiedRoute: false, distanceMeters: 0, durationMinutes: 20 })) })), provider: "mock" });
  return json({ message: "前端演示包未提供此接口" }, 404);
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
