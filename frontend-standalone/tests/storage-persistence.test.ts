import assert from "node:assert/strict";
import test from "node:test";
import {
  createResilientStorage,
  lightweightState,
  storageSizeReport,
  TRIP_STORE_KEY,
// @ts-expect-error Node's type-stripping test runner requires the explicit extension.
} from "../store/storage-persistence.ts";

const largeTrip = () => ({
  id: "trip-large",
  profile: {
    itinerary: Array.from({ length: 9 }, (_, day) => ({
      day,
      stops: Array.from({ length: 14 }, (_, index) => ({ place_id: `p-${day}-${index}`, note: "x".repeat(2000) })),
    })),
    places: Array.from({ length: 120 }, (_, index) => ({ id: `p-${index}`, facts: "x".repeat(4000) })),
    warnings: Array.from({ length: 300 }, (_, index) => ({ code: `W${index}`, message: "x".repeat(500) })),
    replan_history: Array.from({ length: 20 }, () => ({ before: "x".repeat(5000), after: "x".repeat(5000) })),
  },
});

test("large trips never enter the persisted slice", () => {
  const state = { draft: { destinations: ["上海", "苏州"] }, currentTripId: "trip-large", activeDayId: "day-4",
    trips: [largeTrip()], snapshot: largeTrip(), pendingReplan: largeTrip() };
  const fullBytes = storageSizeReport(JSON.stringify({ state, version: 6 })).bytes;
  const persisted = JSON.stringify({ state: lightweightState(state), version: 7 });
  const report = storageSizeReport(persisted);
  assert.ok(fullBytes > 1_000_000);
  assert.ok(report.bytes < 10_000);
  assert.deepEqual(Object.keys(report.fields).sort(), ["activeDayId", "currentTripId", "draft"]);
});

test("quota errors are reported and never escape to polling callers", () => {
  let writes = 0; const warnings: unknown[] = [];
  const storage = createResilientStorage(() => ({
    length: 0, clear() {}, key() { return null; }, getItem() { return null; }, removeItem() {},
    setItem() { writes += 1; throw new DOMException("quota", "QuotaExceededError"); },
  }), warning => warnings.push(warning));
  assert.doesNotThrow(() => storage.setItem(TRIP_STORE_KEY, "value"));
  assert.equal(writes, 1);
  assert.deepEqual(warnings, [{ operation: "write", key: TRIP_STORE_KEY, message: "quota" }]);
});

test("a legacy store restores only draft and navigation state", () => {
  const legacy = JSON.stringify({ state: { draft: { notes: "保留草稿" }, currentTripId: "trip-1",
    activeDayId: "day-2", trips: [largeTrip()] }, version: 5 });
  const data = new Map([["journey-notes-store-v3", legacy]]);
  const storage = createResilientStorage(() => ({
    length: data.size, clear() { data.clear(); }, key() { return null; },
    getItem(key: string) { return data.get(key) ?? null; }, removeItem(key: string) { data.delete(key); },
    setItem(key: string, value: string) { data.set(key, value); },
  }));
  const migrated = JSON.parse(String(storage.getItem(TRIP_STORE_KEY)));
  assert.equal(migrated.state.draft.notes, "保留草稿");
  assert.equal(migrated.state.currentTripId, "trip-1");
  assert.equal(migrated.state.trips, undefined);
});
