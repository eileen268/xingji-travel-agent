import type { StateStorage } from "zustand/middleware";

export const TRIP_STORE_KEY = "journey-notes-store-v4";
export const LEGACY_TRIP_STORE_KEYS = [
  "journey-notes-store-v1",
  "journey-notes-store-v2",
  "journey-notes-store-v3",
] as const;
export const STORAGE_WARNING_EVENT = "journey-notes:storage-warning";
let latestStorageWarning: StorageWarning | null = null;

export type LightweightTripState<TDraft = unknown> = {
  draft?: TDraft;
  currentTripId?: string;
  activeDayId?: string;
};

export const utf8Bytes = (value: string) => new TextEncoder().encode(value).length;

export function storageSizeReport(serialized: string | null) {
  if (!serialized) return { bytes: 0, fields: {} as Record<string, number> };
  let parsed: unknown;
  try { parsed = JSON.parse(serialized); } catch { return { bytes: utf8Bytes(serialized), fields: {} }; }
  const container = (parsed && typeof parsed === "object" && "state" in parsed)
    ? (parsed as { state?: unknown }).state : parsed;
  const fields: Record<string, number> = {};
  if (container && typeof container === "object") {
    for (const [key, value] of Object.entries(container)) fields[key] = utf8Bytes(JSON.stringify(value));
  }
  return { bytes: utf8Bytes(serialized), fields };
}

export function lightweightState<T extends LightweightTripState>(state: T): LightweightTripState {
  return {
    draft: state.draft,
    currentTripId: state.currentTripId,
    activeDayId: state.activeDayId,
  };
}

function lightweightSerializedLegacy(value: string): string | null {
  try {
    const parsed = JSON.parse(value) as { state?: LightweightTripState } | LightweightTripState;
    const state = ("state" in parsed ? parsed.state : parsed) as LightweightTripState | undefined;
    return JSON.stringify({ state: lightweightState(state ?? {}), version: 7 });
  } catch { return null; }
}

export type StorageWarning = { operation: "read" | "write" | "remove"; key: string; message: string };

export function createResilientStorage(
  getStorage: () => Storage,
  report: (warning: StorageWarning) => void = () => undefined,
): StateStorage {
  const warn = (operation: StorageWarning["operation"], key: string, error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    report({ operation, key, message });
  };
  return {
    getItem: (key) => {
      try {
        const storage = getStorage();
        const current = storage.getItem(key);
        if (current !== null || key !== TRIP_STORE_KEY) return current;
        for (const legacyKey of [...LEGACY_TRIP_STORE_KEYS].reverse()) {
          const legacy = storage.getItem(legacyKey);
          if (legacy) return lightweightSerializedLegacy(legacy);
        }
        return null;
      } catch (error) { warn("read", key, error); return null; }
    },
    setItem: (key, value) => {
      try { getStorage().setItem(key, value); }
      catch (error) { warn("write", key, error); }
    },
    removeItem: (key) => {
      try { getStorage().removeItem(key); }
      catch (error) { warn("remove", key, error); }
    },
  };
}

export function cleanupLegacyTripStores(storage: Pick<Storage, "removeItem">) {
  for (const key of LEGACY_TRIP_STORE_KEYS) {
    try { storage.removeItem(key); } catch { /* best-effort cleanup */ }
  }
}

export function reportStorageWarning(warning: StorageWarning) {
  if (typeof window === "undefined") return;
  latestStorageWarning = warning;
  console.warn("journey-notes local persistence disabled for this write", warning);
  window.dispatchEvent(new CustomEvent(STORAGE_WARNING_EVENT, { detail: warning }));
}

export const getLatestStorageWarning = () => latestStorageWarning;
