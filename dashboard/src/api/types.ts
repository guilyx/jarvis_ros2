// Domain shapes: schemas/*.schema.json (events, faults, executions) and the lake's views (history).
// The generated `schema.d.ts` types the routes; these type what flows through them.

export const SEVERITIES = ["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"] as const;
export type Severity = (typeof SEVERITIES)[number];
export const SEVERITY_LEVEL: Record<Severity, number> = {
  DEBUG: 10,
  INFO: 20,
  WARN: 30,
  ERROR: 40,
  CRITICAL: 50,
};

export const KINDS = [
  "LOG",
  "STATE",
  "FAULT_RAISE",
  "FAULT_CLEAR",
  "FAULT_STATE",
  "RULE_MATCH",
  "RECOVERY",
  "INSIGHT",
  "OPERATOR",
  "SYSTEM",
] as const;
export type Kind = (typeof KINDS)[number] | "UNSPECIFIED";

export type AttrValue = string | number | boolean;

export interface Source {
  robot: string;
  node: string;
  component: string;
  host: string;
}

export interface JarvisEvent {
  id: string;
  seq: string; // decimal: a uint64 does not fit a JS number
  stamp: string;
  recv_stamp?: string;
  source: Source;
  kind: Kind;
  code: string;
  severity: Severity;
  message?: string;
  attrs?: Record<string, AttrValue>;
  tags?: string[];
  payload?: unknown;
  payload_schema?: string;
  correlation_id?: string;
  causation_id?: string;
}

export type FaultState = "PENDING" | "ACTIVE" | "LATCHED" | "CLEARED";

export interface Fault {
  fault_id: string;
  code: string;
  source: Source;
  reporting_sources?: string[];
  state: FaultState;
  severity: Severity;
  occurrences: number;
  first_seen: string | null;
  last_seen?: string | null;
  confirmed_at?: string | null;
  cleared_at?: string | null;
  acknowledged: boolean;
  acknowledged_by: string;
  latch: boolean;
  cataloged?: boolean;
  muted_by: string;
  incident_id: string;
  snapshot_ref?: string;
  description?: string;
  message?: string;
  freeze_frame?: Record<string, AttrValue>;
}

export type ExecutionState =
  | "PENDING_APPROVAL"
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "ABORTED"
  | "REJECTED"
  | "EXPIRED";
export type Requester = "OPERATOR" | "RULE" | "AGENT" | "PLAN";

export interface Gate {
  gate: string;
  passed: boolean;
  detail?: string;
}

export interface Step {
  id: string;
  action: string;
  state: "PENDING" | "RUNNING" | "RETRYING" | "SUCCEEDED" | "FAILED" | "SKIPPED" | "ABORTED";
  attempts?: number;
  started?: string | null;
  finished?: string | null;
  message?: string;
}

export interface Execution {
  execution_id: string;
  plan_id: string;
  plan_rev?: string;
  args: Record<string, string>;
  requester: { type: Requester; id?: string };
  correlation_id?: string;
  causation_id?: string;
  rationale?: string;
  confidence?: number | null;
  dry_run: boolean;
  state: ExecutionState;
  reason?: string;
  gates: Gate[];
  needs_approval?: boolean;
  approved_by?: string;
  locks?: string[];
  steps: Step[];
  current_step?: number;
  failed_step?: string;
  created: string | null;
  started?: string | null;
  finished?: string | null;
  expires?: string | null;
  depth?: number;
  parent_id?: string;
  ladder_of?: string;
  ladder?: { running?: boolean };
  escalated?: boolean;
}

export interface ExecutionsSnapshot {
  mode: RecoveryMode;
  executions: Execution[];
  recent: Execution[];
}

export type RecoveryMode = "ENABLED" | "DRY_RUN" | "DISABLED" | "UNKNOWN";

// -- history (the lake's views) -------------------------------------------------------------------

export interface FaultEpisode {
  fault_id: string;
  code: string;
  component: string;
  severity_level: number;
  first_seen: string;
  confirmed_at: string | null;
  cleared_at: string | null;
  occurrences: number | null;
  muted: boolean;
  incident_id: string;
  last_state: FaultState;
}

export interface FaultTransition {
  ts: string;
  seq: number;
  state: FaultState;
  prev_state: string;
  occurrences: number | null;
  message: string;
  severity: Severity;
}

export type IncidentState = "OPEN" | "RECOVERING" | "ESCALATED" | "RESOLVED" | "CLOSED";

export interface Incident {
  incident_id: string;
  component: string;
  title: string;
  severity_level: number;
  opened_at: string;
  escalated_at: string | null;
  resolved_at: string | null;
  closed_at: string | null;
  escalated: boolean;
  state: IncidentState;
}

