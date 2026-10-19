import { Link, useSearch } from "@tanstack/react-router";
import { FlaskConical, WandSparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useBacktestRules, useRule, useRuleSuggestions } from "@/api/queries";
import type { Backtest, BacktestCause, BacktestMatch, BacktestRecovery, Suggestion } from "@/api/types";
import { Chart, chartColors, type ChartOption } from "@/components/Chart";
import {
  Empty,
  ErrorNote,
  Mono,
  PageHeader,
  SeverityBadge,
  StateBadge,
  Table,
  Td,
  Th,
} from "@/components/domain";
import { RuleBuilder } from "@/components/RuleBuilder";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Select, Textarea } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCan } from "@/lib/auth";
import { ago, clock, dateTime, duration, toMs } from "@/lib/format";
import { useTheme } from "@/lib/theme";

export interface RuleLabSearch {
  rule?: string;
  /** Build a rule from a form, or write YAML (the default). */
  mode?: "build" | "yaml";
}

const WINDOWS = [
  { hours: 1, label: "last hour" },
  { hours: 6, label: "last 6 hours" },
  { hours: 24, label: "last 24 hours" },
  { hours: 72, label: "last 3 days" },
  { hours: 168, label: "last 7 days" },
] as const;

const DRAFT_KEY = "jarvis.rule-lab.draft";

export const STARTER = `schema: jarvis.rules/v1
rules:
  - id: stale-twice
    description: The same topic went stale twice within ten minutes.
    partition_by: [attrs.topic]
    when:
      count:
        event: { kind: FAULT_STATE, code: ros.topic.stale, attrs.state: ACTIVE }
        threshold: 2
        within: 10m
    then:
      - notify: { channel: operators, message: "{{ event.attrs.topic }} went stale twice in 10 min" }
`;

/** One rule, as GET /rules/{id} serves its definition, as a rules document to edit. */
export function asDocument(rule: string): string {
  const lines = rule.trimEnd().split("\n");
  const body = lines.map((l, i) => (i === 0 ? `  - ${l}` : l ? `    ${l}` : ""));
  return ["schema: jarvis.rules/v1", "rules:", ...body].join("\n") + "\n";
}

/** The shortest window that replays everything since `iso` (the oldest run of a suggestion). */
export function windowFor(iso: string, now = Date.now()): number {
  const age = (now - (toMs(iso) ?? now)) / 3_600_000;
  return (WINDOWS.find((w) => w.hours >= age + 0.05) ?? WINDOWS[WINDOWS.length - 1]!).hours;
}

function readDraft(): string | null {
  try {
    return globalThis.localStorage?.getItem(DRAFT_KEY) ?? null;
  } catch {
    return null;
  }
}

function saveDraft(text: string) {
  try {
    globalThis.localStorage?.setItem(DRAFT_KEY, text);
  } catch {
    // a private window: the draft just isn't remembered
  }
}

function escape(s: string) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!);
}

/** When each of the draft's rules would have fired (dots) and asked for a recovery (diamonds, just
 * below: they happen at the same instant). One lane per rule. */
export function timelineOption(b: Backtest, dark: boolean): ChartOption {
  const c = chartColors(dark);
  const lanes = (b.rules ?? []).map((r) => r.rule);
  const lane = (rule: string) => Math.max(0, lanes.indexOf(rule));
  const RECOVERY = 0.32;
  const matchTip = (m: BacktestMatch) =>
    `${clock(m.at)} · <b>${escape(m.rule)}</b><br/>` +
    (m.cause ? `${escape(m.cause.code)} on ${escape(m.cause.component || "?")}` : "on another rule's event");
  const recoveryTip = (r: BacktestRecovery) =>
    `${clock(r.at)} · <b>${escape(r.plan)}</b> (${escape(r.state)})` +
    (r.reason ? `<br/>${escape(r.reason)}` : "");
  return {
    grid: { left: 150, right: 24, top: 28, bottom: 48 },
    legend: { top: 0, textStyle: { color: c.text } },
    tooltip: { trigger: "item", formatter: (p: { data?: { tip: string } }) => p.data?.tip ?? "" },
    xAxis: {
      type: "time",
      min: toMs(b.from) ?? undefined,
      max: toMs(b.to) ?? undefined,
      axisLabel: { formatter: (v: number) => clock(new Date(v).toISOString(), false), color: c.text },
      axisLine: { lineStyle: { color: c.line } },
      splitLine: { show: true, lineStyle: { color: c.line, type: "dashed" } },
    },
    yAxis: {
      type: "value",
      min: -0.5,
      max: Math.max(lanes.length, 1) - 0.5 + RECOVERY,
      interval: 1,
      inverse: true,
      axisLabel: {
        formatter: (v: number) => lanes[v] ?? "",
        width: 136,
        overflow: "truncate",
        color: c.fg,
        fontSize: 11,
      },
      axisLine: { show: true, lineStyle: { color: c.line } },
      axisTick: { show: false },
      splitLine: { show: false },
    },
    dataZoom: [
      { type: "inside", xAxisIndex: 0, filterMode: "weakFilter" },
      {
        type: "slider",
        xAxisIndex: 0,
        height: 16,
        bottom: 10,
        filterMode: "weakFilter",
        borderColor: c.line,
      },
    ],
    series: [
      {
        name: "would fire",
        type: "scatter",
        symbolSize: 10,
        itemStyle: { color: "#38bdf8" },
        data: (b.matches ?? []).map((m) => ({ value: [toMs(m.at), lane(m.rule)], tip: matchTip(m) })),
      },
      {
        name: "recovery asked",
        type: "scatter",
        symbol: "diamond",
        symbolSize: 13,
        itemStyle: { color: "#a78bfa" },
        data: (b.recoveries ?? []).map((r) => ({
          value: [toMs(r.at), lane(r.rule) + RECOVERY],
          tip: recoveryTip(r),
        })),
      },
    ],
  };
}

