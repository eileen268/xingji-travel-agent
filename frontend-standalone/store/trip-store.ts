"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { defaultDraft, historySeed } from "@/mocks/trip";
import type { Activity, Budget, ReplanPreview, Trip, TripDraft } from "@/types/trip";
import { normalizeTravelPace } from "@/features/pace/travel-pace-engine";
import { cleanupLegacyTripStores, createResilientStorage, lightweightState, reportStorageWarning, TRIP_STORE_KEY } from "./storage-persistence";

type TripSnapshot = Trip | null;

interface TripStore {
  hydrated: boolean;
  draft: TripDraft;
  trips: Trip[];
  currentTripId: string;
  activeDayId: string | "overview";
  selectedActivityId: string | null;
  snapshot: TripSnapshot;
  pendingReplan: ReplanPreview | null;
  toast: string | null;
  setHydrated: (value: boolean) => void;
  updateDraft: (patch: Partial<TripDraft>) => void;
  updateDraftBudget: (key: keyof Budget, value: number) => void;
  updateDraftPreferences: (patch: Partial<TripDraft["preferences"]>) => void;
  saveGeneratedTrip: (trip: Trip) => string;
  syncTrip: (trip: Trip) => void;
  setCurrentTrip: (id: string) => void;
  setActiveDay: (id: string | "overview") => void;
  selectActivity: (id: string | null) => void;
  reorderActivity: (dayId: string, activeId: string, overId: string) => void;
  moveActivity: (activityId: string, targetDayId: string, index?: number) => void;
  addActivity: (dayId: string, activity: Omit<Activity, "id" | "dayId">) => void;
  updateActivity: (activityId: string, patch: Partial<Activity>) => void;
  removeActivity: (activityId: string) => void;
  toggleActivityLock: (activityId: string) => void;
  updateBudget: (budget: Budget) => void;
  applyHotelChange: (trip: Trip) => void;
  applyManualChange: (trip: Trip, message: string) => void;
  applyAgentReplan: (trip: Trip) => void;
  applyTravelAdvice: (tripId: string, groups: Array<{ title: string; items: string[] }>, tips: string[]) => void;
  setPendingReplan: (preview: ReplanPreview | null) => void;
  confirmReplan: () => void;
  undo: () => void;
  duplicateTrip: (id: string) => string;
  deleteTrip: (id: string) => void;
  toggleShare: (id: string, shared: boolean) => void;
  setToast: (message: string | null) => void;
}

const makeSnapshot = (trip: Trip): Trip => structuredClone(trip);

const updateCurrent = (state: TripStore, updater: (trip: Trip) => Trip) => {
  const current = state.trips.find((trip) => trip.id === state.currentTripId);
  if (!current) return { trips: state.trips };
  const next = updater(makeSnapshot(current));
  next.updatedAt = new Date().toISOString();
  return {
    snapshot: makeSnapshot(current),
    trips: state.trips.map((trip) => (trip.id === next.id ? next : trip)),
  };
};

