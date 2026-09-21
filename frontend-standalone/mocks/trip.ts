import type { Activity, Budget, Trip, TripDay, TripDraft, TransportSchedule } from "@/types/trip";

const activity = (value: Activity): Activity => value;

export const outboundSchedules: TransportSchedule[] = [
  { id: "out-g7501", code: "G7501", mode: "高铁", origin: "上海虹桥", destination: "杭州东", departAt: "07:00", arriveAt: "07:45", duration: "45 分钟", price: 73, recommended: true },
  { id: "out-g7313", code: "G7313", mode: "高铁", origin: "上海虹桥", destination: "杭州东", departAt: "08:30", arriveAt: "09:16", duration: "46 分钟", price: 73 },
  { id: "out-g7549", code: "G7549", mode: "高铁", origin: "上海虹桥", destination: "杭州东", departAt: "10:05", arriveAt: "10:52", duration: "47 分钟", price: 73 },
];

export const returnSchedules: TransportSchedule[] = [
  { id: "ret-g7542", code: "G7542", mode: "高铁", origin: "宁波", destination: "上海虹桥", departAt: "16:12", arriveAt: "18:10", duration: "1 小时 58 分钟", price: 144 },
  { id: "ret-g7516", code: "G7516", mode: "高铁", origin: "宁波", destination: "上海虹桥", departAt: "18:28", arriveAt: "20:20", duration: "1 小时 52 分钟", price: 144, recommended: true },
  { id: "ret-d3102", code: "D3102", mode: "火车", origin: "宁波", destination: "上海虹桥", departAt: "20:02", arriveAt: "22:15", duration: "2 小时 13 分钟", price: 116 },
];

export const defaultBudget: Budget = {
  total: 12000,
  accommodation: 3900,
  food: 2200,
  transportation: 2000,
  activities: 1300,
  shopping: 700,
  other: 280,
};

