import type { Event } from "./types";

/** Compare API percentages are already in 0..100; fractions are explicit. */
export function numeric(value: number | null, unit: string, _key = ""): string {
  if (value === null || !Number.isFinite(value)) return "Not measured";
  if (unit === "%") return value.toFixed(1) + "%";
  if (unit === "fraction") return (value * 100).toFixed(1) + "%";
  if (unit.toLowerCase() === "usd" || unit === "$")
    return "$" + value.toFixed(3);
  return (
    value.toLocaleString(undefined, {
      maximumFractionDigits: unit === "seconds" || unit === "s" ? 2 : 3,
    }) + (unit && unit !== "count" ? " " + unit : "")
  );
}
export function availableAt(event: Event): number {
  return event.payload.available_source_time ?? event.source_time;
}
export function observedAt(event: Event): number {
  if (event.payload.observed_at !== undefined) return event.payload.observed_at;
  const source = (event.payload.data || event.payload).observed_tick;
  return typeof source === "number" ? source / 10 : event.source_time;
}
export function adviceLifecycle(
  event: Event,
  events: Event[],
  time: number,
): string {
  const payload = event.payload;
  if (payload.accepted === false) return "rejected";
  if (payload.accepted === undefined) return payload.status || "recorded";
  const application = payload.application_source_time ?? event.source_time;
  if (application > time) return "proposed";
  const successor = events.find(
    (other) =>
      other.id !== event.id &&
      other.payload.accepted === true &&
      (other.payload.application_source_time ?? other.source_time) >
        application &&
      (other.payload.application_source_time ?? other.source_time) <= time,
  );
  if (successor) return "replaced";
  if (
    payload.expiry_source_time !== undefined &&
    payload.expiry_source_time <= time
  )
    return "expired";
  return "applied";
}

/** Empty live sessions have no available views until the first frame arrives. */
export function replayView(
  available: string[] | undefined,
  recorded: string | undefined,
  current: string,
): string {
  if (!available?.length) return current;
  if (recorded && available.includes(recorded)) return recorded;
  if (available.includes(current)) return current;
  return available.includes("observer") ? "observer" : available[0];
}
