import { Bell, FlaskConical, Pencil, Play, Siren, Sparkles, X } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useCatalog, usePlans } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  EVENT_KINDS,
  FAULT_STATES,
  TEMPLATES,
  defaultArgs,
  newRule,
  problems,
  ruleYaml,
  slug,
  type Action,
  type EventKind,
  type RuleSpec,
} from "@/lib/ruleBuilder";

const KIND_LABEL: Record<EventKind, string> = {
  FAULT_STATE: "A fault",
  LOG: "A log line",
  STATE: "A state",
  SYSTEM: "A Jarvis event",
  RECOVERY: "A recovery event",
  OPERATOR: "An operator's action",
};
const WITHIN = ["1m", "5m", "10m", "30m", "1h"];
const COOLDOWNS = [
  { value: "", label: "none" },
  ...["5s", "30s", "1m", "5m", "15m", "1h"].map((v) => ({ value: v, label: v })),
];
const PER = ["10m", "1h", "1d"];

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-fg">
        {title}
        {hint ? (
          <span className="ml-2 font-normal normal-case tracking-normal opacity-80">{hint}</span>
        ) : null}
      </h3>
      {children}
    </section>
  );
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm">
      <Switch checked={checked} onCheckedChange={onChange} label={label} />
      {label}
    </label>
  );
}

/**
 * A rule from a form: what triggers it, what it does, how often. It writes the YAML (shown as it
 * changes) for the rule lab to backtest, or to edit further by hand.
 */