function EventLink({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <Link to="/events" search={{ event: id }} className="hover:underline">
      {children}
    </Link>
  );
}

/** The event a rule fired on, or that it was another rule's (emitted during the replay). */
function Cause({ cause }: { cause?: BacktestCause | null }) {
  if (!cause) return <span className="text-xs text-muted-fg">another rule's event</span>;
  return (
    <EventLink id={cause.id}>
      <span className="inline-flex items-center gap-1.5">
        <SeverityBadge severity={cause.severity} short />
        <Mono>{cause.code}</Mono>
      </span>
    </EventLink>
  );
}

function Stats({ b }: { b: Backtest }) {
  const replaces = new Set(b.replaces ?? []);
  return (
    <Table>
      <thead>
        <tr>
          <Th>rule</Th>
          <Th className="text-right">would fire</Th>
          <Th className="text-right">dry run</Th>
          <Th className="text-right">suppressed</Th>
          <Th className="text-right">cooled down</Th>
          <Th className="text-right">guard blocked</Th>
          <Th>on the robot</Th>
        </tr>
      </thead>
      <tbody>
        {(b.rules ?? []).map((r) => (
          <tr key={r.rule} data-testid="lab-rule">
            <Td>
              <Mono>{r.rule}</Mono>
            </Td>
            <Td className="text-right text-base font-semibold tabular">{r.fires}</Td>
            <Td className="text-right tabular">{r.dry_fires}</Td>
            <Td className="text-right tabular">{r.suppressed}</Td>
            <Td className="text-right tabular">{r.cooled_down}</Td>
            <Td className="text-right tabular">{r.guard_blocked}</Td>
            <Td className="text-xs">
              {replaces.has(r.rule) ? (
                <Link to="/rules/$ruleId" params={{ ruleId: r.rule }} className="hover:underline">
                  replaces the live rule
                </Link>
              ) : (
                <span className="text-muted-fg">new rule</span>
              )}
            </Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function Comparison({ b }: { b: Backtest }) {
  const missing = b.missing ?? [];
  const extra = b.extra ?? [];
  if (!missing.length && !extra.length) {
    return (
      <CardBody>
        <p className="text-sm text-emerald-400" data-testid="lab-same">
          Same decisions as the live {b.replaces!.length === 1 ? "rule" : "rules"}, event for event.
        </p>
      </CardBody>
    );
  }
  const rows = [
    ...missing.map((d) => ({ ...d, side: "live only" })),
    ...extra.map((d) => ({ ...d, side: "draft only" })),
  ].sort((x, y) => (toMs(x.cause?.at) ?? 0) - (toMs(y.cause?.at) ?? 0));
  return (
    <Table>
      <thead>
        <tr>
          <Th>time</Th>
          <Th>rule</Th>
          <Th>decided</Th>
          <Th>on</Th>
          <Th>component</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((d, i) => (
          <tr key={i} data-testid="lab-diff">
            <Td className="font-mono text-xs tabular">{d.cause ? clock(d.cause.at) : "—"}</Td>
            <Td>
              <Mono>{d.rule}</Mono>
            </Td>
            <Td>
              <span
                className={
                  d.side === "live only"
                    ? "text-xs font-medium text-amber-400"
                    : "text-xs font-medium text-sky-400"
                }
              >
                {d.side === "live only" ? "live, not by the draft" : "by the draft, not live"}
              </span>
            </Td>
            <Td>
              <Cause cause={d.cause} />
            </Td>
            <Td className="text-xs">{d.cause?.component || "—"}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function Results({ b }: { b: Backtest }) {
  const { dark } = useTheme();
  const option = useMemo(() => timelineOption(b, dark), [b, dark]);
  const matches = b.matches ?? [];
  const recoveries = b.recoveries ?? [];
  return (
    <div className="space-y-4" data-testid="lab-results">
      <p className="text-sm text-muted-fg" data-testid="lab-summary">
        Replayed <span className="font-semibold text-fg tabular">{b.events.toLocaleString()}</span> events,{" "}
        {b.from ? `${dateTime(b.from)} → ${dateTime(b.to)}` : "none in this window"}.{" "}
        <span className="font-semibold text-fg tabular">{b.match_count}</span>{" "}
        {b.match_count === 1 ? "fire" : "fires"}
        {recoveries.length ? `, ${recoveries.length} recoveries asked for (dry run)` : ""}.
        {b.truncated ? " Only the first 200,000 events: choose a shorter window for all of them." : ""}
      </p>
      <Card>
        <CardHeader title="Per rule" />
        <Stats b={b} />
      </Card>
      {b.replaces?.length ? (
        <Card>
          <CardHeader title="Against what the robot decided live" />
          <Comparison b={b} />
        </Card>
      ) : null}
      <Card>
        <CardHeader title="When it would have fired" />
        <CardBody>
          {matches.length || recoveries.length ? (
            <Chart
              option={option}
              height={Math.max(150, (b.rules ?? []).length * 44 + 90)}
              label="Backtest timeline"
            />
          ) : (
            <Empty className="py-6">It would not have fired in this window.</Empty>
          )}
        </CardBody>
      </Card>
      {matches.length ? (
        <Card>
          <CardHeader
            title={`Fires (${b.match_count})`}
            actions={
              b.match_count > matches.length ? (
                <span className="text-xs text-muted-fg">the first {matches.length}</span>
              ) : null
            }
          />
          <Table>
            <thead>
              <tr>
                <Th>time</Th>
                <Th>rule</Th>
                <Th>on</Th>
                <Th>component</Th>
                <Th>message</Th>
              </tr>
            </thead>
            <tbody>
              {matches.map((m) => (
                <tr key={m.match_id} data-testid="lab-match">
                  <Td className="font-mono text-xs tabular">{clock(m.at)}</Td>
                  <Td>
                    <Mono>{m.rule}</Mono>
                  </Td>
                  <Td>
                    <Cause cause={m.cause} />
                  </Td>
                  <Td className="text-xs">{m.cause?.component || "—"}</Td>
                  <Td className="max-w-md truncate text-xs text-muted-fg">{m.cause?.message || ""}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      ) : null}
      {recoveries.length ? (
        <Card>
          <CardHeader title="Recoveries it would have asked for (plans in dry run)" />
          <Table>
            <thead>
              <tr>
                <Th>time</Th>
                <Th>rule</Th>
                <Th>plan</Th>
                <Th>outcome</Th>
                <Th>why</Th>
              </tr>
            </thead>
            <tbody>
              {recoveries.map((r, i) => (
                <tr key={i} data-testid="lab-recovery">
                  <Td className="font-mono text-xs tabular">{clock(r.at)}</Td>
                  <Td>
                    <Mono>{r.rule}</Mono>
                  </Td>
                  <Td>
                    <Mono>{r.plan}</Mono>
                    {r.args && Object.keys(r.args).length ? (
                      <div className="text-xs text-muted-fg">
                        {Object.entries(r.args)
                          .map(([k, v]) => `${k}=${v}`)
                          .join(" ")}
                      </div>
                    ) : null}
                  </Td>
                  <Td>{r.state ? <StateBadge state={r.state} /> : "—"}</Td>
                  <Td className="text-xs text-muted-fg">{r.reason || ""}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      ) : null}
    </div>
  );
}

/** What operators keep doing by hand, as rules to draft (GET /rules:suggest, the last 7 days). */
function Suggestions({ onDraft }: { onDraft: (s: Suggestion) => void }) {
  const suggestions = useRuleSuggestions();
  const list = suggestions.data?.suggestions ?? [];
  if (!list.length) return null;
  return (
    <Card data-testid="lab-suggestions">
      <CardHeader
        title="What your operators keep doing by hand"
        actions={<span className="text-xs text-muted-fg">last 7 days</span>}
      />
      <ul className="divide-y divide-border">
        {list.map((s) => (
          <li
            key={s.id}
            className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
            data-testid="lab-suggestion"
          >
            <div className="min-w-0 space-y-0.5">
              <div className="text-sm">
                <Mono className="text-fg">{s.plan}</Mono> <span className="text-muted-fg">after</span>{" "}
                <Mono>{s.code}</Mono>
              </div>
              <div className="text-xs text-muted-fg">
                Run by hand {s.runs} times by {s.operators.join(", ") || "operators"}
                {s.components.length ? ` on ${s.components.join(", ")}` : ""} · {s.succeeded} succeeded ·{" "}
                {duration(s.median_after_s)} after the fault (median) · last {ago(s.last)}
              </div>
            </div>
            <Button onClick={() => onDraft(s)}>
              <WandSparkles size={14} /> Draft the rule
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function RuleLab() {
  const search = useSearch({ from: "/rules/lab" });
  const from = useRule(search.rule ?? "");
  const backtest = useBacktestRules();
  const can = useCan();
  const [text, setText] = useState(() => (search.rule ? "" : (readDraft() ?? STARTER)));
  const [hours, setHours] = useState(24);
  const [loaded, setLoaded] = useState<string | undefined>();
  const [mode, setMode] = useState<"build" | "yaml">(search.mode ?? "yaml");

  // Opened from a rule ("Try changes"): its definition, once it is here.
  useEffect(() => {
    if (search.rule && from.data?.yaml && loaded !== search.rule) {
      setText(asDocument(from.data.yaml));
      setLoaded(search.rule);
    }
  }, [search.rule, from.data, loaded]);

  const run = () => {
    saveDraft(text);
    backtest.mutate({ yaml: text, hours });
  };
  // The builder's rule: tried as it is, or edited further by hand.
  const tryBuilt = (replay: string, keep: string) => {
    setText(keep);
    saveDraft(keep);
    backtest.mutate({ yaml: replay, hours });
  };
  const editBuilt = (yaml: string) => {
    setText(yaml);
    setMode("yaml");
  };
  // A suggestion: its draft, backtested over the window its runs are in.
  const draft = (s: Suggestion) => {
    const h = windowFor(s.first);
    setMode("yaml");
    setText(s.yaml);
    setHours(h);
    saveDraft(s.yaml);
    if (can("admin")) backtest.mutate({ yaml: s.yaml, hours: h });
  };
  const b = backtest.data;
  const findings = b?.findings ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
            <FlaskConical size={18} aria-hidden /> Rule lab
          </span>
        }
      />
      <p className="max-w-3xl text-sm text-muted-fg">
        Write a rule, or change one, and see what it would have done on this robot: its recorded events
        replayed through the same engine, with the robot's other rules and its plans in dry run. Nothing
        changes on the robot; to deploy, put the rule in the robot's rules directory and reload.
      </p>
      <Suggestions onDraft={draft} />
      <Tabs value={mode} onValueChange={(v) => setMode(v as "build" | "yaml")}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <TabsList>
            <TabsTrigger value="build">Build a rule</TabsTrigger>
            <TabsTrigger value="yaml">YAML</TabsTrigger>
          </TabsList>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted-fg">Replay the</span>
            <Select aria-label="Window" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              {WINDOWS.map((w) => (
                <option key={w.hours} value={w.hours}>
                  {w.label}
                </option>
              ))}
            </Select>
            {!can("admin") ? (
              <span className="text-xs text-muted-fg">Backtests need the admin role.</span>
            ) : null}
          </label>
        </div>
        <TabsContent value="build" className="mt-3">
          <RuleBuilder
            onBacktest={tryBuilt}
            onEdit={editBuilt}
            busy={backtest.isPending}
            canRun={can("admin")}
          />
        </TabsContent>
        <TabsContent value="yaml" className="mt-3">
          <Card>
            <CardBody className="space-y-3">
              <Textarea
                className="h-80 w-full"
                aria-label="Draft rules YAML"
                spellCheck={false}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) run();
                }}
              />
              <div className="flex justify-end">
                <Button
                  variant="default"
                  onClick={run}
                  disabled={backtest.isPending || !can("admin") || !text.trim()}
                  title="Ctrl+Enter"
                >
                  <FlaskConical size={14} /> {backtest.isPending ? "Replaying…" : "Backtest"}
                </Button>
              </div>
            </CardBody>
          </Card>
        </TabsContent>
      </Tabs>
      <ErrorNote error={backtest.error ?? (search.rule ? from.error : null)} />
      {b && !b.success ? (
        <div className="text-sm text-red-400" data-testid="lab-invalid">
          {b.message || "The draft has errors."}
        </div>
      ) : null}
      {findings.length ? (
        <ul className="space-y-1 text-sm" data-testid="lab-findings">
          {findings.map((f, i) => (
            <li key={i} className={f.level === "error" ? "text-red-400" : "text-amber-400"}>
              {f.level}: <Mono>{f.message}</Mono>
            </li>
          ))}
        </ul>
      ) : null}
      {b?.success ? <Results b={b} /> : null}
    </div>
  );
}
