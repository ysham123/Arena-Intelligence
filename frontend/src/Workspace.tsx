import React, { useEffect, useMemo, useRef, useState } from "react";
import { request } from "./api";
import {
  availableAt,
  observedAt,
  adviceLifecycle,
  replayView,
} from "./presentation";
import { Button, PageHeading, Status, Empty, icons } from "./Components";
import { ForecastStrip } from "./Workbench";
import type {
  Session,
  SessionDetail,
  Replay,
  Frame,
  Event,
  Hypothesis,
  Insight,
  Review,
  World,
  Entity,
} from "./types";

const palette = ["#3e8192", "#b27a4f", "#77956d", "#967b9b"];
function teamIndex(team: string | number | undefined) {
  if (team === undefined) return 0;
  if (typeof team === "number") return Math.abs(team) % palette.length;
  return ["0", "blue", "friendly"].includes(team)
    ? 0
    : ["1", "red", "opponent"].includes(team)
      ? 1
      : Math.abs([...team].reduce((sum, char) => sum + char.charCodeAt(0), 0)) %
        palette.length;
}
export function drawFrame(
  canvas: HTMLCanvasElement,
  frame: Frame,
  view: string,
  selected?: Event,
) {
  const context = canvas.getContext("2d");
  if (!context) return;
  const size = canvas.width,
    world: World = frame.world || { width: 32, height: 32 },
    width = world.width || 32,
    height = world.height || 32,
    padding = 0,
    cellX = (size - 2 * padding) / width,
    cellY = (size - 2 * padding) / height,
    cell = Math.min(cellX, cellY),
    font = Math.max(
      14,
      (size / Math.max(canvas.getBoundingClientRect().width, 1)) * 10,
    );
  context.fillStyle = "#edf1e8";
  context.fillRect(0, 0, size, size);
  for (let x = 0; x <= width; x++) {
    context.strokeStyle = x % 4 === 0 ? "#d4dfd1" : "#e2e9dc";
    context.lineWidth = x % 4 === 0 ? 1 : 0.6;
    context.beginPath();
    context.moveTo(x * cellX, 0);
    context.lineTo(x * cellX, size);
    context.stroke();
  }
  for (let y = 0; y <= height; y++) {
    context.strokeStyle = y % 4 === 0 ? "#d4dfd1" : "#e2e9dc";
    context.lineWidth = y % 4 === 0 ? 1 : 0.6;
    context.beginPath();
    context.moveTo(0, y * cellY);
    context.lineTo(size, y * cellY);
    context.stroke();
  }
  for (const [x, y] of world.blocked || []) {
    context.fillStyle = "#bccbb8";
    context.fillRect(x * cellX + 1, y * cellY + 1, cellX - 2, cellY - 2);
  }
  for (const node of world.nodes || []) {
    const x = (node.x + 0.5) * cellX,
      y = (node.y + 0.5) * cellY;
    context.save();
    context.fillStyle = "#557a68";
    context.globalAlpha = 0.07;
    context.beginPath();
    context.moveTo(x, y - cellY * 2.5);
    context.lineTo(x + cellX * 2.5, y);
    context.lineTo(x, y + cellY * 2.5);
    context.lineTo(x - cellX * 2.5, y);
    context.closePath();
    context.fill();
    context.globalAlpha = 0.35;
    context.strokeStyle = "#617c68";
    context.setLineDash([3, 5]);
    context.stroke();
    context.restore();
  }
  const groups = new Map<
    string,
    { entity: Entity; count: number; report: boolean }
  >();
  for (const entity of frame.entities) {
    const report =
      view !== "evaluator" &&
      (entity.visibility === "report" ||
        (entity.source_time !== undefined &&
          entity.source_time < frame.source_time - 0.001));
    const key = [entity.team, entity.x, entity.y, report].join(":");
    const group = groups.get(key) || { entity, count: 0, report };
    group.count++;
    groups.set(key, group);
  }
  for (const { entity, count, report } of groups.values()) {
    const x = (entity.x + 0.5) * cellX,
      y = (entity.y + 0.5) * cellY;
    context.save();
    context.strokeStyle = report ? "#976d32" : palette[teamIndex(entity.team)];
    context.fillStyle = palette[teamIndex(entity.team)];
    context.lineWidth = report ? 2 : 1;
    context.globalAlpha = report ? 0.8 : 1;
    context.beginPath();
    context.arc(
      x,
      y,
      cell * (report ? 0.34 : count > 1 ? 0.28 : 0.2),
      0,
      Math.PI * 2,
    );
    if (!report) context.fill();
    context.stroke();
    if (count > 1) {
      context.font = font + "px PlexMono,monospace";
      context.fillStyle = report ? "#946d2b" : palette[teamIndex(entity.team)];
      context.textBaseline = report ? "top" : "bottom";
      context.textAlign = "left";
      context.fillText(
        "×" + count,
        x + cell * 0.38,
        y + cell * (report ? 0.15 : -0.1),
      );
    }
    context.restore();
  }
  for (const node of world.nodes || []) {
    const x = (node.x + 0.5) * cellX,
      y = (node.y + 0.5) * cellY;
    context.fillStyle = "#edf1e8";
    context.fillRect(
      x - cell * 0.7,
      y - cell * 0.95 - font * 0.6,
      cell * 1.4,
      font * 1.2,
    );
    context.fillStyle = "#53695d";
    context.font = font + "px PlexMono,monospace";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText("N" + node.id, x, y - cell * 0.95);
  }
  if (selected) {
    const payload = selected.payload,
      data = payload.data || payload;
    const nodeId = Number(data.node_id),
      node = world.nodes?.find((item) => item.id === nodeId);
    const x = node?.x ?? Number(data.x),
      y = node?.y ?? Number(data.y);
    if (Number.isFinite(x) && Number.isFinite(y)) {
      context.strokeStyle = "#99733e";
      context.lineWidth = 2;
      context.setLineDash([4, 4]);
      context.beginPath();
      context.arc(
        (x + 0.5) * cellX,
        (y + 0.5) * cellY,
        cell * 0.7,
        0,
        Math.PI * 2,
      );
      context.stroke();
      context.setLineDash([]);
    }
  }
}
function latestFrame(frames: Frame[], time: number) {
  let left = 0,
    right = frames.length - 1,
    best: Frame | undefined;
  while (left <= right) {
    const mid = (left + right) >> 1;
    if (frames[mid].source_time <= time) {
      best = frames[mid];
      left = mid + 1;
    } else right = mid - 1;
  }
  return best;
}
function tidy(text: string) {
  return String(text || "")
    .replace(
      /\bticks?\s+(\d+)\s*(?:[–-]|to|through)\s*(\d+)/g,
      (_, start, end) =>
        (Number(start) / 10).toFixed(1) +
        "s to " +
        (Number(end) / 10).toFixed(1) +
        "s",
    )
    .replace(
      /\bticks? (\d+)/g,
      (_, value) => (Number(value) / 10).toFixed(1) + "s",
    )
    .replace(
      /(\d+) ticks/g,
      (_, value) => (Number(value) / 10).toFixed(1) + "s",
    )
    .replace(/cutoff\s*\+\s*300/g, "the 30s forecast horizon")
    .replace(/\bcutoff\b/g, "observation time");
}
function evidenceLabel(event: Event) {
  const payload = event.payload,
    data = payload.data || payload;
  const target =
    data.node_id !== undefined
      ? "N" + data.node_id
      : data.bot_id !== undefined
        ? "Bot " + data.bot_id
        : data.entity_id !== undefined
          ? String(data.entity_id)
          : event.type;
  const source = observedAt(event);
  return target + " · " + source.toFixed(1) + "s";
}
function EvidenceChips({
  ids,
  evidence,
  onSelect,
}: {
  ids: string[];
  evidence: Map<string, Event>;
  onSelect: (event: Event) => void;
}) {
  const [all, setAll] = useState(false);
  return (
    <div className="evidence-chips">
      <span className="small">Evidence</span>
      {ids.slice(0, all ? ids.length : 5).map((id) => {
        const event = evidence.get(id);
        return (
          <button
            type="button"
            key={id}
            onClick={() => event && onSelect(event)}
            disabled={!event}
            aria-label={"Inspect evidence " + id}
            title={id}
          >
            {event ? evidenceLabel(event) : "Unavailable reference"}
          </button>
        );
      })}
      {ids.length > 5 && (
        <Button className="text-button" onClick={() => setAll(!all)}>
          {all ? "Fewer" : "+" + (ids.length - 5) + " reports"}
        </Button>
      )}
      {!ids.length && <span className="small">No supporting reports</span>}
    </div>
  );
}
function Inspector({ event, onClose }: { event: Event; onClose: () => void }) {
  const ref = useRef<HTMLElement>(null),
    data = event.payload.data || event.payload;
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest", behavior: "instant" });
  }, [event.id]);
  const observed = observedAt(event),
    delivered =
      event.payload.delivered_source_time ??
      (typeof data.delivered_tick === "number"
        ? data.delivered_tick / 10
        : null);
  const facts = [
    ["Reference", event.id],
    ["Observed", observed.toFixed(1) + "s"],
    ["Available", availableAt(event).toFixed(1) + "s"],
    ["Workspace received", new Date(event.received_at).toLocaleString()],
    [
      delivered === null ? "Sequence" : "Simulator delivery",
      delivered === null ? String(event.sequence) : delivered.toFixed(1) + "s",
    ],
  ];
  for (const key of [
    "node_id",
    "entity_id",
    "bot_id",
    "friendly_count",
    "opponent_count",
  ])
    if (data[key] !== undefined)
      facts.push([key.replaceAll("_", " "), String(data[key])]);
  return (
    <section
      ref={ref}
      className="surface evidence-inspector"
      aria-label="Selected source evidence"
    >
      <div className="surface-heading">
        <div>
          <div className="eyebrow">Recorded source</div>
          <h3>{evidenceLabel(event)}</h3>
        </div>
        <Button className="text-button" onClick={onClose}>
          Close evidence
        </Button>
      </div>
      <dl className="evidence-facts">
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {data.opponent_count !== undefined && (
        <p>
          Opponent counts are a sensor-visible lower bound. An empty report does
          not establish absence.
        </p>
      )}
      <details>
        <summary>Source record</summary>
        <pre>{JSON.stringify(event, null, 2)}</pre>
      </details>
    </section>
  );
}
function ModelInsight({
  event,
  evidence,
  time,
  onSelect,
  lifecycle,
}: {
  event: Event;
  lifecycle?: string;
  evidence: Map<string, Event>;
  time: number;
  onSelect: (event: Event) => void;
}) {
  const payload = event.payload,
    hypotheses = payload.hypotheses || [],
    primary = hypotheses[0],
    forecast = payload.forecast,
    horizon =
      forecast?.horizon_source_time ??
      (forecast?.horizon_tick === undefined
        ? null
        : forecast.horizon_tick / 10),
    cutoff =
      payload.cutoff_tick === undefined
        ? event.source_time
        : payload.cutoff_tick / 10;
  const origin =
    payload.origin === "scripted"
      ? "Scripted controller"
      : payload.origin === "model" || payload.origin === "imported"
        ? "Recorded model assessment"
        : "Imported assessment";
  return (
    <>
      <div className="insight-header">
        <h2>Recorded assessment</h2>
        <Status value={lifecycle || payload.status || "recorded"} />
      </div>
      <div className="insight-source">
        {origin} · observation {cutoff.toFixed(1)}s ·{" "}
        {(time - cutoff).toFixed(1)}s old
      </div>
      {primary ? (
        <>
          <p className="claim">{tidy(primary.claim)}</p>
          {primary.alternative && (
            <p className="alternative">
              <strong>Alternative · </strong>
              {tidy(primary.alternative)}
            </p>
          )}
          <EvidenceChips
            ids={primary.evidence_ids}
            evidence={evidence}
            onSelect={onSelect}
          />
        </>
      ) : (
        <p className="claim">
          {payload.summary || "No structured hypothesis was recorded."}
        </p>
      )}
      {hypotheses.slice(1).map((hypothesis, index) => (
        <details className="secondary-hypothesis" key={index}>
          <summary>{tidy(hypothesis.claim).slice(0, 95)}…</summary>
          <p className="claim">{tidy(hypothesis.claim)}</p>
          {hypothesis.alternative && (
            <p className="alternative">{tidy(hypothesis.alternative)}</p>
          )}
          <EvidenceChips
            ids={hypothesis.evidence_ids}
            evidence={evidence}
            onSelect={onSelect}
          />
        </details>
      ))}
      {forecast?.probabilities?.length === 4 && (
        <div className="forecast">
          <div className="forecast-title">
            <span>
              Forecast{" "}
              {horizon === null ? "" : "for " + horizon.toFixed(1) + "s"}
            </span>
            <span>
              {horizon === null
                ? ""
                : time < horizon
                  ? (horizon - time).toFixed(1) + "s remaining"
                  : "Horizon passed"}
            </span>
          </div>
          <div className="forecast-columns">
            {forecast.probabilities.map((value, index) => (
              <div
                className={
                  "forecast-column" +
                  (value === Math.max(...forecast.probabilities) ? " high" : "")
                }
                key={index}
              >
                {index === 3 ? "None" : "Node " + index}
                <strong>{(value * 100).toFixed(0)}%</strong>
                <div className="forecast-bar">
                  <span style={{ width: value * 100 + "%" }} />
                </div>
              </div>
            ))}
          </div>
          <p className="forecast-note">
            The recorded forecast is conditional on its recommended assignment
            remaining active through the horizon.
          </p>
        </div>
      )}
      {payload.assignments?.length ? (
        <div className="assignment">
          Recorded objectives ·{" "}
          {[0, 1, 2]
            .map(
              (node) =>
                "N" +
                node +
                " ×" +
                payload.assignments!.filter(
                  (item) => item.objective_id === node,
                ).length,
            )
            .join(" · ")}
        </div>
      ) : null}
      {payload.application_source_time !== undefined && (
        <div className="assignment">
          Applied · {payload.application_source_time.toFixed(1)}s
          {payload.expiry_source_time !== undefined
            ? " · expires " + payload.expiry_source_time.toFixed(1) + "s"
            : ""}
        </div>
      )}
    </>
  );
}
function LocalInsight({
  insight,
  evidence,
  onSelect,
}: {
  insight: Insight;
  evidence: Map<string, Event>;
  onSelect: (event: Event) => void;
}) {
  return (
    <>
      <div className="insight-header">
        <h2>{insight.title}</h2>
        <span className="status">Local check</span>
      </div>
      <div className="insight-source">
        Deterministic signal check · {insight.source_time.toFixed(1)}s
      </div>
      <p className="claim">{insight.summary}</p>
      {insight.alternative && (
        <p className="alternative">
          <strong>Alternative · </strong>
          {insight.alternative}
        </p>
      )}
      <EvidenceChips
        ids={insight.evidence_ids}
        evidence={evidence}
        onSelect={onSelect}
      />
      <p className="forecast-note">
        A local telemetry pattern check is not a model assessment or a statement
        of intent.
      </p>
    </>
  );
}
export function SessionPage({
  id,
  sourceName,
  review,
}: {
  id: string;
  sourceName: (session: Session) => string;
  review: Review | null;
}) {
  const [detail, setDetail] = useState<SessionDetail | null>(null),
    [replay, setReplay] = useState<Replay | null>(null),
    [view, setView] = useState("observer"),
    [error, setError] = useState(""),
    [time, setTime] = useState(0),
    [playing, setPlaying] = useState(false),
    [speed, setSpeed] = useState(1),
    [follow, setFollow] = useState(true),
    [selected, setSelected] = useState<Event | null>(null),
    [reviewMode, setReviewMode] = useState(
      Boolean(new URLSearchParams(location.hash.split("?")[1]).get("review")),
    );
  const canvas = useRef<HTMLCanvasElement>(null),
    clock = useRef<number | null>(null);
  const queryReview = new URLSearchParams(location.hash.split("?")[1]).get(
      "review",
    ),
    finding = review?.items.find(
      (item) => item.id === queryReview && item.session_id === id,
    );
  useEffect(() => {
    if (!finding) return;
    setTime(finding.source_time);
    setPlaying(false);
    setFollow(false);
    setReviewMode(true);
    setSelected(null);
  }, [finding?.id]);
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const [d, r] = await Promise.all([
          request<SessionDetail>("/sessions/" + encodeURIComponent(id)),
          request<Replay>(
            "/sessions/" + encodeURIComponent(id) + "/replay?view=" + view,
          ),
        ]);
        if (!alive) return;
        setDetail(d);
        setReplay(r);
        const selectedView = replayView(r.available_views, r.view, view);
        if (selectedView !== view) setView(selectedView);
        setError("");
      } catch (problem) {
        if (alive) setError((problem as Error).message);
      }
    }
    load();
    const interval = setInterval(load, 2500);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [id, view]);
  const frames = useMemo(
      () =>
        [...(replay?.frames || [])].sort(
          (a, b) => a.source_time - b.source_time,
        ),
      [replay],
    ),
    max = frames.at(-1)?.source_time || 0,
    active = latestFrame(frames, time),
    observationTime =
      active?.observation_source_time ?? active?.agent_cutoff_time;
  useEffect(() => {
    if (!replay || !frames.length) return;
    const live = ["running", "recording"].includes(replay.session.status);
    if (follow && live) setTime(max);
    else setTime((value) => Math.min(value, max));
  }, [max, replay?.session.status, follow]);
  useEffect(() => {
    if (!canvas.current || !active) return;
    drawFrame(canvas.current, active, view, selected || undefined);
    const redraw = () => {
      if (canvas.current)
        drawFrame(canvas.current, active, view, selected || undefined);
    };
    window.addEventListener("resize", redraw);
    document.fonts.ready.then(redraw);
    return () => window.removeEventListener("resize", redraw);
  }, [active, selected, view]);
  useEffect(() => {
    if (!playing) {
      clock.current = null;
      return;
    }
    let handle: number,
      current = time;
    const next = (now: number) => {
      const elapsed = clock.current === null ? 0 : now - clock.current;
      clock.current = now;
      current = Math.min(max, current + (elapsed / 1000) * speed);
      setTime(current);
      if (current >= max) {
        setPlaying(false);
        return;
      }
      handle = requestAnimationFrame(next);
    };
    handle = requestAnimationFrame(next);
    return () => {
      cancelAnimationFrame(handle);
      clock.current = null;
    };
  }, [playing, speed, max]);
  const allEvidence = useMemo(() => {
    const result = new Map<string, Event>();
    for (const event of [
      ...(detail?.events || []),
      ...(replay?.evidence || []),
    ]) {
      result.set(event.id, event);
      if (event.payload.original_id)
        result.set(event.payload.original_id, event);
      for (const record of event.payload.records || []) {
        result.set(record.id, {
          ...event,
          id: record.id,
          payload: {
            ...record,
            original_id: record.id,
            data: record.details,
            observed_at: record.observed_at,
            delivered_source_time: record.received_at,
            available_source_time:
              record.available_source_time ?? event.source_time,
            canonical_event_id: event.id,
          },
        });
      }
    }
    return result;
  }, [detail, replay]);
  const evidence = useMemo(
    () =>
      new Map(
        [...allEvidence].filter(([, event]) => availableAt(event) <= time),
      ),
    [allEvidence, time],
  );
  const assessments = (replay?.assessments || [])
    .filter((event) => event.source_time <= time)
    .sort(
      (a, b) =>
        (b.payload.application_source_time ?? b.source_time) -
        (a.payload.application_source_time ?? a.source_time),
    );
  const local = (detail?.insights || [])
      .filter((insight) => insight.source_time <= time)
      .sort((a, b) => b.source_time - a.source_time),
    primary = assessments[0],
    marks = [
      ...new Set(
        (replay?.assessments || []).map((event) =>
          event.payload.cutoff_tick === undefined
            ? event.source_time
            : event.payload.cutoff_tick / 10,
        ),
      ),
    ]
      .filter((value) => value <= max)
      .sort((a, b) => a - b);
  function jump(value: number) {
    setTime(value);
    setPlaying(false);
    setFollow(false);
  }
  function lifecycle(event: Event) {
    return adviceLifecycle(event, assessments, time);
  }
  function chooseEvidence(event: Event) {
    setSelected(event);
  }
  if (!detail || !replay)
    return (
      <>
        {error ? (
          <div className="notice-banner" role="alert">
            {error}
          </div>
        ) : (
          <div className="loading">Opening recorded telemetry…</div>
        )}
      </>
    );
  const scores = active?.scores
    ? Array.isArray(active.scores)
      ? active.scores.map((score, index) => ["Team " + index, score] as const)
      : Object.entries(active.scores)
    : [];
  const visibleSelection =
    selected && (reviewMode || evidence.has(selected.id));
  return (
    <>
      <PageHeading
        title={detail.session.name}
        description={sourceName(detail.session) + " · " + detail.session.id}
        eyebrow="Session workspace"
      >
        <Status value={detail.session.status} />
        <a href="#/sessions" className="text-button">
          All sessions
        </a>
      </PageHeading>
      {error && (
        <div className="notice-banner" role="alert">
          {error}
        </div>
      )}
      <div className="session-layout">
        <section className="surface">
          <div className="map-heading">
            <h3>Telemetry map</h3>
            {scores.length > 0 && (
              <div className="score" aria-label="Recorded scores">
                {scores.slice(0, 2).map(([label, value], index) => (
                  <React.Fragment key={label}>
                    <span>{label}</span>
                    <strong className={index ? "opponent" : ""}>{value}</strong>
                    {index === 0 && scores.length > 1 && <span>:</span>}
                  </React.Fragment>
                ))}
              </div>
            )}
          </div>
          <div className="map-tools">
            <span className="status">
              {view === "evaluator"
                ? "Evaluator ground truth"
                : view === "observer"
                  ? "Observer telemetry"
                  : "Reported telemetry"}
            </span>
            <select
              disabled={!replay.available_views?.length}
              value={replay.view || view}
              onChange={(event) => {
                setView(event.target.value);
                setSelected(null);
              }}
              aria-label="Information boundary"
            >
              {(replay.available_views?.length
                ? replay.available_views
                : [view]
              ).map((value) => (
                <option key={value} value={value}>
                  {value === "evaluator"
                    ? "Evaluator ground truth"
                    : value === "observer"
                      ? "Observer telemetry"
                      : "Reported telemetry"}
                </option>
              ))}
            </select>
          </div>
          {active ? (
            <>
              <div className="arena-map">
                <canvas
                  ref={canvas}
                  width={800}
                  height={800}
                  aria-label="Recorded simulation map. Positions and timestamps come from the selected telemetry view."
                />
              </div>
              <div className="map-legend">
                <span>
                  <i className="legend-dot" />
                  Reported entity
                </span>
                <span>
                  <i className="legend-dot report" />
                  Historical position
                </span>
                {Boolean(active.world?.nodes?.length) && (
                  <span>N0 / N1 / N2 · resources</span>
                )}
              </div>
              <div className="telemetry-caption">
                <div>
                  <strong>
                    Source time · {active.source_time.toFixed(1)}s
                  </strong>
                  {observationTime !== undefined && (
                    <> · Agent observation · {observationTime.toFixed(1)}s</>
                  )}
                </div>
                <div>
                  {view === "evaluator"
                    ? "Complete evaluator positions, including information unavailable to the agents."
                    : "Historical entity reports retain their own source times. Unknown positions remain unknown."}
                </div>
              </div>
            </>
          ) : (
            <div className="empty-state">
              <h2>Waiting for the first snapshot</h2>
              <p>
                This session has no recorded positions yet. Send a valid
                snapshot from the connected simulator.
              </p>
            </div>
          )}
          <div className="playback">
            <input
              type="range"
              min={0}
              max={max || 1}
              step={0.1}
              value={time}
              onChange={(event) => jump(Number(event.target.value))}
              disabled={!frames.length}
              aria-label="Session source time"
              aria-valuetext={time.toFixed(1) + " seconds"}
            />
            <div
              className="planning-marks"
              aria-label="Recorded assessment observations"
            >
              {marks.map((value) => (
                <button
                  key={value}
                  type="button"
                  style={{
                    left:
                      "calc(" +
                      (max ? (value / max) * 100 : 0) +
                      "% + " +
                      (5 - (max ? (value / max) * 10 : 0)) +
                      "px)",
                  }}
                  onClick={() => jump(value)}
                  aria-label={"Inspect observation at " + value + " seconds"}
                >
                  {value.toFixed(0)}s
                </button>
              ))}
            </div>
            <div className="playback-controls">
              <Button
                primary
                symbol={playing ? "pause" : "play"}
                disabled={!frames.length}
                onClick={() => {
                  setFollow(false);
                  if (time >= max) setTime(0);
                  setPlaying(!playing);
                }}
              >
                {playing ? "Pause" : "Play"}
              </Button>
              <Button
                symbol="restart"
                disabled={!frames.length}
                onClick={() => jump(0)}
              >
                Restart
              </Button>
              <select
                aria-label="Playback speed"
                value={speed}
                onChange={(event) => setSpeed(Number(event.target.value))}
              >
                {[0.25, 1, 4, 16, 64].map((value) => (
                  <option key={value} value={value}>
                    {value}×
                  </option>
                ))}
              </select>
              {["running", "recording"].includes(detail.session.status) && (
                <Button
                  onClick={() => {
                    setFollow(true);
                    setPlaying(false);
                    setTime(max);
                  }}
                >
                  Follow live
                </Button>
              )}
              <span className="playback-clock">
                {time.toFixed(1)} / {max.toFixed(1)}s
              </span>
            </div>
          </div>
        </section>
        <aside>
          <section className="surface insight">
            {finding && (
              <div className="insight-switch">
                <button
                  type="button"
                  className={reviewMode ? "selected" : ""}
                  onClick={() => setReviewMode(true)}
                >
                  Review finding
                </button>
                <button
                  type="button"
                  className={!reviewMode ? "selected" : ""}
                  onClick={() => setReviewMode(false)}
                >
                  At source time
                </button>
              </div>
            )}
            {finding && reviewMode ? (
              <>
                <div className="review-origin">
                  <span className={"priority " + finding.priority}>
                    Post-run review
                  </span>
                  <span>
                    {finding.origin === "local_check"
                      ? "Local check"
                      : "Evaluator outcome"}
                  </span>
                </div>
                <h2 className="review-detail-title">{finding.title}</h2>
                <p className="claim">{finding.summary}</p>
                <ForecastStrip item={finding} />
                <p className="alternative">
                  <strong>Why this matters · </strong>
                  {finding.why_it_matters}
                </p>
                <EvidenceChips
                  ids={finding.evidence_ids}
                  evidence={allEvidence}
                  onSelect={chooseEvidence}
                />
                {finding.alternatives?.map((alternative, index) => (
                  <details className="secondary-hypothesis" key={index}>
                    <summary>Recorded alternative {index + 1}</summary>
                    <p className="claim">{tidy(alternative)}</p>
                  </details>
                ))}
                <div className="review-next">
                  <div className="eyebrow">Next check</div>
                  <p>{finding.next_check}</p>
                </div>
                <Button
                  className="text-button"
                  onClick={() => {
                    jump(finding.source_time);
                    setReviewMode(false);
                  }}
                >
                  Inspect the original source time
                </Button>
              </>
            ) : primary ? (
              <ModelInsight
                event={primary}
                lifecycle={lifecycle(primary)}
                evidence={evidence}
                time={time}
                onSelect={chooseEvidence}
              />
            ) : local[0] ? (
              <LocalInsight
                insight={local[0]}
                evidence={evidence}
                onSelect={chooseEvidence}
              />
            ) : (
              <>
                <div className="insight-header">
                  <h2>Insights</h2>
                  <span className="status">Waiting for evidence</span>
                </div>
                <p className="claim">
                  {frames.length
                    ? "No assessment or local check is available at this source time."
                    : "Send telemetry to begin inspecting this session."}
                </p>
                <p className="forecast-note">
                  Arena shows recorded claims and traceable signal checks. It
                  does not fill missing evidence with a conclusion.
                </p>
              </>
            )}
            {!reviewMode && (assessments.length > 1 || local.length > 1) && (
              <div className="assessment-history">
                <div className="eyebrow">Recorded history</div>
                {assessments.slice(1).map((event) => (
                  <details className="history-record" key={event.id}>
                    <summary>
                      Assessment · {event.source_time.toFixed(1)}s{" "}
                      <span>{event.payload.origin || "imported"}</span>
                    </summary>
                    <ModelInsight
                      event={event}
                      lifecycle={lifecycle(event)}
                      evidence={evidence}
                      time={time}
                      onSelect={chooseEvidence}
                    />
                  </details>
                ))}
                {local.slice(primary ? 0 : 1, 4).map((insight) => (
                  <details className="history-record" key={insight.id}>
                    <summary>
                      {insight.title}
                      <span>Local check</span>
                    </summary>
                    <LocalInsight
                      insight={insight}
                      evidence={evidence}
                      onSelect={chooseEvidence}
                    />
                  </details>
                ))}
              </div>
            )}
          </section>
          {selected && (
            <>
              {visibleSelection ? (
                <Inspector event={selected} onClose={() => setSelected(null)} />
              ) : (
                <section className="surface evidence-inspector">
                  <p>
                    This evidence was not available at the selected source time.
                  </p>
                  <Button
                    className="text-button"
                    onClick={() => setSelected(null)}
                  >
                    Close evidence
                  </Button>
                </section>
              )}
            </>
          )}
        </aside>
      </div>
      <div className="workspace-status">
        <span>
          {view === "evaluator"
            ? "Evaluator boundary · complete recorded state"
            : "Telemetry boundary · reported information only"}
        </span>
        <span>
          {frames.length} playback snapshots ·{" "}
          {detail.session.event_count ?? detail.events.length} source events
        </span>
      </div>
    </>
  );
}