export function RuleBuilder({
  onBacktest,
  onEdit,
  busy,
  canRun,
}: {
  /** `replay` is the rule as if live, so the backtest shows what it would ask for; `keep` is the
   * rule as built (in shadow mode, if so). */
  onBacktest: (replay: string, keep: string) => void;
  onEdit: (yaml: string) => void;
  busy: boolean;
  canRun: boolean;
}) {
  const catalog = useCatalog();
  const plans = usePlans();
  const [spec, setSpec] = useState<RuleSpec>(newRule);
  const [idTouched, setIdTouched] = useState(false);
  const set = (patch: Partial<RuleSpec>) => setSpec((s) => ({ ...s, ...patch }));
  const setTrigger = (patch: Partial<RuleSpec["trigger"]>) =>
    setSpec((s) => ({ ...s, trigger: { ...s.trigger, ...patch } }));
  const setAction = (i: number, a: Action) =>
    setSpec((s) => ({ ...s, actions: s.actions.map((x, j) => (j === i ? a : x)) }));
  const removeAction = (i: number) =>
    setSpec((s) => ({ ...s, actions: s.actions.filter((_, j) => j !== i) }));
  const addAction = (a: Action) => setSpec((s) => ({ ...s, actions: [...s.actions, a] }));

  const planList = plans.data?.plans ?? [];
  const planById = useMemo(() => new Map(planList.map((p) => [p.id, p])), [planList]);
  const required = (id: string) =>
    Object.entries(planById.get(id)?.params ?? {})
      .filter(([, p]) => p.required)
      .map(([name]) => name);
  const missing = problems(spec, required);
  const yaml = ruleYaml(spec);
  const t = spec.trigger;
  const fault = t.kind === "FAULT_STATE";

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,460px)]" data-testid="rule-builder">
      <Card>
        <CardBody className="space-y-5">
          <Section title="When">
            <div className="flex flex-wrap items-end gap-2">
              <label>
                <Label>Trigger</Label>
                <Select
                  aria-label="Trigger"
                  value={t.kind}
                  onChange={(e) => setTrigger({ kind: e.target.value as EventKind })}
                >
                  {EVENT_KINDS.map((k) => (
                    <option key={k} value={k}>
                      {KIND_LABEL[k]}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="min-w-56 flex-1">
                <Label hint="(* globs)">Code</Label>
                <Input
                  className="w-full font-mono"
                  aria-label="Code"
                  list="rule-builder-codes"
                  placeholder={fault ? "sensor.scan.stale" : "ros.log.warn"}
                  value={t.code}
                  onChange={(e) => setTrigger({ code: e.target.value })}
                />
                <datalist id="rule-builder-codes">
                  {(catalog.data?.faults ?? []).map((f) => (
                    <option key={f.code} value={f.code}>
                      {f.description}
                    </option>
                  ))}
                </datalist>
              </label>
              {fault ? (
                <label>
                  <Label>Becomes</Label>
                  <Select
                    aria-label="Becomes"
                    value={t.state}
                    onChange={(e) => setTrigger({ state: e.target.value as RuleSpec["trigger"]["state"] })}
                  >
                    {FAULT_STATES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </Select>
                </label>
              ) : null}
              <label>
                <Label hint="(optional)">On component</Label>
                <Input
                  aria-label="On component"
                  placeholder="any"
                  value={t.component}
                  onChange={(e) => setTrigger({ component: e.target.value })}
                />
              </label>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Toggle
                checked={!!t.repeat}
                onChange={(on) => setTrigger({ repeat: on ? { threshold: 3, within: "10m" } : null })}
                label="Only when it keeps happening"
              />
              {t.repeat ? (
                <>
                  <Input
                    type="number"
                    min={1}
                    className="w-16"
                    aria-label="Times"
                    value={t.repeat.threshold}
                    onChange={(e) =>
                      setTrigger({ repeat: { ...t.repeat!, threshold: Number(e.target.value) } })
                    }
                  />
                  <span className="text-muted-fg">times within</span>
                  <Select
                    aria-label="Within"
                    value={t.repeat.within}
                    onChange={(e) => setTrigger({ repeat: { ...t.repeat!, within: e.target.value } })}
                  >
                    {WITHIN.map((w) => (
                      <option key={w}>{w}</option>
                    ))}
                  </Select>
                </>
              ) : null}
            </div>
          </Section>

          <Section title="Unless">
            <Toggle
              checked={spec.safetyGuard}
              onChange={(v) => set({ safetyGuard: v })}
              label="Not while a safety fault is open (safety.*)"
            />
          </Section>

          <Section title="Then" hint="in order">
            <div className="space-y-2">
              {spec.actions.map((a, i) => (
                <ActionEditor
                  key={i}
                  action={a}
                  plans={planList.map((p) => p.id)}
                  params={(id) => planById.get(id)?.params ?? {}}
                  onChange={(next) => setAction(i, next)}
                  onPlan={(id) =>
                    setAction(i, {
                      type: "recover",
                      plan: id,
                      args: defaultArgs(planById.get(id)?.params ?? {}, t),
                    })
                  }
                  onRemove={() => removeAction(i)}
                />
              ))}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={() => addAction({ type: "recover", plan: "", args: {} })}>
                <Play size={12} /> Run a plan
              </Button>
              <Button
                size="sm"
                onClick={() =>
                  addAction({ type: "notify", channel: "operators", message: "{{ event.message }}" })
                }
              >
                <Bell size={12} /> Notify
              </Button>
              <Button size="sm" onClick={() => addAction({ type: "incident", op: "escalate" })}>
                <Siren size={12} /> Incident
              </Button>
              <Button size="sm" onClick={() => addAction({ type: "ask_agent", focus: "" })}>
                <Sparkles size={12} /> Ask the agent
              </Button>
            </div>
          </Section>

          <Section title="How often">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
              <Toggle
                checked={spec.perComponent}
                onChange={(v) => set({ perComponent: v })}
                label="Per component"
              />
              <label className="flex items-center gap-2">
                <span className="text-muted-fg">Cooldown</span>
                <Select
                  aria-label="Cooldown"
                  value={spec.cooldown}
                  onChange={(e) => set({ cooldown: e.target.value })}
                >
                  {COOLDOWNS.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </Select>
              </label>
              <span className="flex items-center gap-2">
                <Toggle
                  checked={!!spec.maxFires}
                  onChange={(on) => set({ maxFires: on ? { count: 3, per: "1h" } : null })}
                  label="At most"
                />
                {spec.maxFires ? (
                  <>
                    <Input
                      type="number"
                      min={1}
                      className="w-16"
                      aria-label="Max fires"
                      value={spec.maxFires.count}
                      onChange={(e) =>
                        set({ maxFires: { ...spec.maxFires!, count: Number(e.target.value) } })
                      }
                    />
                    <span className="text-muted-fg">times per</span>
                    <Select
                      aria-label="Per"
                      value={spec.maxFires.per}
                      onChange={(e) => set({ maxFires: { ...spec.maxFires!, per: e.target.value } })}
                    >
                      {PER.map((p) => (
                        <option key={p}>{p}</option>
                      ))}
                    </Select>
                  </>
                ) : null}
              </span>
            </div>
            <Toggle
              checked={spec.dryRun}
              onChange={(v) => set({ dryRun: v })}
              label="Start in shadow mode: it decides, nothing moves"
            />
          </Section>

          <Section title="Name">
            <div className="flex flex-wrap gap-2">
              <label className="min-w-64 flex-[2]">
                <Label>Description</Label>
                <Input
                  className="w-full"
                  aria-label="Description"
                  placeholder="The lidar went quiet: restart its relay."
                  value={spec.description}
                  onChange={(e) =>
                    set({
                      description: e.target.value,
                      ...(idTouched ? {} : { id: slug(e.target.value) }),
                    })
                  }
                />
              </label>
              <label className="min-w-48 flex-1">
                <Label>Id</Label>
                <Input
                  className="w-full font-mono"
                  aria-label="Id"
                  value={spec.id}
                  onChange={(e) => {
                    setIdTouched(true);
                    set({ id: e.target.value });
                  }}
                />
              </label>
            </div>
          </Section>
        </CardBody>
      </Card>

      <Card className="self-start xl:sticky xl:top-0">
        <CardHeader title="The rule" />
        <CardBody className="space-y-3">
          <pre
            className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-bg p-3 font-mono text-xs leading-relaxed ring-1 ring-border"
            data-testid="rule-builder-yaml"
          >
            {yaml}
          </pre>
          {missing.length ? (
            <p className="text-xs text-amber-500" data-testid="rule-builder-missing">
              To try it, it needs: {missing.join(", ")}.
            </p>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={() => onEdit(yaml)}>
              <Pencil size={14} /> Edit as YAML
            </Button>
            <Button
              variant="default"
              disabled={busy || !canRun || missing.length > 0}
              onClick={() => onBacktest(ruleYaml({ ...spec, dryRun: false }), yaml)}
            >
              <FlaskConical size={14} /> {busy ? "Replaying…" : "Backtest"}
            </Button>
          </div>
          {spec.dryRun ? (
            <p className="text-xs text-muted-fg">
              The backtest replays it as if live, to show what it would ask for. On the robot it starts in
              shadow mode.
            </p>
          ) : null}
        </CardBody>
      </Card>
    </div>
  );
}

function ActionEditor({
  action,
  plans,
  params,
  onChange,
  onPlan,
  onRemove,
}: {
  action: Action;
  plans: string[];
  params: (plan: string) => Record<string, { required?: boolean; description?: string; type?: string }>;
  onChange: (a: Action) => void;
  onPlan: (plan: string) => void;
  onRemove: () => void;
}) {
  const remove = (
    <button
      type="button"
      onClick={onRemove}
      className="rounded p-1 text-muted-fg hover:bg-muted hover:text-fg cursor-pointer"
      aria-label="Remove this action"
    >
      <X size={14} />
    </button>
  );
  const box = (title: string, body: ReactNode) => (
    <div className="space-y-2 rounded-md p-3 ring-1 ring-border" data-testid="rule-builder-action">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{title}</span>
        {remove}
      </div>
      {body}
    </div>
  );

  switch (action.type) {
    case "recover": {
      const spec = params(action.plan);
      return box(
        "Run a plan",
        <div className="space-y-2">
          <Select
            aria-label="Plan"
            className="w-full"
            value={action.plan}
            onChange={(e) => onPlan(e.target.value)}
          >
            <option value="">Choose a plan…</option>
            {plans.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </Select>
          <datalist id="rule-builder-templates">
            {TEMPLATES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </datalist>
          {Object.entries(spec).map(([name, p]) => (
            <label key={name} className="flex items-center gap-2">
              <span className="w-28 shrink-0 font-mono text-xs">
                {name}
                {p.required ? <span className="text-red-400">*</span> : null}
              </span>
              <Input
                className="w-full font-mono text-xs"
                aria-label={`Argument ${name}`}
                list="rule-builder-templates"
                placeholder={p.description ?? p.type ?? ""}
                value={action.args[name] ?? ""}
                onChange={(e) => onChange({ ...action, args: { ...action.args, [name]: e.target.value } })}
              />
            </label>
          ))}
        </div>,
      );
    }
    case "notify":
      return box(
        "Notify",
        <div className="flex flex-wrap gap-2">
          <Input
            className="w-36"
            aria-label="Channel"
            value={action.channel}
            onChange={(e) => onChange({ ...action, channel: e.target.value })}
          />
          <Input
            className="min-w-64 flex-1"
            aria-label="Message"
            list="rule-builder-templates"
            value={action.message}
            onChange={(e) => onChange({ ...action, message: e.target.value })}
          />
        </div>,
      );
    case "incident":
      return box(
        "Incident",
        <Select
          aria-label="Incident"
          value={action.op}
          onChange={(e) => onChange({ ...action, op: e.target.value as "open" | "escalate" })}
        >
          <option value="escalate">Escalate it: a human is needed</option>
          <option value="open">Open one, for people to track</option>
        </Select>,
      );
    case "ask_agent":
      return box(
        "Ask the agent",
        <Input
          className="w-full"
          aria-label="Question"
          placeholder="Why does {{ event.source.component }} keep failing?"
          value={action.focus}
          onChange={(e) => onChange({ ...action, focus: e.target.value })}
        />,
      );
  }
}
