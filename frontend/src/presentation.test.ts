import assert from "node:assert/strict";
import test from "node:test";
import {
  numeric,
  availableAt,
  observedAt,
  adviceLifecycle,
  replayView,
} from "./presentation.ts";
import type { Event } from "./types.ts";
const event = (
  id: string,
  source_time: number,
  payload: Event["payload"],
): Event => ({
  id,
  session_id: "session",
  sequence: 1,
  source_time,
  received_at: "2026-01-01T00:00:00Z",
  type: "assessment",
  payload,
});
test("API percentage units do not get multiplied twice", () => {
  assert.equal(numeric(66.6667, "%"), "66.7%");
  assert.equal(numeric(2 / 3, "fraction"), "66.7%");
  assert.equal(numeric(100, "%"), "100.0%");
  assert.equal(numeric(null, "%"), "Not measured");
  assert.equal(numeric(0.231402, "USD"), "$0.231");
});
test("a delayed report preserves source time and is hidden until available", () => {
  const report = event("e1242", 90, {
    observed_at: 88,
    available_source_time: 90,
  });
  assert.equal(observedAt(report), 88);
  assert.equal(availableAt(report), 90);
  assert.equal(availableAt(report) <= 89.9, false);
  assert.equal(availableAt(report) <= 90, true);
});
test("advice is proposed, applied, and expired at the recorded boundaries", () => {
  const assessment = event("first", 10, {
    accepted: true,
    application_source_time: 10.1,
    expiry_source_time: 45,
  });
  assert.equal(adviceLifecycle(assessment, [assessment], 10), "proposed");
  assert.equal(adviceLifecycle(assessment, [assessment], 10.1), "applied");
  assert.equal(adviceLifecycle(assessment, [assessment], 45), "expired");
});
test("only an applied accepted successor replaces advice", () => {
  const first = event("first", 10, {
    accepted: true,
    application_source_time: 10.1,
    expiry_source_time: 45,
  });
  const rejected = event("rejected", 20, {
    accepted: false,
    application_source_time: 20.1,
  });
  assert.equal(adviceLifecycle(first, [first, rejected], 30), "applied");
  const next = event("next", 30, {
    accepted: true,
    application_source_time: 30.1,
  });
  assert.equal(adviceLifecycle(first, [first, next], 30), "applied");
  assert.equal(adviceLifecycle(first, [first, next], 30.1), "replaced");
  assert.equal(adviceLifecycle(rejected, [first, rejected], 30), "rejected");
});

test("an empty live session keeps a valid view until telemetry establishes its boundary", () => {
  assert.equal(replayView([], undefined, "observer"), "observer");
  assert.equal(replayView(["reported"], "observer", "observer"), "reported");
  assert.equal(
    replayView(["observer", "evaluator"], "evaluator", "observer"),
    "evaluator",
  );
});
