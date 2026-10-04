import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { request, post } from "./api";
import {
  Button,
  Status,
  PageHeading,
  Modal,
  Empty,
  date,
  icons,
} from "./Components";
import { Overview, ComparePage } from "./Workbench";
import { SessionPage } from "./Workspace";
import type {
  Health,
  Settings,
  Connection,
  Session,
  Review,
  Capabilities,
} from "./types";
import "../style.css";

const navigation = [
  ["overview", "Review workspace"],
  ["sessions", "Sessions"],
  ["connections", "Connections"],
  ["compare", "Compare runs"],
  ["guide", "Integration guide"],
  ["settings", "Runtime settings"],
] as const;
function App() {
  const [path, setPath] = useState(location.hash.slice(1) || "/overview"),
    [health, setHealth] = useState<Health | null>(null),
    [settings, setSettings] = useState<Settings | null>(null),
    [connections, setConnections] = useState<Connection[]>([]),
    [sessions, setSessions] = useState<Session[]>([]),
    [review, setReview] = useState<Review | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [menu, setMenu] = useState(false),
    [connectionForm, setConnectionForm] = useState(false),
    [instructions, setInstructions] = useState<Connection | null>(null),
    [toast, setToast] = useState(""),
    [launching, setLaunching] = useState(false),
    [newSession, setNewSession] = useState(false);
  const [page, id] = path.split("?")[0].split("/").filter(Boolean);
  const section = page === "session" ? "sessions" : page;
  async function refresh() {
    const results = await Promise.allSettled([
      request<Health>("/health"),
      request<{ connections: Connection[] }>("/connections"),
      request<{ sessions: Session[] }>("/sessions"),
      request<Settings>("/settings"),
      request<Review>("/review"),
    ]);
    const [h, c, s, t, r] = results;
    if (h.status === "fulfilled") {
      setHealth(h.value);
      setError("");
    } else {
      setHealth(null);
      setError(h.reason.message);
    }
    if (c.status === "fulfilled") setConnections(c.value.connections);
    if (s.status === "fulfilled") setSessions(s.value.sessions);
    if (t.status === "fulfilled") setSettings(t.value);
    if (r.status === "fulfilled") setReview(r.value);
    setLoading(false);
  }
  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 4000);
    const change = () => {
      setPath(location.hash.slice(1) || "/overview");
      setMenu(false);
      requestAnimationFrame(() =>
        document.getElementById("main")?.focus({ preventScroll: true }),
      );
    };
    window.addEventListener("hashchange", change);
    return () => {
      clearInterval(interval);
      window.removeEventListener("hashchange", change);
    };
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timeout = setTimeout(() => setToast(""), 5500);
    return () => clearTimeout(timeout);
  }, [toast]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.querySelector("dialog[open]")) {
        setMenu(false);
        document.querySelector<HTMLButtonElement>(".mobile-menu")?.focus();
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, []);
  async function launch() {
    setLaunching(true);
    try {
      const result = await post<{ session: Session }>("/demo", {
        name: "Reference arena / changing priorities",
        mode: "recorded",
      });
      location.hash = "/session/" + result.session.id;
      refresh();
    } catch (problem) {
      setToast((problem as Error).message);
    } finally {
      setLaunching(false);
    }
  }
  const runtime = Boolean(health?.ok),
    referenceAvailable = Boolean(
      health?.capabilities?.recorded_sample ||
      health?.capabilities?.native_demo,
    );
  function sourceName(session: Session) {
    return (
      connections.find((item) => item.id === session.connection_id)?.name ||
      session.connection_name ||
      "Recorded simulator"
    );
  }
  const sessionRows = (items: Session[]) => (
    <table className="session-table">
      <thead>
        <tr>
          <th>Session</th>
          <th>Status</th>
          <th>Source</th>
          <th>Updated</th>
        </tr>
      </thead>
      <tbody>
        {items.map((session) => (
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
  );
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to workspace
      </a>
      <div className="app-shell">
        <aside className={"sidebar" + (menu ? " open" : "")} id="navigation">
          <a href="#/overview" className="brand">
            <svg viewBox="0 0 24 25" aria-hidden="true">
              <path
                d="M3 22 12 3l9 19M7 15h10"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              />
            </svg>
            <span>ARENA</span>
          </a>
          <div className="brand-caption">Autonomy test workspace</div>
          <nav className="navigation" aria-label="Workspace navigation">
            {navigation.map(([key, label]) => {
              const Icon = icons[key === "compare" ? "compare" : key];
              return (
                <React.Fragment key={key}>
                  {key === "guide" && <div className="nav-divider" />}
                  <a
                    href={"#/" + key}
                    className={"nav-link" + (section === key ? " current" : "")}
                    aria-current={section === key ? "page" : undefined}
                  >
                    <Icon className="icon" aria-hidden />
                    <span>{label}</span>
                  </a>
                </React.Fragment>
              );
            })}
          </nav>
          <div className="sidebar-bottom">
            <strong>Local workspace</strong>
            <div className="runtime-line">
              <span className={"status-dot" + (!runtime ? " offline" : "")} />
              <span>
                {runtime
                  ? "Service available · v" + health?.version
                  : "Service unavailable"}
              </span>
            </div>
          </div>
        </aside>
        <div className="workspace-shell">
          <div className="topbar">
            <div className="topbar-left">
              <button
                className="mobile-menu button-icon"
                type="button"
                aria-controls="navigation"
                aria-expanded={menu}
                onClick={() => setMenu(!menu)}
              >
                <icons.menu className="icon" aria-hidden />
                Navigation
              </button>
              <span className="crumb-prefix">Workspace /</span>
              <span className="crumb-destination">
                {navigation.find(([key]) => key === section)?.[1] || "Session"}
              </span>
            </div>
            <div className="topbar-right">
              <span className={"status-dot" + (!runtime ? " offline" : "")} />
              <span>
                {runtime ? "Local service online" : "Local service offline"}
              </span>
              <span>127.0.0.1</span>
            </div>
          </div>
          <main id="main" className="content" tabIndex={-1}>
            {error && (
              <div className="notice-banner" role="alert">
                <span>The local service is unavailable. {error}</span>
                <Button onClick={refresh} className="text-button">
                  Retry
                </Button>
              </div>
            )}
            {loading ? (
              <div className="loading">Opening the local workspace…</div>
            ) : (
              <>
                {(!page || page === "overview") && (
                  <Overview
                    sessions={sessions}
                    connections={connections}
                    review={review}
                    runtime={runtime}
                    sourceName={sourceName}
                    onConnect={() => setConnectionForm(true)}
                    onLaunch={launch}
                    launching={launching}
                    referenceAvailable={referenceAvailable}
                    onNewSession={() => setNewSession(true)}
                  />
                )}
                {page === "sessions" && (
                  <>
                    <PageHeading
                      title="Sessions"
                      description="Recorded and incoming test runs from your connected simulators."
                      eyebrow="Test runs"
                    >
                      <Button
                        primary
                        symbol="plus"
                        onClick={() => setNewSession(true)}
                      >
                        New session
                      </Button>
                      <Button
                        onClick={launch}
                        disabled={launching || !referenceAvailable}
                        symbol="arrow"
                      >
                        {launching
                          ? "Opening reference…"
                          : "Open reference session"}
                      </Button>
                    </PageHeading>
                    {sessions.length ? (
                      <section className="surface">
                        {sessionRows(sessions)}
                      </section>
                    ) : (
                      <Empty
                        title="Your first run belongs here"
                        description="Send snapshots through a connection, or open the recorded reference. New sessions appear when actual telemetry arrives."
                      >
                        <Button
                          primary
                          onClick={launch}
                          disabled={launching || !referenceAvailable}
                        >
                          Open reference session
                        </Button>
                      </Empty>
                    )}
                  </>
                )}
                {page === "connections" && (
                  <>
                    <PageHeading
                      title="Connections"
                      description="Authenticate a synthetic simulator and bring its telemetry into this workspace."
                      eyebrow="Sources"
                    >
                      <Button
                        primary
                        symbol="plus"
                        onClick={() => setConnectionForm(true)}
                      >
                        New connection
                      </Button>
                    </PageHeading>
                    {connections.length ? (
                      <div className="connections-list">
                        {connections.map((connection) => (
                          <article
                            key={connection.id}
                            className="surface connection-row"
                          >
                            <div>
                              <h3>{connection.name}</h3>
                              <p>
                                {connection.description ||
                                  "Versioned synthetic telemetry · simulation-v1"}
                              </p>
                              <p>{connection.id}</p>
                              <Status value={connection.status} />
                              {connection.last_seen_at && (
                                <p>
                                  Last received ·{" "}
                                  {date(connection.last_seen_at)}
                                </p>
                              )}
                            </div>
                            <div className="connection-actions">
                              <Button
                                onClick={() => setInstructions(connection)}
                              >
                                Instructions
                              </Button>
                              <Button
                                symbol="signal"
                                onClick={async () => {
                                  try {
                                    const result = await post<{
                                      message: string;
                                    }>(
                                      "/connections/" + connection.id + "/test",
                                    );
                                    setToast(result.message);
                                    refresh();
                                  } catch (problem) {
                                    setToast((problem as Error).message);
                                  }
                                }}
                              >
                                Check source
                              </Button>
                            </div>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <Empty
                        symbol="connections"
                        title="No simulator connected"
                        description="Create a source to receive an integration token and a local ingestion endpoint. Your simulator supplies the telemetry; Arena records and checks it."
                      >
                        <Button
                          primary
                          symbol="plus"
                          onClick={() => setConnectionForm(true)}
                        >
                          Create connection
                        </Button>
                      </Empty>
                    )}
                  </>
                )}
                {page === "guide" && (
                  <Guide onConnect={() => setConnectionForm(true)} />
                )}
                {page === "settings" && (
                  <Runtime health={health} settings={settings} />
                )}
                {page === "compare" && <ComparePage sessions={sessions} />}
                {page === "session" && id && (
                  <SessionPage
                    key={id}
                    id={decodeURIComponent(id)}
                    sourceName={sourceName}
                    review={review}
                  />
                )}
              </>
            )}
          </main>
        </div>
      </div>
      {newSession && (
        <NewSessionDialog
          capabilities={health?.capabilities || {}}
          onClose={() => setNewSession(false)}
          onCreated={(session) => {
            setNewSession(false);
            location.hash = "/session/" + session.id;
            refresh();
          }}
        />
      )}
      {connectionForm && (
        <ConnectionDialog
          onClose={() => {
            setConnectionForm(false);
            refresh();
          }}
          onNotify={setToast}
        />
      )}{" "}
      {instructions && (
        <Modal
          title={instructions.name + " / integration"}
          onClose={() => setInstructions(null)}
        >
          <Instructions
            connection={instructions}
            onDone={() => setInstructions(null)}
            onNotify={setToast}
          />
        </Modal>
      )}
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </>
  );
}

function NewSessionDialog({
  capabilities,
  onClose,
  onCreated,
}: {
  capabilities: Capabilities;
  onClose: () => void;
  onCreated: (session: Session) => void;
}) {
  const [mode, setMode] = useState(
      capabilities.native_demo ? "native" : "recorded",
    ),
    [backend, setBackend] = useState("mock"),
    [name, setName] = useState(""),
    [consent, setConsent] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const paid = ["single", "multi"].includes(backend),
    available =
      mode === "recorded"
        ? capabilities.recorded_sample
        : capabilities.native_demo;
  return (
    <Modal title="New simulation session" onClose={onClose}>
      <p>
        Start a local fictional arena test or open a recorded reference.
        Connected simulators create their own sessions through the ingestion
        endpoint.
      </p>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError("");
          try {
            const result = await post<{ session: Session }>("/demo", {
              name: name.trim() || undefined,
              mode,
              backend,
              allow_paid: mode === "native" && paid && consent,
            });
            onCreated(result.session);
          } catch (problem) {
            setError((problem as Error).message);
            setBusy(false);
          }
        }}
      >
        <div className="new-session-options">
          <label>
            <input
              type="radio"
              name="session-mode"
              value="native"
              checked={mode === "native"}
              onChange={() => setMode("native")}
              disabled={!capabilities.native_demo}
            />
            <span>
              <strong>Live native arena</strong>
              <small>
                Run a 90-second local C++ simulation with the selected reasoning
                pipeline.
                {!capabilities.native_demo
                  ? " Native runtime unavailable."
                  : ""}
              </small>
            </span>
          </label>
          <label>
            <input
              type="radio"
              name="session-mode"
              value="recorded"
              checked={mode === "recorded"}
              onChange={() => setMode("recorded")}
              disabled={!capabilities.recorded_sample}
            />
            <span>
              <strong>Recorded reference</strong>
              <small>
                Open an actual completed match with its original evidence and
                assessments. No model calls.
              </small>
            </span>
          </label>
        </div>
        <label className="field">
          <span>Session name</span>
          <input
            placeholder="Optional test-run label"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
          />
        </label>
        {mode === "native" && (
          <>
            <label className="field">
              <span>Reasoning pipeline</span>
              <select
                value={backend}
                onChange={(event) => {
                  setBackend(event.target.value);
                  setConsent(false);
                }}
              >
                <option value="mock">Scripted pipeline · free</option>
                <option value="frequency">Frequency baseline · free</option>
                <option
                  value="single"
                  disabled={!capabilities.paid_native_available}
                >
                  Single analyst · model calls
                </option>
                <option
                  value="multi"
                  disabled={!capabilities.paid_native_available}
                >
                  Analyst + challenger · model calls
                </option>
              </select>
              <small>
                {capabilities.paid_native_available
                  ? "Model credentials are available in the local runtime."
                  : "Model pipelines require credentials configured in the local runtime."}
              </small>
            </label>
            {paid && (
              <label className="paid-consent">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(event) => setConsent(event.target.checked)}
                />
                <span>
                  Run paid model calls for this session, subject to the existing
                  local cost ledger and project ceiling.
                </span>
              </label>
            )}
          </>
        )}
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
        <div className="dialog-footer">
          <Button onClick={onClose}>Cancel</Button>
          <Button
            primary
            type="submit"
            disabled={
              busy || !available || (mode === "native" && paid && !consent)
            }
          >
            {busy
              ? "Starting…"
              : mode === "native"
                ? "Start session"
                : "Open reference"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ConnectionDialog({
  onClose,
  onNotify,
}: {
  onClose: () => void;
  onNotify: (message: string) => void;
}) {
  const [created, setCreated] = useState<{
      connection: Connection;
      integration_token: string;
      ingest_url: string;
    } | null>(null),
    [name, setName] = useState(""),
    [description, setDescription] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal
      title={created ? "Your source is ready" : "Connect a simulator"}
      onClose={onClose}
    >
      {created ? (
        <Instructions
          connection={created.connection}
          token={created.integration_token}
          ingestUrl={created.ingest_url}
          onDone={onClose}
          onNotify={onNotify}
        />
      ) : (
        <>
          <p>
            Create a source for synthetic telemetry. Arena issues a
            connection-specific token and records source time separately from
            receipt time.
          </p>
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setError("");
              try {
                const result = await post<{
                  connection: Connection;
                  integration_token: string;
                  ingest_url: string;
                }>("/connections", {
                  name: name.trim(),
                  description: description.trim(),
                  adapter: "simulation-v1",
                });
                setCreated(result);
              } catch (problem) {
                setError((problem as Error).message);
                setBusy(false);
              }
            }}
          >
            <label className="field">
              <span>Source name</span>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Navigation bench"
                maxLength={120}
                autoFocus
                required
              />
            </label>
            <label className="field">
              <span>Description</span>
              <input
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Environment or test rig, optional"
                maxLength={500}
              />
            </label>
            <div className="code-block">
              <div className="eyebrow">simulation-v1</div>
              <p>Authenticated HTTP ingestion · local loopback</p>
            </div>
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            <div className="dialog-footer">
              <Button onClick={onClose}>Cancel</Button>
              <Button type="submit" primary disabled={busy}>
                {busy ? "Creating…" : "Create connection"}
              </Button>
            </div>
          </form>
        </>
      )}
    </Modal>
  );
}
function Instructions({
  connection,
  token,
  ingestUrl = location.origin + "/api/ingest",
  onDone,
  onNotify,
}: {
  connection: Connection;
  token?: string;
  ingestUrl?: string;
  onDone: () => void;
  onNotify: (message: string) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  return (
    <>
      <p>
        {token
          ? "Save this integration token now. It is shown once and removed from the browser when this dialog closes."
          : "Use the integration token saved when this source was created. Saved tokens are not retrievable."}
      </p>
      {token && (
        <>
          <div
            className="token-display"
            aria-label={
              revealed ? "Integration token" : "Hidden integration token"
            }
          >
            {revealed ? token : "•••• •••• •••• •••• •••• ••••"}
          </div>
          <div className="button-row">
            <Button
              symbol="copy"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(token);
                  onNotify("Integration token copied.");
                } catch {
                  onNotify("Clipboard unavailable. Reveal and copy the token.");
                }
              }}
            >
              Copy token
            </Button>
            <Button onClick={() => setRevealed(!revealed)}>
              {revealed ? "Hide token" : "Reveal token"}
            </Button>
          </div>
        </>
      )}
      <div className="onboarding-step">
        <h3>1. Send a synthetic snapshot</h3>
        <p>
          Source · {connection.name}. Source time comes from the simulator;
          receipt time is recorded independently.
        </p>
        <pre className="code-block">{integrationExample(ingestUrl)}</pre>
      </div>
      <div className="onboarding-step">
        <h3>2. Inspect the session</h3>
        <p>
          The source stays waiting until its first valid event arrives. Sessions
          shows actual telemetry, imported assessments, and local signal checks.
        </p>
      </div>
      <div className="dialog-footer">
        <Button primary onClick={onDone}>
          Done
        </Button>
      </div>
    </>
  );
}
export function integrationExample(url: string) {
  return `curl -X POST ${url} \\\n  -H "Authorization: Bearer <integration-token>" \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "schema_version": 1,\n    "scope": "synthetic_simulation",\n    "session": {"external_id": "bench-001",\n                "name": "Navigation bench / 001",\n                "status": "running"},\n    "events": [{\n      "sequence": 1, "source_time": 0,\n      "type": "snapshot",\n      "payload": {\n        "world": {"width": 32, "height": 32},\n        "visibility": "reported",\n        "entities": [{"id": "bot-1", "x": 4, "y": 7,\n                      "team": "blue", "source_time": 0}]\n      }\n    }]\n  }'`;
}
function Guide({ onConnect }: { onConnect: () => void }) {
  return (
    <>
      <PageHeading
        title="Connect your simulator"
        description="A versioned telemetry boundary for synthetic autonomy tests."
        eyebrow="Integration guide"
      >
        <Button primary symbol="plus" onClick={onConnect}>
          Create connection
        </Button>
      </PageHeading>
      <div className="guide-grid">
        <article className="surface article">
          <h2>Log a run. Review the record.</h2>
          <p>
            Arena accepts synthetic snapshots, evidence, and assessments through
            an authenticated local endpoint. It preserves source time, receipt
            time, and the evidence behind each claim.
          </p>
          <ol>
            <li>Create a source and save its one-time integration token.</li>
            <li>
              Send versioned events with increasing sequence numbers and
              simulator source times.
            </li>
            <li>
              Link assessments to evidence event IDs. Mark the session completed
              when your run ends.
            </li>
            <li>
              Review the record, inspect failures, and compare matching test
              conditions.
            </li>
          </ol>
          <h3>Example snapshot</h3>
          <pre className="code-block">
            {integrationExample(location.origin + "/api/ingest")}
          </pre>
          <h3>Keep the boundary explicit</h3>
          <p>
            Reported positions are telemetry. Imported model assessments and
            deterministic local checks retain separate labels. A missing report
            remains an unknown; it does not establish absence.
          </p>
        </article>
        <aside className="surface guide-note">
          <h2>Local by default</h2>
          <p>
            The service binds to loopback. Records live in the configured data
            directory. This interface does not start paid model calls.
          </p>
          <h3>Synthetic environments</h3>
          <p>
            Use fictional simulation telemetry. Arena provides analysis and
            replay, with no real-world control or deployment actions.
          </p>
          <a className="text-button" href="#/settings">
            Inspect runtime configuration
          </a>
        </aside>
      </div>
    </>
  );
}
function Runtime({
  health,
  settings,
}: {
  health: Health | null;
  settings: Settings | null;
}) {
  const rows = [
    ["Version", health?.version || "Unavailable"],
    [
      "Service address",
      settings ? settings.bind_host + ":" + settings.port : location.host,
    ],
    ["Data directory", settings?.data_directory || "Unavailable"],
    ["Telemetry scope", settings?.scope || "Unavailable"],
    [
      "Native simulation runtime",
      health?.capabilities?.native_demo ? "Available" : "Unavailable",
    ],
    [
      "Recorded sample",
      health?.capabilities?.recorded_sample ? "Available" : "Unavailable",
    ],
  ];
  return (
    <>
      <PageHeading
        title="Local runtime"
        description="The installed workspace and its recording limits."
        eyebrow="Runtime settings"
      />
      <section className="surface">
        <dl className="settings-list">
          {rows.map(([label, value]) => (
            <div className="setting" key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <p className="settings-foot">
        Runtime settings are configured when the service starts. Integration
        secrets are managed by each source and are never stored in browser
        storage.
      </p>
      {settings?.limits && (
        <section className="surface article" style={{ marginTop: 22 }}>
          <h2>Recording limits</h2>
          <pre className="code-block">
            {JSON.stringify(settings.limits, null, 2)}
          </pre>
        </section>
      )}
    </>
  );
}
createRoot(document.getElementById("app")!).render(<App />);