export interface IncidentDetail extends Incident {
  timeline: JarvisEvent[];
  executions: ExecutionRecord[];
  faults: FaultEpisode[];
}

export interface ExecutionRecord {
  execution_id: string;
  plan: string;
  requester: Requester;
  requester_id: string;
  dry_run: boolean;
  parent_id: string | null;
  ladder_of: string | null;
  incident_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  outcome: ExecutionState | null;
  escalated: boolean;
  last_phase: string;
  events?: JarvisEvent[];
}

export interface ParamSpec {
  type?: string;
  required?: boolean;
  default?: string | number | boolean;
  pattern?: string;
  one_of?: string[];
  description?: string;
}

export interface PlanStats {
  plan: string;
  runs: number;
  succeeded: number;
  failed: number;
  not_run: number;
  escalated: number;
  mean_duration_s: number | null;
  success_rate: number | null;
}

export interface Plan {
  id: string;
  description: string;
  rev: string;
  file: string;
  params: Record<string, ParamSpec>;
  allowed_requesters: Requester[];
  requires_approval: Requester[];
  agent_policy: string;
  steps: string[];
  timeout_s: number;
  stats?: PlanStats | null;
  executions?: ExecutionRecord[];
}

export interface Rule {
  id: string;
  description: string;
  file: string;
  enabled: boolean;
  dry_run: boolean;
  fires?: number;
  dry_fires?: number;
  suppressed?: number;
  cooled_down?: number;
  guard_blocked?: number;
  action_failures?: number;
  partitions?: number;
  fires_window: number;
  dry_run_fires_window: number;
  last_fire: string | null;
}

export interface RuleMatch {
  ts: string;
  seq: number;
  id: string;
  rule: string;
  dry_run: boolean;
  component: string;
  incident_id: string;
  causation_id: string;
}

export interface Kpis {
  window: { since: string; until: string };
  events: number;
  warnings_or_worse: number;
  faults_confirmed: number;
  mttr_s: number | null;
  incidents_opened: number;
  incidents_open: number;
  incidents_escalated: number;
  auto_recovery_rate: number | null;
  recoveries_finished: number;
  recovery_success_rate: number | null;
}

export interface RatePoint {
  bucket: string;
  severity: Severity;
  events: number;
}

export interface TopCode {
  code: string;
  episodes: number;
  mttr_s: number | null;
  cumulative_share: number;
}

export interface Stats {
  hours: number;
  kpis: Kpis;
  faults_open: Record<Severity, number>;
  event_rate: RatePoint[];
  top_codes: TopCode[];
  plans: PlanStats[];
}

export interface ComponentHealth {
  component: string;
  events: number;
  last_event: string;
  faults: number;
  open_faults: number;
  mttr_s: number | null;
  last_fault: string | null;
  worst_severity: Severity;
  mtbf_h: number | null;
}

export interface HeatCell {
  component: string;
  bucket: string;
  faults: number;
}

export interface Flapping {
  code: string;
  component: string;
  cycles: number;
  mean_active_s: number | null;
  first: string;
  last: string;
}

export interface RuleFires {
  rule: string;
  fires: number;
  dry_run_fires: number;
  last_fire: string | null;
}

export interface Bag {
  kind: "recording" | "blackbox";
  bag: string;
  file: string;
  size: number;
  start: string | null;
  end: string | null;
  open: boolean;
  converted: boolean;
  incidents: string[];
}

export interface LakeStatus {
  updated_at?: number; // unix seconds
  converted_total?: number;
  last_conversion?: { split?: string; rows?: number; at?: number; [key: string]: unknown } | null;
  last_error?: string | null;
  splits?: number;
  splits_converted?: number;
  splits_pending?: number;
  splits_failed?: Record<string, string>;
  bags_bytes?: number;
  lake_bytes?: number;
  blackbox_bytes?: number;
  partitions?: string[];
  rows?: number;
}

export interface Health {
  status: "ok" | "degraded" | "starting";
  connected: boolean;
  faults_snapshot: boolean;
  executions_snapshot: boolean;
  last_event_age_s: number | null;
  robot?: string;
  lake?: LakeStatus | null;
}

export interface FleetRobot {
  name: string;
  url: string; // its gateway (and dashboard); empty for this one
  online: boolean;
  local?: boolean; // this gateway's own robot
  robot?: string;
  status?: string;
  connected?: boolean;
  latency_ms?: number | null;
  error?: string | null; // why it is offline
  mode: RecoveryMode;
  faults: { CRITICAL?: number; ERROR?: number; WARN?: number; total?: number };
  worst?: {
    code: string;
    severity: string;
    state: string;
    component: string;
    message: string;
    fault_id: string;
  } | null;
  incidents: number;
  latched: number;
}

