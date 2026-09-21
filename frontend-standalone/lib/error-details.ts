export function normalizeErrorDetails(value:unknown):unknown[] {
  return Array.isArray(value)?value:[];
}
