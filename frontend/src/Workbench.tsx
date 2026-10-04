import React, { useEffect, useRef, useState } from "react";
import { request } from "./api";
import {
  Button,
  PageHeading,
  Status,
  Empty,
  date,
  numeric,
  icons,
} from "./Components";
import { drawFrame } from "./Workspace";
import type {
  Session,
  Connection,
  Review,
  ReviewItem,
  Comparison,
  Frame,
} from "./types";

function origin(value: string) {
  return value === "local_check"
    ? "Local signal check"
    : value === "recorded_outcome"
      ? "Post-run evaluator result"
      : "Recorded model assessment";
}
export function ForecastStrip({ item }: { item: ReviewItem }) {
  const forecast = item.forecast;
  if (!forecast?.probabilities?.length) return null;
  const probabilities = forecast.probabilities,
    top = probabilities.indexOf(Math.max(...probabilities));
  return (
    <div className="forecast review-forecast">
      <div className="forecast-title">
        <span>
          Recorded forecast{" "}
          {forecast.cutoff_tick !== undefined
            ? "· " +
              (forecast.cutoff_tick / 10).toFixed(0) +
              "s → " +
              ((forecast.horizon_tick || 0) / 10).toFixed(0) +
              "s"
            : ""}
        </span>
        <span>
          Actual ·{" "}
          {forecast.outcome === 3 ? "none" : "node " + forecast.outcome}
        </span>
      </div>
      <div className="forecast-columns">
        {probabilities.map((value, index) => (
          <div
            key={index}
            className={"forecast-column" + (top === index ? " high" : "")}
          >
            <span>{index === 3 ? "None" : "Node " + index}</span>
            <strong>{(value * 100).toFixed(0)}%</strong>
            <div className="forecast-bar">
              <span style={{ width: value * 100 + "%" }} />
            </div>
          </div>
        ))}
      </div>
      {item.evaluation_boundary && (
        <p className="forecast-note">{item.evaluation_boundary}</p>
      )}
    </div>
  );
}
export function ReviewCard({
  item,
  compact = false,
}: {
  item: ReviewItem;
  compact?: boolean;
}) {
  return (
    <article className={"priority-review" + (compact ? " compact" : "")}>
      <div className="review-origin">
        <span className={"priority " + item.priority}>
          {item.priority === "high"
            ? "High priority"
            : item.priority === "medium"
              ? "Review"
              : "Information"}
        </span>
        <span>{origin(item.origin)}</span>
      </div>
      <h2>{item.title}</h2>
      <p className="review-summary">{item.summary}</p>
      <a
        href={
          "#/session/" +
          item.session_id +
          "?review=" +
          encodeURIComponent(item.id)
        }
        className="button-link review-inspect"
      >
        Inspect evidence <icons.arrow className="icon" aria-hidden />
      </a>
      {!compact && (
        <>
          <ForecastStrip item={item} />
          <div className="review-reason">
            <div className="eyebrow">Why this matters</div>
            <p>{item.why_it_matters}</p>
          </div>
          {item.alternatives?.length ? (
            <details className="counterevidence">
              <summary>Alternative explanations</summary>
              {item.alternatives.map((value, index) => (
                <p key={index}>{value}</p>
              ))}
            </details>
          ) : null}
          <div className="review-next">
            <div className="eyebrow">Next check</div>
            <p>{item.next_check}</p>
          </div>
        </>
      )}
      <div className="review-footer">
        <span>{item.session_name}</span>
        <span>Source time · {item.source_time.toFixed(1)}s</span>
      </div>
    </article>
  );
}
export function Overview({
  sessions,
  connections,
  review,
  runtime,
  sourceName,
  onConnect,
  onLaunch,
  launching,
  referenceAvailable,
  onNewSession,
}: {
  sessions: Session[];
  connections: Connection[];
  review: Review | null;
  runtime: boolean;
  sourceName: (session: Session) => string;
  onConnect: () => void;
  onLaunch: () => void;
  launching: boolean;
  referenceAvailable: boolean;
  onNewSession: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const item =
    review?.items.find((value) => value.id === selected) || review?.items[0];
  const preview = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (!preview.current) return;
    fetch("/app/reference.json")
      .then((response) => response.json())
      .then((frame: Frame) => {
        if (preview.current) drawFrame(preview.current, frame, "observer");
      })
      .catch(() => {});
  }, [sessions.length]);
  return (
    <>
      <PageHeading
        title="Review workspace"
        description="Find the test runs that need attention. Inspect the evidence before the next release."
        eyebrow="Autonomy engineering"
      >
        <Button symbol="plus" onClick={onConnect}>
          Connect simulator
        </Button>
        <Button primary symbol="plus" onClick={onNewSession}>
          New session
        </Button>
        <Button
          symbol="arrow"
          onClick={onLaunch}
          disabled={launching || !referenceAvailable}
        >
          {launching ? "Opening reference…" : "Open reference"}
        </Button>
      </PageHeading>
      {sessions.length ? (
        <>
          <div className="workspace-summary">
            <span>
              <strong>{review?.sessions_reviewed ?? sessions.length}</strong>{" "}
              sessions in review
            </span>
            <span>
              <strong>{review?.counts.high ?? 0}</strong> high-priority findings
            </span>
            <span>
              <strong>
                {
                  sessions.filter((value) =>
                    ["running", "recording"].includes(value.status),
                  ).length
                }
              </strong>{" "}
              active runs
            </span>
            <span className="scope-note">
              Recorded outcomes and local checks
            </span>
          </div>
          {item ? (
            <div className="review-grid">
              <section className="surface">
                <div className="surface-heading">
                  <h3>Priority review</h3>
                  <span className="eyebrow">Evidence before decisions</span>
                </div>
                <ReviewCard item={item} />
              </section>
              <aside className="surface review-queue">
                <div className="surface-heading">
                  <h3>Review queue</h3>
                  <span className="queue-count">
                    {review?.items.length ?? 0}
                  </span>
                </div>
                {review?.items.slice(0, 7).map((finding, index) => (
                  <button
                    className={
                      "queue-item" + (finding.id === item.id ? " selected" : "")
                    }
                    key={finding.id}
                    onClick={() => setSelected(finding.id)}
                  >
                    <span className="queue-number">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <span>
                      <span className="queue-title">{finding.title}</span>
                      <span className="queue-session">
                        {finding.session_name}
                      </span>
                      <span className={"priority " + finding.priority}>
                        {finding.priority === "high"
                          ? "High priority"
                          : finding.priority === "medium"
                            ? "Review"
                            : "Information"}
                      </span>
                    </span>
                    <icons.chevron className="icon" aria-hidden />
                  </button>
                ))}
                <div className="queue-method">
                  {review?.method ||
                    "Checks use recorded telemetry and outcomes."}
                </div>
              </aside>
            </div>
          ) : (
            <Empty
              symbol="review"
              title="No review findings available"
              description="Recorded outcomes and local signal checks appear here when their supporting events are available. Arena does not invent a readiness score."
            />
          )}
          <section className="surface recent-runs">
            <div className="surface-heading">
              <h2>Sessions</h2>
              <a href="#/compare" className="text-button">
                Compare runs
              </a>
            </div>
            <table className="session-table">
              <thead>
                <tr>
                  <th>Test run</th>
                  <th>Status</th>
                  <th>Source</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {sessions.slice(0, 5).map((session) => (
                  <tr key={session.id}>
                    <td>
                      <a href={"#/session/" + session.id}>{session.name}</a>
                      <span className="subtitle">{session.id}</span>
                    </td>
                    <td>
                      <Status value={session.status} />
                    </td>
                    <td>{sourceName(session)}</td>
                    <td>{date(session.updated_at || session.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      ) : (
        <div className="overview-grid">
          <section className="surface setup">
            <div className="eyebrow">Bring your test runs into view</div>
            <p className="setup-intro">
              Connect a source.
              <br />
              Keep every conclusion traceable.
            </p>
            <div className="setup-step">
              <span className="step-number">01</span>
              <div>
                <h3>Local workspace</h3>
                <p>
                  Telemetry, source credentials, and recorded sessions stay in
                  the local data directory.
                </p>
                <span className="complete-label">
                  <icons.check className="icon" aria-hidden />
                  {runtime
                    ? "Runtime available"
                    : "Waiting for the local service"}
                </span>
              </div>
            </div>
            <div className="setup-step">
              <span className="step-number">02</span>
              <div>
                <h3>Connect your simulator</h3>
                <p>
                  Create an authenticated source and send versioned snapshots,
                  evidence, and assessments.
                </p>
                <Button primary symbol="plus" onClick={onConnect}>
                  Create connection
                </Button>
              </div>
            </div>
            <div className="setup-step">
              <span className="step-number">03</span>
              <div>
                <h3>Inspect a recorded test</h3>
                <p>
                  Start with a real recorded arena session. Review a priority
                  shift, delayed evidence, and a high-confidence forecast miss.
                </p>
                <Button
                  onClick={onLaunch}
                  disabled={launching || !referenceAvailable}
                >
                  Open recorded reference
                </Button>
              </div>
            </div>
          </section>
          <section className="surface reference">
            <div className="eyebrow">Included reference</div>
            <h2>A changing-priority arena</h2>
            <p>
              Recorded fictional telemetry with analyst and challenger
              assessments. Use it to explore the workflow before connecting your
              own system.
            </p>
            <canvas
              ref={preview}
              width={440}
              height={440}
              aria-label="Known terrain and initial friendly telemetry from the recorded reference"
            />
            <div className="reference-meta">
              <span>Actual recorded source</span>
              <span>180s · seed 101</span>
            </div>
            <div className="button-row">
              <Button
                symbol="arrow"
                onClick={onLaunch}
                disabled={launching || !referenceAvailable}
              >
                Open reference
              </Button>
            </div>
          </section>
        </div>
      )}
      <div className="info-strip">
        <span>
          <strong>
            {connections.length
              ? connections.length +
                " source" +
                (connections.length === 1 ? "" : "s") +
                " in workspace"
              : "Your simulator supplies the record."}
          </strong>{" "}
          Arena preserves the observation boundary and checks the evidence.
        </span>
        <a href="#/guide" className="text-button">
          Integration contract <span aria-hidden="true">↗</span>
        </a>
      </div>
    </>
  );
}
export function ComparePage({ sessions }: { sessions: Session[] }) {
  const [left, setLeft] = useState(sessions[0]?.id || ""),
    [right, setRight] = useState(sessions[1]?.id || ""),
    [comparison, setComparison] = useState<Comparison | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  async function compare() {
    setLoading(true);
    setError("");
    try {
      setComparison(
        await request<Comparison>(
          "/compare?left=" +
            encodeURIComponent(left) +
            "&right=" +
            encodeURIComponent(right),
        ),
      );
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setLoading(false);
    }
  }
  return (
    <>
      <PageHeading
        title="Compare runs"
        description="Compare measured outcomes under matching simulation conditions. Missing measurements remain explicit."
        eyebrow="Release review"
      />
      <section className="surface compare-select">
        <label className="field">
          <span>Baseline run</span>
          <select
            value={left}
            onChange={(event) => {
              setLeft(event.target.value);
              setComparison(null);
            }}
          >
            <option value="">Choose a session</option>
            {sessions.map((session) => (
              <option key={session.id} value={session.id}>
                {session.name}
              </option>
            ))}
          </select>
        </label>
        <span className="compare-versus">vs</span>
        <label className="field">
          <span>Candidate run</span>
          <select
            value={right}
            onChange={(event) => {
              setRight(event.target.value);
              setComparison(null);
            }}
          >
            <option value="">Choose a session</option>
            {sessions.map((session) => (
              <option key={session.id} value={session.id}>
                {session.name}
              </option>
            ))}
          </select>
        </label>
        <Button
          primary
          symbol="compare"
          onClick={compare}
          disabled={loading || !left || !right || left === right}
        >
          {loading ? "Comparing…" : "Compare"}
        </Button>
      </section>
      {error && (
        <div className="notice-banner" role="alert">
          {error}
        </div>
      )}
      {comparison ? (
        <section className="surface comparison-result">
          <div className="comparison-verdict">
            <div className="eyebrow">
              {comparison.comparable
                ? "Matching recorded conditions"
                : "Conditions differ"}
            </div>
            <h2>{comparison.verdict.title}</h2>
            <p>{comparison.verdict.detail}</p>
          </div>
          <div
            className="comparison-table-wrap"
            tabIndex={0}
            aria-label="Measured run comparison, scroll horizontally on smaller screens"
          >
            <table className="comparison-table">
              <thead>
                <tr>
                  <th>Measured outcome</th>
                  <th>{comparison.left.name}</th>
                  <th>{comparison.right.name}</th>
                  <th>Difference</th>
                </tr>
              </thead>
              <tbody>
                {comparison.rows.map((row) => (
                  <tr key={row.key}>
                    <td>{row.label}</td>
                    <td>{numeric(row.left, row.unit, row.key)}</td>
                    <td>{numeric(row.right, row.unit, row.key)}</td>
                    <td
                      className={
                        row.delta !== null &&
                        comparison.comparable &&
                        row.lower_is_better !== null
                          ? row.delta === 0
                            ? "neutral"
                            : row.delta < 0 === row.lower_is_better
                              ? "improvement"
                              : "regression"
                          : ""
                      }
                    >
                      {row.delta === null
                        ? "Not measured"
                        : (row.delta > 0 ? "+" : "") +
                          numeric(row.delta, row.unit, row.key)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {comparison.limitations.length > 0 && (
            <div className="comparison-limitations">
              <h3>Interpretation limits</h3>
              {comparison.limitations.map((limit, index) => (
                <p key={index}>{limit}</p>
              ))}
            </div>
          )}
        </section>
      ) : (
        <Empty
          symbol="compare"
          title={
            sessions.length < 2
              ? "A comparison needs two recorded runs"
              : "Select the conditions you want to compare"
          }
          description="Arena checks whether native scenarios match. It reports measured coverage, forecast quality, latency, score, and cost only when those values are recorded."
        />
      )}
    </>
  );
}