const days: TripDay[] = [
  {
    id: "day-1", dayNumber: 1, date: "2026-10-02", city: "杭州", routeColor: "#a84e42",
    weather: { icon: "sun", label: "晴", low: 18, high: 26 },
    note: "上午抵达后先寄存行李，西湖段以步行和短途打车为主。",
    activities: [
      activity({ id: "a-101", dayId: "day-1", title: "抵达杭州东站", city: "杭州", kind: "transport", start: "07:45", end: "08:10", duration: "25 分钟", cost: 73, reason: "早到可以避开午间客流，为第一天保留完整游览时间。", coord: [30.293, 120.211], locked: true, transport: { mode: "高铁", durationMinutes: 45, distanceKm: 169, note: "G7501" } }),
      activity({ id: "a-102", dayId: "day-1", title: "西湖环湖慢行", city: "杭州", kind: "attraction", start: "09:30", end: "11:30", duration: "2 小时", cost: 0, reason: "清晨光线柔和，适合亲子步行与拍照。", coord: [30.253, 120.148], locked: false, transport: { mode: "打车", durationMinutes: 24, distanceKm: 9.1 } }),
      activity({ id: "a-103", dayId: "day-1", title: "楼外楼午餐", city: "杭州", kind: "meal", start: "12:00", end: "13:15", duration: "1 小时 15 分钟", cost: 320, reason: "靠近西湖，减少老人和儿童的折返。", coord: [30.254, 120.142], locked: false, transport: { mode: "步行", durationMinutes: 8, distanceKm: 0.6 } }),
      activity({ id: "a-104", dayId: "day-1", title: "灵隐寺", city: "杭州", kind: "attraction", start: "14:30", end: "17:00", duration: "2 小时 30 分钟", cost: 150, reason: "午后树荫较多，节奏舒缓。", coord: [30.241, 120.102], locked: false, transport: { mode: "打车", durationMinutes: 22, distanceKm: 7.3 } }),
      activity({ id: "a-105", dayId: "day-1", title: "西湖畔酒店 Check-in", city: "杭州", kind: "hotel", start: "17:30", end: "18:00", duration: "30 分钟", cost: 880, reason: "酒店位置靠近日行程中心，次日出发更省时。", coord: [30.247, 120.151], locked: false, transport: { mode: "打车", durationMinutes: 18, distanceKm: 6.2 } }),
    ],
  },
  {
    id: "day-2", dayNumber: 2, date: "2026-10-03", city: "绍兴", routeColor: "#d2852c",
    weather: { icon: "cloud-rain", label: "阵雨", low: 17, high: 23 },
    note: "午后可能有阵雨，户外河坊段提前，室内黄酒博物馆作为备选。",
    activities: [
      activity({ id: "a-201", dayId: "day-2", title: "杭州东至绍兴北", city: "绍兴", kind: "transport", start: "08:05", end: "08:24", duration: "19 分钟", cost: 30, reason: "Agent 推荐的早间城际高铁，减少换乘等待。", coord: [30.049, 120.535], locked: true, transport: { mode: "高铁", durationMinutes: 19, distanceKm: 56, note: "G7651" } }),
      activity({ id: "a-202", dayId: "day-2", title: "鲁迅故里", city: "绍兴", kind: "attraction", start: "09:20", end: "11:20", duration: "2 小时", cost: 0, reason: "上午人流相对较少，适合慢慢参观。", coord: [29.995, 120.582], locked: false, transport: { mode: "公交", durationMinutes: 38, distanceKm: 15.2 } }),
      activity({ id: "a-203", dayId: "day-2", title: "仓桥直街午餐", city: "绍兴", kind: "meal", start: "11:50", end: "13:10", duration: "1 小时 20 分钟", cost: 240, reason: "品尝绍兴菜，并留出足够午休时间。", coord: [30.006, 120.574], locked: false, transport: { mode: "步行", durationMinutes: 16, distanceKm: 1.2 } }),
      activity({ id: "a-204", dayId: "day-2", title: "沈园与乌篷船", city: "绍兴", kind: "attraction", start: "14:00", end: "16:30", duration: "2 小时 30 分钟", cost: 180, reason: "路线紧凑，雨势增大时可缩短游船。", coord: [29.992, 120.589], locked: false, transport: { mode: "公交", durationMinutes: 15, distanceKm: 2.1 } }),
      activity({ id: "a-205", dayId: "day-2", title: "绍兴古城酒店 Check-in", city: "绍兴", kind: "hotel", start: "17:10", end: "17:40", duration: "30 分钟", cost: 760, reason: "位于古城内，晚餐后可步行返回。", coord: [30.002, 120.581], locked: false, transport: { mode: "打车", durationMinutes: 12, distanceKm: 3.4 } }),
    ],
  },
  {
    id: "day-3", dayNumber: 3, date: "2026-10-04", city: "宁波", routeColor: "#4e7e4e",
    weather: { icon: "cloud-sun", label: "多云", low: 18, high: 25 },
    note: "城际抵达后先办理行李寄存，下午步行串联老外滩。",
    activities: [
      activity({ id: "a-301", dayId: "day-3", title: "绍兴北至宁波", city: "宁波", kind: "transport", start: "08:32", end: "09:18", duration: "46 分钟", cost: 48, reason: "Agent 推荐直达班次，时间和价格均衡。", coord: [29.866, 121.539], locked: true, transport: { mode: "高铁", durationMinutes: 46, distanceKm: 118, note: "G7545" } }),
      activity({ id: "a-302", dayId: "day-3", title: "天一阁", city: "宁波", kind: "attraction", start: "10:10", end: "12:00", duration: "1 小时 50 分钟", cost: 60, reason: "抵达后顺路参观，午前庭院体验更舒适。", coord: [29.871, 121.542], locked: false, transport: { mode: "地铁", durationMinutes: 18, distanceKm: 3.1 } }),
      activity({ id: "a-303", dayId: "day-3", title: "鼓楼沿线午餐", city: "宁波", kind: "meal", start: "12:20", end: "13:40", duration: "1 小时 20 分钟", cost: 260, reason: "就近安排宁波小吃，减少额外移动。", coord: [29.875, 121.551], locked: false, transport: { mode: "步行", durationMinutes: 12, distanceKm: 0.9 } }),
      activity({ id: "a-304", dayId: "day-3", title: "老外滩", city: "宁波", kind: "attraction", start: "15:00", end: "17:30", duration: "2 小时 30 分钟", cost: 0, reason: "傍晚光线适合拍照，沿江散步节奏轻松。", coord: [29.884, 121.566], locked: false, transport: { mode: "地铁", durationMinutes: 20, distanceKm: 3.8 } }),
      activity({ id: "a-305", dayId: "day-3", title: "三江口酒店 Check-in", city: "宁波", kind: "hotel", start: "18:00", end: "18:30", duration: "30 分钟", cost: 820, reason: "靠近老外滩与地铁，方便末两日活动。", coord: [29.881, 121.563], locked: false, transport: { mode: "步行", durationMinutes: 9, distanceKm: 0.7 } }),
    ],
  },
  {
    id: "day-4", dayNumber: 4, date: "2026-10-05", city: "象山", routeColor: "#4779a8",
    weather: { icon: "sun", label: "晴", low: 19, high: 24 },
    note: "往返距离较长，采用包车并减少晚间活动。",
    activities: [
      activity({ id: "a-401", dayId: "day-4", title: "酒店至象山影视城", city: "象山", kind: "transport", start: "08:00", end: "09:35", duration: "1 小时 35 分钟", cost: 460, reason: "家庭同行采用包车，减少换乘。", coord: [29.354, 121.864], locked: false, transport: { mode: "包车", durationMinutes: 95, distanceKm: 82 } }),
      activity({ id: "a-402", dayId: "day-4", title: "象山影视城", city: "象山", kind: "attraction", start: "09:45", end: "14:00", duration: "4 小时 15 分钟", cost: 450, reason: "集中游览主要片区，中途安排休息。", coord: [29.353, 121.866], locked: false, transport: { mode: "步行", durationMinutes: 5, distanceKm: 0.3 } }),
      activity({ id: "a-403", dayId: "day-4", title: "石浦海鲜晚餐", city: "象山", kind: "meal", start: "16:30", end: "18:00", duration: "1 小时 30 分钟", cost: 420, reason: "回程前用餐，避免抵达宁波过晚。", coord: [29.196, 121.944], locked: false, transport: { mode: "包车", durationMinutes: 42, distanceKm: 34 } }),
    ],
  },
  {
    id: "day-5", dayNumber: 5, date: "2026-10-06", city: "宁波", routeColor: "#7b5aa6",
    weather: { icon: "cloud-sun", label: "多云", low: 18, high: 26 },
    note: "返程前仅安排市中心轻量活动，并预留 90 分钟进站时间。",
    activities: [
      activity({ id: "a-501", dayId: "day-5", title: "宁波博物院", city: "宁波", kind: "attraction", start: "09:30", end: "11:30", duration: "2 小时", cost: 0, reason: "室内活动稳定，适合作为返程日安排。", coord: [29.818, 121.548], locked: false, transport: { mode: "地铁", durationMinutes: 31, distanceKm: 9.6 } }),
      activity({ id: "a-502", dayId: "day-5", title: "酒店 Check-out 与午餐", city: "宁波", kind: "hotel", start: "12:20", end: "14:00", duration: "1 小时 40 分钟", cost: 220, reason: "集中处理退房和午餐，减少行李移动。", coord: [29.881, 121.563], locked: true, transport: { mode: "地铁", durationMinutes: 28, distanceKm: 8.7 } }),
      activity({ id: "a-503", dayId: "day-5", title: "宁波站返程", city: "宁波", kind: "transport", start: "17:00", end: "18:28", duration: "1 小时 28 分钟", cost: 144, reason: "为进站和晚高峰预留缓冲。", coord: [29.86, 121.536], locked: true, transport: { mode: "高铁", durationMinutes: 112, distanceKm: 314, note: "G7516" } }),
    ],
  },
];

