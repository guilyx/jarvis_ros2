import { Link } from "@tanstack/react-router";
import { Clock, Sparkles } from "lucide-react";
import { useMemo } from "react";
import { useIncidents, useInsights, useStats } from "@/api/queries";
import { SEVERITIES, insightConfidence, insightOf, type RatePoint, type Severity } from "@/api/types";
import { Chart, chartColors, type ChartOption } from "@/components/Chart";
import { DecideButtons } from "@/components/DecideButtons";
import { Empty, ErrorNote, SeverityBadge, StateBadge } from "@/components/domain";
import { FaultActions } from "@/components/FaultActions";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useLive } from "@/live/context";
import { useCan } from "@/lib/auth";
import { ago, duration, percent, toMs } from "@/lib/format";
import { SEVERITY_STYLE } from "@/lib/severity";
import { useTheme } from "@/lib/theme";

function Kpi({
  label,
  value,
  sub,
  testId,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  testId?: string;
}) {
  return (
    <Card className="px-4 py-3" data-testid={testId}>
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-fg">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular">{value}</div>
      {sub ? <div className="mt-0.5 text-xs text-muted-fg">{sub}</div> : null}
    </Card>
  );
}

export function rateOption(points: RatePoint[], dark: boolean): ChartOption {
  const buckets = [...new Set(points.map((p) => p.bucket))].sort();
  const c = chartColors(dark);
  return {
    grid: { left: 4, right: 4, top: 8, bottom: 4, containLabel: false },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: buckets, show: false },
    yAxis: { type: "value", show: false, splitLine: { lineStyle: { color: c.line } } },
    series: SEVERITIES.filter((s) => s !== "DEBUG").map((sev) => ({
      name: sev,
      type: "bar",
      stack: "events",
      barCategoryGap: "20%",
      itemStyle: { color: SEVERITY_STYLE[sev].chart },
      data: buckets.map((b) => points.find((p) => p.bucket === b && p.severity === sev)?.events ?? 0),
    })),
  };
}

function ActiveFaults() {
  const faults = useLive((s) => s.faults);
  const counts = useMemo(() => {
    const out: Partial<Record<Severity, number>> = {};
    for (const f of faults ?? []) if (!f.muted_by) out[f.severity] = (out[f.severity] ?? 0) + 1;
    return out;
  }, [faults]);
  const shown = (["CRITICAL", "ERROR", "WARN", "INFO"] as const).filter((s) => counts[s]);
  return (
    <Kpi
      label="Active faults"
      testId="kpi-active-faults"
      value={faults === null ? "…" : (faults ?? []).filter((f) => !f.muted_by).length}
      sub={
        shown.length ? (
          <span className="flex gap-2">
            {shown.map((s) => (
              <span key={s} className={SEVERITY_STYLE[s].text}>
                {counts[s]} {SEVERITY_STYLE[s].short}
              </span>
            ))}
          </span>
        ) : (
          "all clear"
        )
      }
    />
  );
}

