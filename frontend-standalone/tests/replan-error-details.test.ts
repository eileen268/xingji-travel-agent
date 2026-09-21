import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error Node's type-stripping test runner requires the explicit extension.
import { normalizeErrorDetails } from "../lib/error-details.ts";

test("replan error details accepts only arrays", () => {
  assert.deepEqual(normalizeErrorDetails([{ type: "reason", message: "冲突" }]), [{ type: "reason", message: "冲突" }]);
  assert.deepEqual(normalizeErrorDetails({ message: "object" }), []);
  assert.deepEqual(normalizeErrorDetails("string"), []);
  assert.deepEqual(normalizeErrorDetails(null), []);
});