export interface Delivery {
  webhook: string;
  channel: string;
  event_id: string;
  attempts: number;
  ok: boolean;
  status: number | null;
  error: string;
  at: number;
}

export interface SystemInfo {
  version: string;
  robot: string;
  uptime_s: number;
  connected: boolean;
  events_received: number;
  events_dropped?: number;
  recent_events: number;
  stream_clients: number;
  components: Record<string, { state: string; since: string }>;
  lake: LakeStatus | null;
  notifications: Delivery[];
  webhooks: string[];
}

export type Role = "viewer" | "operator" | "admin";

export interface Me {
  name: string;
  role: Role;
}

export interface Outcome {
  success: boolean;
  message: string;
  data: Record<string, unknown>;
}

export interface LintFinding {
  level: "error" | "warning";
  message: string;
}

// -- the rule lab (POST /rules:backtest) ------------------------------------------------------------

export interface BacktestCause {
  id: string;
  kind: string;
  code: string;
  component: string;
  severity: string;
  message: string;
  at: string;
}

export interface BacktestMatch {
  at: string;
  rule: string;
  match_id: string;
  cause_id: string;
  cause?: BacktestCause | null; // null: the cause was another rule's event, emitted in the replay
  dry_run?: boolean;
}

/** A decision one side made and the other didn't. No cause: it was another rule's event. */
export interface BacktestDecision {
  rule: string;
  cause_id: string;
  cause?: BacktestCause | null;
}

export interface BacktestRecovery {
  at: string;
  rule: string;
  plan: string;
  args?: Record<string, string>;
  execution_id?: string;
  state: string;
  reason: string;
}

export interface BacktestRule {
  rule: string;
  fires: number;
  dry_fires: number;
  suppressed: number;
  cooled_down: number;
  guard_blocked: number;
}

export interface Backtest {
  success: boolean;
  message: string;
  findings?: LintFinding[];
  events: number;
  from?: string | null;
  to?: string | null;
  hours: number;
  truncated: boolean;
  replaces?: string[]; // the draft's rules the robot already has: compared with what they did live
  rules?: BacktestRule[];
  matches?: BacktestMatch[];
  match_count: number;
  recoveries?: BacktestRecovery[];
  missing?: BacktestDecision[]; // decided live, not by the draft
  extra?: BacktestDecision[]; // decided by the draft, not live
}

// -- the agent (schemas/insight.schema.json) --------------------------------------------------------

export interface InsightHypothesis {
  cause: string;
  confidence: number;
  evidence: string[];
  counter_evidence?: string[];
  flagged?: boolean;
}

export interface InsightRecommendation {
  kind: "RECOVER" | "INVESTIGATE" | "CONFIGURE" | "ESCALATE";
  text: string;
  plan_id?: string;
  args?: Record<string, string>;
  confidence?: number;
}

export interface Insight {
  insight_id: string;
  trigger: { type: string; ref?: string };
  window: { from: string; to: string; event_count: number; correlation_id?: string };
  title: string;
  summary: string;
  assessment: Severity;
  hypotheses: InsightHypothesis[];
  recommendations: InsightRecommendation[];
  proposals?: string[];
  degraded: boolean;
  analyst: "heuristic" | "llm";
  flags?: string[];
  facts?: string[];
  model?: {
    provider: string;
    name: string;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  } | null;
  created?: string;
}

/** The insight an INSIGHT event carries (the gateway decodes the payload), or null. */
export function insightOf(e: JarvisEvent): Insight | null {
  const p = e.payload as Partial<Insight> | undefined;
  return p && typeof p === "object" && typeof p.title === "string" ? (p as Insight) : null;
}

export function insightConfidence(i: Insight): number {
  return Math.max(0, ...i.hypotheses.map((h) => h.confidence));
}

/** Who answered, and what the model cost: "openrouter/x/y · 12.4k tokens · $0.0042". */
export function analystLabel(i: Insight): string {
  if (i.analyst === "heuristic") {
    return i.model ? `heuristic analyst (${i.model.name} could not answer)` : "heuristic analyst (no model)";
  }
  if (!i.model) return "model";
  const tokens = (i.model.input_tokens + i.model.output_tokens) / 1000;
  const parts = [i.model.name, `${tokens.toFixed(1)}k tokens`];
  if (i.model.cost_usd > 0) {
    parts.push(`$${i.model.cost_usd.toFixed(i.model.cost_usd < 0.01 ? 4 : 2)}`);
  }
  return parts.join(" · ");
}