export const useTripStore = create<TripStore>()(
  persist(
    (set, get) => ({
      hydrated: false,
      draft: defaultDraft,
      trips: historySeed,
      currentTripId: "demo-trip",
      activeDayId: "overview",
      selectedActivityId: null,
      snapshot: null,
      pendingReplan: null,
      toast: null,
      setHydrated: (hydrated) => set({ hydrated }),
      updateDraft: (patch) => set((state) => ({ draft: { ...state.draft, ...patch } })),
      updateDraftBudget: (key, value) => set((state) => ({ draft: { ...state.draft, budget: { ...state.draft.budget, [key]: value } } })),
      updateDraftPreferences: (patch) => set((state) => ({ draft: { ...state.draft, preferences: { ...state.draft.preferences, ...patch } } })),
      saveGeneratedTrip: (trip) => {
        set((state) => ({ trips: [trip, ...state.trips.filter((item) => item.id !== trip.id)], currentTripId: trip.id, activeDayId: "overview", selectedActivityId: null }));
        return trip.id;
      },
      syncTrip: (trip) => set((state) => ({ trips: [trip, ...state.trips.filter((item) => item.id !== trip.id)] })),
      setCurrentTrip: (id) => set({ currentTripId: id, activeDayId: "overview", selectedActivityId: null }),
      setActiveDay: (id) => set({ activeDayId: id, selectedActivityId: null }),
      selectActivity: (id) => set({ selectedActivityId: id }),
      reorderActivity: (dayId, activeId, overId) => set((state) => updateCurrent(state, (trip) => {
        const day = trip.days.find((item) => item.id === dayId);
        if (!day) return trip;
        const from = day.activities.findIndex((item) => item.id === activeId);
        const to = day.activities.findIndex((item) => item.id === overId);
        if (from < 0 || to < 0 || from === to) return trip;
        const [moved] = day.activities.splice(from, 1);
        day.activities.splice(to, 0, moved);
        return trip;
      })),
      moveActivity: (activityId, targetDayId, index) => set((state) => updateCurrent(state, (trip) => {
        let moved: Activity | undefined;
        for (const day of trip.days) {
          const itemIndex = day.activities.findIndex((item) => item.id === activityId);
          if (itemIndex >= 0) [moved] = day.activities.splice(itemIndex, 1);
        }
        const target = trip.days.find((day) => day.id === targetDayId);
        if (!moved || !target) return trip;
        moved.dayId = targetDayId;
        target.activities.splice(index ?? target.activities.length, 0, moved);
        return trip;
      })),
      addActivity: (dayId, activityValue) => set((state) => updateCurrent(state, (trip) => {
        const day = trip.days.find((item) => item.id === dayId);
        if (day) day.activities.push({ ...activityValue, id: `activity-${Date.now()}`, dayId });
        return trip;
      })),
      updateActivity: (activityId, patch) => set((state) => updateCurrent(state, (trip) => {
        trip.days.forEach((day) => {
          const item = day.activities.find((activityValue) => activityValue.id === activityId);
          if (item) Object.assign(item, patch);
        });
        return trip;
      })),
      removeActivity: (activityId) => set((state) => updateCurrent(state, (trip) => {
        trip.days.forEach((day) => { day.activities = day.activities.filter((item) => item.id !== activityId || item.locked); });
        return trip;
      })),
      toggleActivityLock: (activityId) => set((state) => updateCurrent(state, (trip) => {
        trip.days.forEach((day) => {
          const item = day.activities.find((activityValue) => activityValue.id === activityId);
          if (item) item.locked = !item.locked;
        });
        return trip;
      })),
      updateBudget: (budget) => set((state) => ({ ...updateCurrent(state, (trip) => ({ ...trip, budget })), toast: "预算已保存，尚未重新规划" })),
      applyHotelChange: (trip) => set((state) => {
        const current = state.trips.find((item) => item.id === trip.id);
        return { snapshot: current ? makeSnapshot(current) : state.snapshot, trips: state.trips.map((item) => item.id === trip.id ? trip : item), toast: "酒店已更换，同城每日首尾交通与时间已重新计算" };
      }),
      applyManualChange: (trip, message) => set((state) => {
        const current = state.trips.find((item) => item.id === trip.id);
        return { snapshot: current ? makeSnapshot(current) : state.snapshot, trips: state.trips.map((item) => item.id === trip.id ? trip : item), toast: message };
      }),
      applyAgentReplan: (trip) => set((state) => {
        const current = state.trips.find((item) => item.id === trip.id);
        return { snapshot: current ? makeSnapshot(current) : state.snapshot, trips: state.trips.map((item) => item.id === trip.id ? trip : item), toast: "Agent 已完成真实重规划，可撤销本次修改" };
      }),
      applyTravelAdvice: (tripId, groups, tips) => set((state) => ({ trips: state.trips.map((trip) => trip.id === tripId ? { ...trip, preparationItems: groups, travelTips: tips, updatedAt: new Date().toISOString() } : trip) })),
      setPendingReplan: (pendingReplan) => set({ pendingReplan }),
      confirmReplan: () => {
        const pending = get().pendingReplan;
        if (!pending) return;
        pending.action();
        set({ pendingReplan: null, toast: "已完成局部重规划，可撤销本次修改" });
      },
      undo: () => set((state) => {
        if (!state.snapshot) return { toast: "当前没有可撤销的修改" };
        return { trips: state.trips.map((trip) => trip.id === state.snapshot?.id ? state.snapshot : trip), snapshot: null, toast: "已恢复到修改前" };
      }),
      duplicateTrip: (id) => {
        const source = get().trips.find((trip) => trip.id === id);
        if (!source) return id;
        const newId = `trip-${Date.now()}`;
        const copy = { ...makeSnapshot(source), id: newId, title: `${source.title} 副本`, updatedAt: new Date().toISOString(), status: "draft" as const, shared: false, shareId: undefined };
        set((state) => ({ trips: [copy, ...state.trips], currentTripId: newId, toast: "已复制为新草稿" }));
        return newId;
      },
      deleteTrip: (id) => set((state) => ({ trips: state.trips.filter((trip) => trip.id !== id), toast: "行程已删除" })),
      toggleShare: (id, shared) => set((state) => ({ trips: state.trips.map((trip) => trip.id === id ? { ...trip, shared, shareId: shared ? trip.shareId ?? `share-${Date.now()}` : undefined } : trip), toast: shared ? "只读分享已开启" : "分享已停止" })),
      setToast: (toast) => set({ toast }),
    }),
    {
      name: TRIP_STORE_KEY,
      version: 7,
      storage: createJSONStorage(() => createResilientStorage(() => localStorage, reportStorageWarning)),
      migrate: (persisted) => {
        const state = persisted as Partial<TripStore>;
        if (state.draft) {
          state.draft.preferences = { ...defaultDraft.preferences, ...state.draft.preferences };
          state.draft.preferences.pace = normalizeTravelPace(state.draft.preferences.pace);
          state.draft.preferences.interests = state.draft.preferences.interests.filter((value) => !/购物|美妆|时尚|买手店/.test(value));
          state.draft.travelStyle = ({ "情侣出行": "情侣或夫妻", "朋友结伴": "朋友", "亲子旅行": "亲子" } as Record<string,string>)[state.draft.travelStyle] ?? state.draft.travelStyle;
        }
        // Full trips, profiles, routes, facts, diagnostics and replan history
        // deliberately remain in memory and are restored from the backend.
        return lightweightState(state) as Partial<TripStore>;
      },
      partialize: (state) => lightweightState(state),
      onRehydrateStorage: () => (state) => {
        cleanupLegacyTripStores(localStorage);
        state?.setHydrated(true);
      },
    },
  ),
);

