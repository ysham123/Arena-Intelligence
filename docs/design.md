# Arena product design

Arena is a local workspace for teams testing agents in simulated environments. A user connects a simulator, records a session, and inspects decisions against the evidence available at the time. The first release supports the included fictional arena and an explicit HTTP adapter contract. It does not claim compatibility with a named robotics stack simply because that stack can send JSON.

## The user and the job

The primary user is an engineer responsible for an autonomy test. Their questions are concrete:

1. Is my simulator connected, and is the data current?
2. What changed during this run?
3. What did the agent conclude, and what observations supported it?
4. Was the recommendation applied, rejected, late, or replaced?
5. Can I reproduce the failure or give someone else the recording?

The product starts with these tasks. Model names, framework logos, agent counts, and benchmark numbers are not the home screen's organizing principle. A recorded session should explain the product before a user configures an API key or builds C++.

## First use

The initial workspace opens with a review queue built from two bundled, explicitly recorded reference sessions. The lead case is a high-probability forecast that missed its recorded outcome. Users can inspect it, compare the paired runs, or connect a simulator. The reference is labeled as a recording and retains its actual assessment history. It must never masquerade as a live connection.

Connecting a source requires a descriptive name, a generated credential, and the documented simulation event interface. The setup view shows the receiver address, adapter example, and receipt status. Creating a connection record does not prove connectivity. A source becomes active only after a valid event arrives. A quiet source must eventually show stale or waiting state rather than a permanent green indicator.

The app runs on the user's machine. The integration token is scoped to the local receiver; model credentials remain in the Python environment. The browser does not receive the Anthropic key. First use makes no paid inference calls.

## Navigation

| Destination | User purpose | Primary content |
| --- | --- | --- |
| Review | Prioritize an investigation | Recorded failures, confident forecast misses, evidence, alternatives, and a next check |
| Compare | Assess a pair of runs | Condition match, outcome, coverage denominators, latency, cost, and limitations |
| Sessions | Find a run | Source, session state, time, recorded data, open action |
| Connections | Attach and diagnose a simulator | Receiver contract, token setup, last event, validation feedback |
| Session workspace | Understand what happened | Spatial playback, evidence, assessment lifecycle, time controls |
| Integration guide | Make the first event arrive | Install/start steps, working adapter, versioned event contract |

Navigation remains stable between empty, live, and recorded states. Empty views explain the next action. Errors preserve user input and offer a concrete recovery path. A local runtime failure should have an inline state with retry; it should not erase the workspace.

## Session workspace

The spatial view and decision review share a timeline. Selecting a moment controls what is shown. An old sighting remains an old sighting even when the player reaches a later time. The source timestamp and receipt timestamp are separate so transport delay cannot be mistaken for recent evidence.

One assessment receives enough space to read. Its structure is claim, evidence, alternative explanation, forecast, and outcome or lifecycle state. Evidence links open the underlying observation. A cited ID establishes a reference, not proof of the conclusion. Imported agent assessments and locally computed checks are labeled by their origin.

Raw event JSON is available for debugging, but does not dominate the default view. Internal prompts, chain-of-thought text, queue internals, and cost accounting are not a substitute for a useful explanation. The UI shows the recorded concise rationale and testable outputs, not hidden model reasoning.

For the built-in arena, the observer view contains own-team telemetry, public scores, and received opponent reports. Evaluator ground truth requires an explicit view selection. Generic adapters are responsible for sending the permitted simulation observation stream; Arena cannot reconstruct a simulator's private visibility policy from arbitrary coordinates.

## Review priorities

The review queue uses a disclosed rule rather than an invented readiness score. A failed run or a missed forecast with at least 75% assigned probability is high priority. Uncovered forecasts, heavily reduced horizons, and unusual source cadence are medium priority. Other diagnostics are informational. The queue is a starting point for investigation, not a release approval.

Comparison requires a scenario fingerprint before labeling conditions as matched. Even then, different assignments can produce different later observations. The view shows accuracy with its denominators, cost, latency, and missing values; it does not infer causality from one pair. Post-run evaluator outcomes are explicitly identified.

## Insights worth showing

- A supported change in observed occupancy or objective preference, with the time window and evidence that changed.
- A forecast with a defined horizon, all outcome probabilities, and coverage. An unavailable outcome stays unavailable.
- A stale report, missing expected update, invalid event, timeout, or rejected assignment that affects interpretation.
- A repeated stationary position, when a defined window supports it, labeled as a signal to inspect rather than a diagnosis.
- An explicit contradiction between a forecast and an observed outcome when both are recorded and comparable.

No generic confidence meter summarizes the whole system. No invented threat score, personality inference, or ungrounded prediction fills a blank panel. The first release concerns synthetic simulation entities and test results.

## Visual system

Use a warm, light workbench with a compact dark navigation rail. Typography, alignment, and information density create the character. IBM Plex Sans carries labels and prose; IBM Plex Mono is used sparingly for time, IDs, and code. These fonts are packaged locally with their license.

The primary accent is deep teal. Color communicates state and always has a text or shape equivalent. Warnings use restrained amber; failures use muted red. A border separates adjacent surfaces more often than a floating card. The map is a working instrument rather than decorative hero art.

Use complete words, short labels, tabular numerical alignment, consistent spacing, and clear focus rings. Avoid decorative radar sweeps, glowing edges, invented acronyms, nonfunctional search, fabricated teams, and status chips with no underlying measurement. Motion follows playback or a user action. Respect reduced-motion preferences.

At desktop width, navigation stays fixed and the work area has breathing room. At narrow widths, controls wrap, tables scroll within their own container, and the session map precedes evidence. There must be no page-wide horizontal overflow. Keyboard users must reach navigation, setup forms, playback controls, and evidence with visible focus.

## Implementation choices and references

The product uses React and TypeScript, built with Vite into packaged browser assets, a local Python HTTP service, persistent records, and native Canvas. The release includes the compiled frontend inside the Python wheel, so consumers do not need a Node build. Frontend development has its own locked dependencies and type check. The browser uses the service's typed interface; it does not access local files or model credentials directly.

[React](https://react.dev/learn/creating-a-react-app) supplies the component architecture; [Vite](https://vite.dev/guide/) builds the local bundle. [Radix Primitives](https://www.radix-ui.com/primitives/docs/overview/accessibility) was considered for complex focus-managed components. The current app uses native controls and tests those behaviors directly. [IBM Plex](https://github.com/IBM/plex) supplies the typography and Lucide supplies interface icons. No design library is claimed as an installed dependency unless it is actually bundled.

The product workflow is informed by [Foxglove's separation of sources, playback, and connection problems](https://docs.foxglove.dev/docs/visualization) and [Rerun's SDK-to-recording-to-viewer workflow](https://rerun.io/docs/getting-started/data-in). These are reference patterns, not supported integrations. [USWDS design principles](https://designsystem.digital.gov/design-principles/) inform the emphasis on user tasks, trust, and accessibility.

## Acceptance criteria

- A fresh installation opens the product without a C++ build or API key.
- The bundled reference is immediately reviewable and visibly recorded.
- A user can create a source, send events with the sample adapter, and inspect a persisted session.
- Incorrect credentials, malformed events, duplicate sequence numbers, and unavailable runtime states produce understandable errors.
- The UI makes no external resource requests and contains no provider credentials.
- Desktop, tablet, and narrow mobile views remain usable; setup and review work with the keyboard.
- The wheel and source archive include the UI, fonts, reference data, and integration instructions.
- Integration capabilities and engineering results are described at their measured scope. The previous arena experiments remain historical results of the frozen inference configuration.
