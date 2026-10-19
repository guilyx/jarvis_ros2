// The rule builder's model and the rules YAML it writes (schemas/rules.schema.json): a trigger, a
// guard, actions and a policy, the shape most rules have. Anything else is written in YAML.

export const EVENT_KINDS = ["FAULT_STATE", "LOG", "STATE", "SYSTEM", "RECOVERY", "OPERATOR"] as const;
export type EventKind = (typeof EVENT_KINDS)[number];
export const FAULT_STATES = ["ACTIVE", "LATCHED", "CLEARED"] as const;
export type FaultState = (typeof FAULT_STATES)[number];

export interface Trigger {
  kind: EventKind;
  /** The event's code; `*` globs. */
  code: string;
  /** A fault's state (FAULT_STATE only). */
  state: FaultState;
  /** Only events of this component (empty: any). */
  component: string;
  /** Fire on the `threshold`-th match within `within` (a duration), not on each one. */
  repeat: { threshold: number; within: string } | null;
}

export type Action =
  | { type: "recover"; plan: string; args: Record<string, string> }
  | { type: "notify"; channel: string; message: string }
  | { type: "incident"; op: "open" | "escalate" }
  | { type: "ask_agent"; focus: string };

export interface RuleSpec {
  id: string;
  description: string;
  trigger: Trigger;
  /** One cooldown, count and rate limit per component (partition_by: source.component). */
  perComponent: boolean;
  /** Never while a safety fault is open (guard: faults_inactive: safety.*). */
  safetyGuard: boolean;
  actions: Action[];
  /** "" for none. */
  cooldown: string;
  maxFires: { count: number; per: string } | null;
  /** Shadow mode: decide, act on nothing. */
  dryRun: boolean;
}

/** What a plan's argument can take from the event that fired the rule. */
export const TEMPLATES = [
  { value: "{{ event.source.component }}", label: "the event's component" },
  { value: "{{ event.attrs.node }}", label: "the node the fault names" },
  { value: "{{ event.attrs.topic }}", label: "the topic the fault names" },
  { value: "{{ event.code }}", label: "the event's code" },
  { value: "{{ event.message }}", label: "the event's message" },
  { value: "{{ robot }}", label: "the robot" },
] as const;

export const ID_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;

export function newRule(): RuleSpec {
  return {
    id: "",
    description: "",
    trigger: { kind: "FAULT_STATE", code: "", state: "ACTIVE", component: "", repeat: null },
    perComponent: true,
    safetyGuard: true,
    actions: [],
    cooldown: "30s",
    maxFires: { count: 3, per: "1h" },
    dryRun: true,
  };
}

/** "Restart the lidar relay" -> "restart-the-lidar-relay". */
export function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48)
    .replace(/-+$/, "");
}

/** A plan's arguments, prefilled: from the event where the name says what it is, else the default. */
export function defaultArgs(
  params: Record<string, { default?: string | number | boolean; required?: boolean }>,
  trigger: Trigger,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [name, spec] of Object.entries(params)) {
    if (name === "component") out[name] = "{{ event.source.component }}";
    else if (name === "node") out[name] = "{{ event.attrs.node }}";
    else if (name === "topic") out[name] = "{{ event.attrs.topic }}";
    else if (name === "fault" && trigger.kind === "FAULT_STATE" && trigger.code) out[name] = trigger.code;
    else if (spec.required) out[name] = spec.default === undefined ? "" : String(spec.default);
  }
  return out;
}

/** What is missing before the rule can be tried; the backtest's lint has the last word. */
export function problems(spec: RuleSpec, required: (plan: string) => string[] = () => []): string[] {
  const out: string[] = [];
  if (!ID_PATTERN.test(spec.id)) out.push("a name: lowercase words joined by dashes");
  if (!spec.trigger.code.trim()) out.push("the code that triggers it");
  if (spec.trigger.repeat && !(spec.trigger.repeat.threshold >= 1)) out.push("how many times");
  if (!spec.actions.length) out.push("at least one thing to do");
  for (const a of spec.actions) {
    if (a.type === "recover") {
      if (!a.plan) out.push("which plan to run");
      for (const p of required(a.plan)) if (!a.args[p]?.trim()) out.push(`the plan's ${p}`);
    }
    if (a.type === "notify" && !a.message.trim()) out.push("the notification's message");
    if (a.type === "ask_agent" && !a.focus.trim()) out.push("what to ask the agent");
  }
  return out;
}

// -- YAML ------------------------------------------------------------------------------------------

const PLAIN = /^[A-Za-z0-9_][A-Za-z0-9_./*-]*$/;
const RESERVED = /^(true|false|null|yes|no|on|off|y|n|~)$/i;

/** A scalar as YAML: plain when it is safe, else double-quoted (JSON strings are YAML). */
export function scalar(v: string | number | boolean): string {
  if (typeof v !== "string") return String(v);
  if (PLAIN.test(v) && !RESERVED.test(v) && !/^[0-9.]+$/.test(v)) return v;
  return JSON.stringify(v);
}

function flow(entries: [string, string | number | boolean][]): string {
  return `{ ${entries.map(([k, v]) => `${k}: ${scalar(v)}`).join(", ")} }`;
}

function matcher(t: Trigger): string {
  const m: [string, string][] = [
    ["kind", t.kind],
    ["code", t.code.trim()],
  ];
  if (t.kind === "FAULT_STATE") m.push(["attrs.state", t.state]);
  if (t.component.trim()) m.push(["source.component", t.component.trim()]);
  return flow(m);
}

function action(a: Action): string[] {
  switch (a.type) {
    case "recover": {
      const args = Object.entries(a.args).filter(([, v]) => v.trim() !== "");
      return [
        "      - recover:",
        `          plan: ${scalar(a.plan)}`,
        ...(args.length ? [`          args: ${flow(args.map(([k, v]) => [k, v.trim()]))}`] : []),
      ];
    }
    case "notify":
      return [
        `      - notify: ${flow([
          ["channel", a.channel.trim() || "operators"],
          ["message", a.message],
        ])}`,
      ];
    case "incident":
      return [`      - incident: { ${a.op}: true }`];
    case "ask_agent":
      return [`      - ask_agent: ${flow([["focus", a.focus]])}`];
  }
}

/** The rule as a rules document, ready for the rule lab (and the robot's rules directory). */
export function ruleYaml(spec: RuleSpec): string {
  const lines = ["schema: jarvis.rules/v1", "rules:", `  - id: ${spec.id || "my-rule"}`];
  if (spec.description.trim()) lines.push(`    description: ${scalar(spec.description.trim())}`);
  if (spec.dryRun) lines.push("    dry_run: true");
  if (spec.perComponent) lines.push("    partition_by: [source.component]");
  lines.push("    when:");
  const t = spec.trigger;
  if (t.repeat) {
    lines.push("      count:", `        event: ${matcher(t)}`);
    lines.push(`        threshold: ${t.repeat.threshold}`, `        within: ${scalar(t.repeat.within)}`);
  } else {
    lines.push(`      event: ${matcher(t)}`);
  }
  if (spec.safetyGuard) lines.push('    guard: { faults_inactive: ["safety.*"] }');
  lines.push("    then:");
  for (const a of spec.actions) lines.push(...action(a));
  const policy: string[] = [];
  if (spec.cooldown) policy.push(`cooldown: ${scalar(spec.cooldown)}`);
  if (spec.maxFires)
    policy.push(`max_fires: { count: ${spec.maxFires.count}, per: ${scalar(spec.maxFires.per)} }`);
  if (policy.length) lines.push(`    policy: { ${policy.join(", ")} }`);
  return lines.join("\n") + "\n";
}
