export interface Health {
  ok: boolean;
  version: string;
  scope: string;
  capabilities: Capabilities;
  limits?: Record<string, unknown>;
}
export interface Capabilities {
  native_demo?: boolean;
  recorded_sample?: boolean;
  ingestion?: boolean;
  paid_native_available?: boolean;
}
export interface Settings {
  bind_host: string;
  port: number;
  data_directory: string;
  scope: string;
  capabilities: Capabilities;
  limits?: Record<string, unknown>;
}
export interface Connection {
  id: string;
  name: string;
  adapter: string;
  description?: string;
  status: string;
  last_seen_at?: string;
  created_at?: string;
  event_count?: number;
}
export interface Session {
  id: string;
  name: string;
  connection_id?: string;
  connection_name?: string;
  status: string;
  origin?: string;
  created_at?: string;
  updated_at?: string;
  event_count?: number;
  last_source_time?: number;
  external_id?: string;
  visibility?: string;
}
export interface Entity {
  id: string;
  x: number;
  y: number;
  team?: string | number;
  label?: string;
  source_time?: number;
  objective_id?: number;
  visibility?: string;
}
export interface World {
  width: number;
  height: number;
  blocked?: number[][];
  nodes?: { id: number; x: number; y: number }[];
}
export interface Hypothesis {
  claim: string;
  alternative?: string;
  evidence_ids: string[];
}
export interface Forecast {
  horizon_tick?: number;
  horizon_source_time?: number;
  probabilities: number[];
}
export interface EvidenceRecord {
  id: string;
  kind: string;
  observed_at: number;
  received_at: number;
  available_source_time?: number;
  details: Record<string, unknown>;
  summary?: string;
  entity_id?: string;
}
export interface EventPayload {
  records?: EvidenceRecord[];
  observed_at?: number;
  delivered_source_time?: number;
  available_source_time?: number;
  assignments?: { bot_id: number; objective_id: number }[];
  entities?: Entity[];
  world?: World;
  scores?: number[] | Record<string, number>;
  visibility?: string;
  status?: string;
  origin?: string;
  summary?: string;
  hypotheses?: Hypothesis[];
  forecast?: Forecast;
  request_id?: string;
  cutoff_tick?: number;
  application_source_time?: number;
  expiry_source_time?: number;
  accepted?: boolean;
  original_id?: string;
  observed_tick?: number;
  delivered_tick?: number;
  kind?: string;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface Event {
  id: string;
  session_id: string;
  sequence: number;
  source_time: number;
  received_at: string;
  type: string;
  payload: EventPayload;
}
export interface Frame {
  sequence: number;
  source_time: number;
  entities: Entity[];
  world?: World;
  scores?: number[] | Record<string, number>;
  visibility?: string;
  observation_source_time?: number;
  agent_cutoff_time?: number;
}
export interface Insight {
  id: string;
  kind: string;
  origin: string;
  severity: string;
  title: string;
  summary: string;
  source_time: number;
  evidence_ids: string[];
  details?: Record<string, unknown>;
  alternative?: string;
}
export interface SessionDetail {
  session: Session;
  events: Event[];
  insights: Insight[];
  summary: Record<string, unknown>;
  replay_url?: string;
}
export interface Replay {
  session: Session;
  frames: Frame[];
  evidence: Event[];
  assessments: Event[];
  summary: Record<string, unknown>;
  available_views?: string[];
  view?: string;
  coverage?: unknown;
}
export interface ReviewItem {
  id: string;
  session_id: string;
  session_name: string;
  kind: string;
  priority: "high" | "medium" | "info";
  title: string;
  summary: string;
  why_it_matters: string;
  next_check: string;
  origin: "recorded_assessment" | "recorded_outcome" | "local_check";
  source_time: number;
  evidence_ids: string[];
  request_id?: string;
  status: string;
  assessment?: { hypotheses: Hypothesis[] };
  alternatives?: string[];
  forecast?: ReviewForecast;
  evaluation_boundary?: string;
}
export interface Review {
  items: ReviewItem[];
  counts: { high: number; medium: number; info: number };
  sessions_reviewed: number;
  method: string;
}
export interface Comparison {
  left: { id: string; name: string; metrics: Record<string, unknown> };
  right: { id: string; name: string; metrics: Record<string, unknown> };
  comparable: boolean;
  limitations: string[];
  rows: {
    key: string;
    label: string;
    left: number | null;
    right: number | null;
    delta: number | null;
    unit: string;
    lower_is_better: boolean | null;
  }[];
  verdict: { title: string; detail: string };
}
export interface ReviewForecast {
  probabilities?: number[];
  outcome?: number;
  prediction?: number;
  cutoff_tick?: number;
  horizon_tick?: number;
  latency_seconds?: number;
  remaining_horizon_ticks?: number;
  application_tick?: number;
  status?: string;
  brier_score?: number;
}