function NeedsAttention() {
  const faults = useLive((s) => s.faults);
  const executions = useLive((s) => s.executions);
  const escalated = useIncidents("ESCALATED");
  const can = useCan();
  const pending = (executions?.executions ?? []).filter((x) => x.state === "PENDING_APPROVAL");
  const urgent = (faults ?? []).filter(
    (f) => !f.muted_by && (f.state === "LATCHED" || f.severity === "CRITICAL"),
  );
  const items = pending.length + urgent.length + (escalated.data?.incidents.length ?? 0);
  return (
    <Card>
      <CardHeader title="Needs attention" />
      <CardBody className="space-y-2">
        {!items ? <Empty className="py-6">Nothing waits for a human.</Empty> : null}
        {pending.map((x) => (
          <div
            key={x.execution_id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-amber-500/5 px-3 py-2 ring-1 ring-amber-500/20"
          >
            <div className="min-w-0 text-sm">
              <Clock size={13} className="mr-1.5 inline text-amber-400" />
              Approval:{" "}
              <Link
                to="/recovery/$executionId"
                params={{ executionId: x.execution_id }}
                className="font-medium hover:underline"
              >
                {x.plan_id}
              </Link>{" "}
              <span className="text-muted-fg">
                by {x.requester.type.toLowerCase()} {x.requester.id}
                {x.confidence ? ` · conf ${x.confidence.toFixed(2)}` : ""}
                {x.expires ? ` · expires ${ago(x.expires)}` : ""}
              </span>
            </div>
            {can("operator") ? <DecideButtons x={x} description={x.rationale || undefined} /> : null}
          </div>
        ))}
        {(escalated.data?.incidents ?? []).map((i) => (
          <div
            key={i.incident_id}
            className="flex items-center justify-between gap-2 rounded-md bg-red-500/5 px-3 py-2 ring-1 ring-red-500/20"
          >
            <div className="min-w-0 truncate text-sm">
              <StateBadge state="ESCALATED" className="mr-2" />
              {i.title} <span className="text-muted-fg">· {ago(i.escalated_at ?? i.opened_at)}</span>
            </div>
            <Link to="/incidents/$incidentId" params={{ incidentId: i.incident_id }}>
              <Button size="sm">Open</Button>
            </Link>
          </div>
        ))}
        {urgent.map((f) => (
          <div
            key={f.fault_id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted/40 px-3 py-2 ring-1 ring-border"
            data-testid="attention-fault"
          >
            <div className="flex min-w-0 items-center gap-2 text-sm">
              <SeverityBadge severity={f.severity} short />
              <StateBadge state={f.state} />
              <Link
                to="/faults/$faultId"
                params={{ faultId: f.fault_id }}
                className="truncate font-mono text-xs hover:underline"
              >
                {f.code}
              </Link>
              <span className="text-xs text-muted-fg">{f.source.component}</span>
              {f.acknowledged ? <span className="text-xs text-muted-fg">ack {f.acknowledged_by}</span> : null}
            </div>
            <FaultActions fault={f} />
          </div>
        ))}
      </CardBody>
    </Card>
  );
}

function LatestInsights() {
  const insights = useInsights(3);
  return (
    <Card>
      <CardHeader
        title="Latest insights"
        actions={
          <Link to="/agent" className="text-xs text-sky-400 hover:underline">
            All
          </Link>
        }
      />
      <CardBody className="space-y-3">
        {insights.data?.insights.length ? (
          insights.data.insights.map((e) => {
            const insight = insightOf(e);
            if (!insight) return null;
            const evidence = insight.hypotheses.reduce((n, h) => n + h.evidence.length, 0);
            return (
              <Link key={e.id} to="/agent" className="block text-sm hover:underline">
                <Sparkles size={13} className="mr-1.5 inline text-violet-400" />
                {insight.title}
                <div className="text-xs text-muted-fg">
                  conf {insightConfidence(insight).toFixed(2)} · {evidence} evidence · {ago(e.stamp)}
                </div>
              </Link>
            );
          })
        ) : (
          <Empty className="py-6">No insights yet: the agent writes one when something goes wrong.</Empty>
        )}
      </CardBody>
    </Card>
  );
}

export function Overview() {
  const stats = useStats(24);
  const { dark } = useTheme();
  const k = stats.data?.kpis;
  const rate = useMemo(() => rateOption(stats.data?.event_rate ?? [], dark), [stats.data, dark]);
  const lastRate = useMemo(() => {
    const points = stats.data?.event_rate ?? [];
    if (!points.length) return null;
    const last = points.reduce((a, p) => ((toMs(p.bucket) ?? 0) > (toMs(a.bucket) ?? 0) ? p : a));
    return points.filter((p) => p.bucket === last.bucket).reduce((n, p) => n + p.events, 0);
  }, [stats.data]);
  const maxEpisodes = Math.max(1, ...(stats.data?.top_codes ?? []).map((t) => t.episodes));

  return (
    <div className="space-y-4">
      <ErrorNote error={stats.error} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <ActiveFaults />
        <Kpi
          label="Open incidents"
          testId="kpi-open-incidents"
          value={k ? k.incidents_open : "…"}
          sub={k ? `${k.incidents_escalated} escalated · ${k.incidents_opened} in 24 h` : undefined}
        />
        <Kpi
          label="Auto-recovery (24 h)"
          value={percent(k?.auto_recovery_rate)}
          sub="incidents resolved without a human"
        />
        <Kpi
          label="MTTR (24 h)"
          value={duration(k?.mttr_s)}
          sub={k ? `${k.faults_confirmed} faults confirmed` : undefined}
        />
        <Card className="px-4 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-fg">
            Events (24 h)
          </div>
          <div className="mt-1 text-2xl font-semibold tabular">{k ? k.events.toLocaleString() : "…"}</div>
          <Chart option={rate} height={34} label="Event rate by severity" />
          {lastRate !== null ? (
            <div className="text-xs text-muted-fg">{lastRate} in the last bucket</div>
          ) : null}
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <NeedsAttention />
        </div>
        <LatestInsights />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Top fault codes (24 h)"
            actions={
              <Link to="/analytics" className="text-xs text-sky-400 hover:underline">
                Analytics
              </Link>
            }
          />
          <CardBody className="space-y-1.5">
            {stats.data?.top_codes.length ? (
              stats.data.top_codes.map((t) => (
                <div
                  key={t.code}
                  className="grid grid-cols-[minmax(0,14rem)_1fr_3rem] items-center gap-3 text-sm"
                >
                  <span className="truncate font-mono text-xs">{t.code}</span>
                  <div className="h-2 rounded bg-muted">
                    <div
                      className="h-2 rounded bg-orange-500/70"
                      style={{ width: `${(t.episodes / maxEpisodes) * 100}%` }}
                    />
                  </div>
                  <span className="text-right tabular">{t.episodes}</span>
                </div>
              ))
            ) : (
              <Empty className="py-6">No faults in the last 24 h.</Empty>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader
            title="Recovery outcomes (24 h)"
            actions={
              <Link to="/recovery" className="text-xs text-sky-400 hover:underline">
                Recovery
              </Link>
            }
          />
          <CardBody className="space-y-1.5">
            {stats.data?.plans.length ? (
              stats.data.plans.map((p) => (
                <div
                  key={p.plan}
                  className="grid grid-cols-[minmax(0,14rem)_1fr_4rem] items-center gap-3 text-sm"
                >
                  <span className="truncate font-mono text-xs">{p.plan}</span>
                  <span className="text-xs">
                    <span className="text-emerald-400">✔ {p.succeeded}</span>{" "}
                    <span className="text-red-400">✖ {p.failed}</span>
                    {p.not_run ? <span className="text-muted-fg"> · {p.not_run} not run</span> : null}
                  </span>
                  <span className="text-right tabular">{percent(p.success_rate)}</span>
                </div>
              ))
            ) : (
              <Empty className="py-6">No recoveries in the last 24 h.</Empty>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