export const defaultDraft: TripDraft = {
  origin: "上海",
  destinations: ["杭州", "绍兴", "宁波"],
  mainDestination: "杭州",
  startDate: "2026-10-02",
  endDate: "2026-10-06",
  adults: 2,
  children: 1,
  seniors: 1,
  travelStyle: "家庭旅行",
  budget: defaultBudget,
  preferences: {
    pace: "balanced",
    interests: ["人文古迹", "亲子", "美食", "摄影"],
    budgetLevel: "comfort",
    mustGo: "",
    avoid: "",
    constraints: "",
    longDistance: "high_speed_rail",
    localTransport: ["transit", "taxi"],
    accommodation: "舒适型，靠近地铁与当天路线中心",
  },
  notes: "老人不连续步行超过 90 分钟，儿童午后需要休息。",
  outboundPeriod: "morning",
  outboundMode: "schedule",
  outboundScheduleId: undefined,
  returnPeriod: "evening",
  returnMode: "schedule",
  returnScheduleId: undefined,
};

export const createMockTrip = (draft: TripDraft = defaultDraft, id = "demo-trip"): Trip => ({
  id,
  title: `${draft.destinations.join(" · ")}，5 日家庭旅行`,
  origin: draft.origin,
  destinations: draft.destinations,
  mainDestination: draft.mainDestination,
  destinationPlan: draft.destinationPlan,
  startDate: draft.startDate,
  endDate: draft.endDate,
  travelers: { adults: draft.adults, children: draft.children, seniors: draft.seniors, style: draft.travelStyle },
  status: "completed",
  updatedAt: new Date().toISOString(),
  budget: { ...draft.budget },
  preferences: { ...draft.preferences },
  days: days.map((day) => ({ ...day, activities: day.activities.map((item) => ({ ...item })) })),
  hotelStays: [
    { city: "杭州", name: "西湖畔酒店", coord: [30.247,120.151], pricePerNight: 880, source: "legacy" },
    { city: "绍兴", name: "绍兴古城酒店", coord: [30.002,120.581], pricePerNight: 760, source: "legacy" },
    { city: "宁波", name: "三江口酒店", coord: [29.881,121.563], pricePerNight: 820, source: "legacy" },
  ],
  transportSegments: [
    { id: "segment-outbound", kind: "outbound", label: `${draft.origin}至${draft.destinations[0]}`, origin: draft.origin, destination: draft.destinations[0], selectionMode: draft.outboundMode, selectedScheduleId: draft.outboundMode === "schedule" ? draft.outboundScheduleId ?? outboundSchedules.find((item) => item.recommended)?.id : undefined, customTime: draft.outboundArrival, customTimeLabel: "预计到达时间", schedules: outboundSchedules },
    { id: "segment-intercity-1", kind: "intercity", label: "杭州至绍兴", origin: "杭州", destination: "绍兴", travelDate: "2026-10-03", selectionMode: "schedule", selectedScheduleId: "city-g7651", schedules: [{ id: "city-g7651", code: "G7651", mode: "高铁", origin: "杭州东", destination: "绍兴北", departAt: "08:05", arriveAt: "08:24", duration: "19 分钟", price: 30, recommended: true }, { id: "city-g7431", code: "G7431", mode: "高铁", origin: "杭州南", destination: "绍兴北", departAt: "09:02", arriveAt: "09:17", duration: "15 分钟", price: 21 }] },
    { id: "segment-intercity-2", kind: "intercity", label: "绍兴至宁波", origin: "绍兴", destination: "宁波", travelDate: "2026-10-04", selectionMode: "schedule", selectedScheduleId: "city-g7545", schedules: [{ id: "city-g7545", code: "G7545", mode: "高铁", origin: "绍兴北", destination: "宁波", departAt: "08:32", arriveAt: "09:18", duration: "46 分钟", price: 48, recommended: true }, { id: "city-d3231", code: "D3231", mode: "火车", origin: "绍兴北", destination: "宁波", departAt: "10:11", arriveAt: "10:59", duration: "48 分钟", price: 45 }] },
    { id: "segment-return", kind: "return", label: `${draft.destinations.at(-1)}至${draft.origin}`, origin: draft.destinations.at(-1) ?? "宁波", destination: draft.origin, selectionMode: draft.returnMode, selectedScheduleId: draft.returnMode === "schedule" ? draft.returnScheduleId ?? returnSchedules.find((item) => item.recommended)?.id : undefined, customTime: draft.returnDeparture, customTimeLabel: "最晚离开时间", schedules: returnSchedules },
  ],
  shared: true,
  shareId: "jn-hzsn-2026",
});

export const historySeed: Trip[] = [
  createMockTrip(defaultDraft, "demo-trip"),
  { ...createMockTrip({ ...defaultDraft, destinations: ["成都", "都江堰"], mainDestination: "成都", budget: { ...defaultBudget, total: 9800 } }, "chengdu-trip"), title: "成都 · 都江堰，6 日亲子游", updatedAt: "2026-08-22T10:00:00.000Z" },
  { ...createMockTrip({ ...defaultDraft, destinations: ["北京"], mainDestination: "北京", budget: { ...defaultBudget, total: 6500 } }, "beijing-trip"), title: "北京，4 日文化之旅", status: "draft", updatedAt: "2026-08-15T07:40:00.000Z" },
];
