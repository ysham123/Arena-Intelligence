# Arena Intelligence 0.2.0

Arena is a simulation and evaluation workspace for defense autonomy engineering. This release connects a deterministic C++20 arena, asynchronous Python reasoning agents, and a local React/TypeScript product for reviewing evidence and outcomes.

## Included

- An installable local application with recorded sessions, spatial replay, evidence-linked review, and run comparison.
- Authenticated HTTP ingestion, persistent sessions, and a working standard-library Python adapter for synthetic simulators.
- A deterministic C++20 reference environment with filtered observations, atomic objective assignments, deadline recovery, and exact replay.
- An analyst/challenger workflow with structured output, local evidence checks, semantic validation, and a persistent spending ceiling.
- Frozen experiment reports, paired reference recordings, raw experiment records, and reproduction tools.

## Downloads

| File | Use |
| --- | --- |
| `arena-intelligence-local-0.2.0.zip` | Recommended installation kit; includes the wheel, installer, and adapter examples |
| `arena_intelligence-0.2.0-py3-none-any.whl` | Python package for an existing environment |
| `arena-intelligence-source.zip` | Source, documentation, reports, compiled UI, and demo recording |
| `arena-intelligence-experiments.zip` | Complete frozen experiment records; expands into the source archive's `runs/` tree |
| `arena-intelligence-release.json` | SHA-256 checksums for the four artifacts above |

The first installation downloads Python dependencies. Recorded sessions and external adapters require no API key, frontend build, or native compiler. New native matches require the optional C++ build. Instructions are in `docs/install.md`.

## Verification and scope

The local release checks passed 131 Python tests and five frontend tests. A fresh macOS installation served the bundled interface and both reference sessions, accepted a synthetic adapter stream, and preserved its records after restart. A real paced native run completed 900 ticks and three applied scripted assessments. No additional paid model calls were used for the product redesign.

The recorded matched-input study produced 120/144 correct forecasts for analyst plus challenger and 119/144 for the single agent. That one-forecast difference does not establish a reliable advantage. Full latency, cost, coverage, closed-loop outcomes, and native performance methods are in the engineering report.

This release supports fictional simulation data and local, single-user engineering review. It does not provide operational military analysis or native integrations with named defense or robotics platforms. There is no affiliation with or endorsement by Anduril. Linux/macOS CI and Linux sanitizers run separately in GitHub Actions; the product validation report records the local checks.