export const getAllocatedBudget = (budget: Budget) => budget.accommodation + budget.food + budget.transportation + budget.activities + budget.shopping + budget.other;

export function getTripSpendByCategory(trip: Trip): Record<Exclude<keyof Budget, "total">, number> {
  const spending = { accommodation: 0, food: 0, transportation: 0, activities: 0, shopping: 0, other: 0 };
  for (const stay of trip.hotelStays ?? []) {
    if (stay.totalCost !== undefined) spending.accommodation += Math.max(0, stay.totalCost);
    else if (stay.checkInAt && stay.checkOutAt) {
      const nights = Math.max(1, Math.round((new Date(`${stay.checkOutAt.slice(0, 10)}T00:00:00Z`).getTime() - new Date(`${stay.checkInAt.slice(0, 10)}T00:00:00Z`).getTime()) / 86_400_000));
      spending.accommodation += Math.max(0, stay.pricePerNight) * nights;
    } else spending.accommodation += Math.max(0, stay.pricePerNight);
  }
  for (const activity of trip.days.flatMap((day) => day.activities)) {
    const amount = Number.isFinite(activity.cost) ? Math.max(0, activity.cost) : 0;
    if (activity.kind === "hotel") continue;
    if (activity.kind === "meal") spending.food += amount;
    else if (activity.kind === "transport") spending.transportation += amount;
    else spending.activities += amount;
  }
  return spending;
}

export function getEstimatedTripCost(trip: Trip) {
  return Object.values(getTripSpendByCategory(trip)).reduce((sum, value) => sum + value, 0);
}
