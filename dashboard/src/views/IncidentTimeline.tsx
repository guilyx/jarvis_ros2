import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { graphic } from "echarts/core";
import { ListTree, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { useCloseIncident, useIncident } from "@/api/queries";
import type { IncidentDetail, JarvisEvent } from "@/api/types";
import { Chart, chartColors, type ChartEvents, type ChartOption } from "@/components/Chart";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Empty, ErrorNote, Loading, Mono, PageHeader, SeverityBadge, StateBadge } from "@/components/domain";
import { EventSheet } from "@/components/EventSheet";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Tip } from "@/components/ui/tooltip";
import { useCan } from "@/lib/auth";
import { between, clock, dateTime, duration, severityName, toMs } from "@/lib/format";
import { SEVERITY_STYLE } from "@/lib/severity";
import { useTheme } from "@/lib/theme";

const OUTCOME_COLOR: Record<string, string> = {
  SUCCEEDED: "#10b981",
  FAILED: "#ef4444",
  ABORTED: "#94a3b8",
  REJECTED: "#94a3b8",
  EXPIRED: "#94a3b8",
};

type Link_ =
  | { type: "fault"; id: string }
  | { type: "execution"; id: string }
  | { type: "event"; id: string };

interface Item {
  value: number[];
  tip: string;
  link: Link_;
  itemStyle: { color: string; opacity?: number };
  symbol?: string;
}

/** Swimlanes: one row per fault and per execution, then rule matches, the agent, operators, the incident. */
export function buildTimeline(d: IncidentDetail, now = Date.now()) {
  const lanes: string[] = [];
  const bars: Item[] = [];
  const points: Item[] = [];
  const lane = (name: string) => {
    const i = lanes.indexOf(name);
    if (i >= 0) return i;
    lanes.push(name);
    return lanes.length - 1;
  };
  const transitions = d.timeline.filter((e) => e.kind === "FAULT_STATE");
  for (const f of d.faults) {
    const row = lane(`⚠ ${f.code}${f.component ? ` · ${f.component}` : ""}`);
    const start = toMs(f.confirmed_at ?? f.first_seen) ?? now;
    const end = toMs(f.cleared_at) ?? now;
    const sev = severityName(f.severity_level);
    bars.push({
      value: [row, start, end],
      tip: `${f.code}<br/>${f.last_state} · active ${duration((end - start) / 1000)}`,
      link: { type: "fault", id: f.fault_id },
      itemStyle: { color: SEVERITY_STYLE[sev].chart, opacity: f.cleared_at ? 0.55 : 0.9 },
    });
    for (const t of transitions.filter((e) => e.attrs?.fault_id === f.fault_id)) {
      points.push(point(row, t, "rect", `${t.attrs?.state}: ${t.message ?? ""}`));
    }
  }
  for (const x of d.executions) {
    const row = lane(`↻ ${x.plan}${x.dry_run ? " (dry run)" : ""}`);
    const start = toMs(x.started_at ?? x.created_at) ?? now;
    const end = toMs(x.finished_at) ?? now;
    bars.push({
      value: [row, start, Math.max(end, start + 500)],
      tip: `${x.plan} · ${x.outcome ?? x.last_phase}<br/>by ${x.requester} ${x.requester_id} · ${duration((end - start) / 1000)}`,
      link: { type: "execution", id: x.execution_id },
      itemStyle: { color: OUTCOME_COLOR[x.outcome ?? ""] ?? "#38bdf8" },
    });
  }
  const others: [string, (e: JarvisEvent) => boolean, string][] = [
    ["◆ Rules", (e) => e.kind === "RULE_MATCH", "diamond"],
    ["✦ Agent", (e) => e.kind === "INSIGHT", "pin"],
    ["● Operator", (e) => e.kind === "OPERATOR", "circle"],
    ["▲ Incident", (e) => e.kind === "SYSTEM" && e.code.startsWith("jarvis.incident."), "triangle"],
    ["✉ Notify", (e) => e.code === "jarvis.notify", "roundRect"],
  ];
  for (const [name, test, symbol] of others) {
    const events = d.timeline.filter(test);
    if (!events.length) continue;
    const row = lane(name);
    for (const e of events) points.push(point(row, e, symbol, e.message ?? e.code));
  }
  return { lanes, bars, points };
}

function point(row: number, e: JarvisEvent, symbol: string, text: string): Item {
  return {
    value: [toMs(e.stamp) ?? 0, row],
    symbol,
    tip: `${clock(e.stamp)} · ${e.code}<br/>${escape(text)}`,
    link: { type: "event", id: e.id },
    itemStyle: { color: SEVERITY_STYLE[e.severity]?.chart ?? "#38bdf8" },
  };
}

function escape(s: string) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!);
}

function timelineOption(d: IncidentDetail, dark: boolean): { option: ChartOption; height: number } {
  const { lanes, bars, points } = buildTimeline(d);
  const c = chartColors(dark);
  const option: ChartOption = {
    grid: { left: 210, right: 24, top: 12, bottom: 48 },
    tooltip: { trigger: "item", formatter: (p: { data?: Item }) => p.data?.tip ?? "" },
    xAxis: {
      type: "time",
      axisLabel: { formatter: (v: number) => clock(new Date(v).toISOString(), false), color: c.text },
      axisLine: { lineStyle: { color: c.line } },
      splitLine: { show: true, lineStyle: { color: c.line, type: "dashed" } },
    },
    yAxis: {
      type: "category",
      data: lanes,
      inverse: true,
      axisLabel: { width: 196, overflow: "truncate", color: c.fg, fontSize: 11 },
      axisLine: { lineStyle: { color: c.line } },
      axisTick: { show: false },
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
        type: "custom",
        encode: { x: [1, 2], y: 0 },
        renderItem: (
          params: { coordSys: { x: number; y: number; width: number; height: number } },
          api: {
            value: (i: number) => number;
            coord: (v: number[]) => number[];
            size: (v: number[]) => number[];
            style: () => Record<string, unknown>;
          },
        ) => {
          const row = api.value(0);
          const start = api.coord([api.value(1), row]);
          const end = api.coord([api.value(2), row]);
          const height = api.size([0, 1])[1]! * 0.45;
          const rect = graphic.clipRectByRect(
            { x: start[0]!, y: start[1]! - height / 2, width: Math.max(end[0]! - start[0]!, 3), height },
            params.coordSys,
          );
          return rect ? { type: "rect", shape: { ...rect, r: 3 }, style: api.style() } : undefined;
        },
        data: bars,
      },
      { type: "scatter", symbolSize: 11, data: points, z: 3 },
    ],
  };
  return { option, height: Math.max(160, lanes.length * 34 + 70) };
}

function AgentSummary({ insights, onPick }: { insights: JarvisEvent[]; onPick: (id: string) => void }) {
  if (!insights.length) return null;
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <Sparkles size={13} className="text-violet-400" /> Agent summary
          </span>
        }
      />
      <CardBody className="space-y-4">
        {insights.map((e) => {
          const p = (e.payload ?? {}) as {
            title?: string;
            hypotheses?: { cause?: string; confidence?: number; evidence?: string[] }[];
            recommendations?: (string | { action?: string; text?: string })[];
          };
          return (
            <div key={e.id} className="space-y-2 text-sm">
              <button
                type="button"
                onClick={() => onPick(e.id)}
                className="font-medium hover:underline cursor-pointer"
              >
                {p.title ?? e.message}
              </button>
              {p.hypotheses?.length ? (
                <ol className="list-decimal space-y-1 pl-5">
                  {p.hypotheses.map((h, i) => (
                    <li key={i}>
                      {h.cause}{" "}
                      {h.confidence !== undefined ? (
                        <span className="text-muted-fg">({h.confidence.toFixed(2)})</span>
                      ) : null}
                    </li>
                  ))}
                </ol>
              ) : null}
              {p.recommendations?.length ? (
                <ul className="list-disc space-y-1 pl-5 text-muted-fg">
                  {p.recommendations.map((r, i) => (
                    <li key={i}>{typeof r === "string" ? r : (r.text ?? r.action)}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          );
        })}
      </CardBody>
    </Card>
  );
}

export function IncidentTimeline() {
  const { incidentId } = useParams({ from: "/incidents/$incidentId" });
  const incident = useIncident(incidentId);
  const close = useCloseIncident();
  const can = useCan();
  const navigate = useNavigate();
  const { dark } = useTheme();
  const [picked, setPicked] = useState<string | null>(null);
  const chart = useMemo(
    () => (incident.data ? timelineOption(incident.data, dark) : null),
    [incident.data, dark],
  );
  const events = useMemo<ChartEvents>(
    () => ({
      click: (params) => {
        const link = (params as { data?: Item }).data?.link;
        if (!link) return;
        if (link.type === "event") setPicked(link.id);
        else if (link.type === "fault")
          void navigate({ to: "/faults/$faultId", params: { faultId: link.id } });
        else void navigate({ to: "/recovery/$executionId", params: { executionId: link.id } });
      },
    }),
    [navigate],
  );

  if (incident.isLoading) return <Loading />;
  if (incident.error) return <ErrorNote error={incident.error} />;
  const d = incident.data!;
  const insights = d.timeline.filter((e) => e.kind === "INSIGHT");

  return (
    <div className="space-y-4">
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={severityName(d.severity_level)} short />
            <span>{d.title}</span>
            <StateBadge state={d.state} />
          </span>
        }
      >
        <span className="text-sm text-muted-fg">
          opened {dateTime(d.opened_at)} · {duration(between(d.opened_at, d.closed_at ?? d.resolved_at))}
        </span>
        <Link to="/events" search={{ incident: incidentId }}>
          <Button size="sm">
            <ListTree size={13} /> Events
          </Button>
        </Link>
        <Tip content="The agent arrives with milestone 4">
          <span>
            <Button size="sm" disabled>
              <Sparkles size={13} /> Ask agent
            </Button>
          </span>
        </Tip>
        {can("operator") && d.state === "RESOLVED" ? (
          <ConfirmDialog
            trigger={
              <Button size="sm" variant="default">
                Close
              </Button>
            }
            title="Close the incident"
            description="Closing says it was dealt with: what happened and why goes in the note."
            confirm="Close"
            onConfirm={(note) => close.mutateAsync({ id: incidentId, note })}
          />
        ) : null}
      </PageHeader>
      <Card>
        <CardHeader
          title="Timeline"
          actions={<span className="text-xs text-muted-fg">scroll to zoom · click for details</span>}
        />
        <CardBody className="pt-2">
          {chart && (d.faults.length || d.executions.length || d.timeline.length) ? (
            <Chart option={chart.option} height={chart.height} events={events} label="Incident timeline" />
          ) : (
            <Empty>Nothing recorded yet.</Empty>
          )}
        </CardBody>
      </Card>
      <AgentSummary insights={insights} onPick={setPicked} />
      <Card>
        <CardHeader title={`Events (${d.timeline.length})`} />
        <div className="max-h-[28rem] overflow-y-auto">
          {d.timeline.map((e) => (
            <button
              type="button"
              key={e.id}
              onClick={() => setPicked(e.id)}
              className="grid w-full grid-cols-[7rem_5rem_8rem_minmax(10rem,1fr)_2fr] items-center gap-3 border-b border-border/50 px-4 py-1.5 text-left text-sm hover:bg-muted/60 cursor-pointer"
            >
              <Mono className="tabular">{clock(e.stamp)}</Mono>
              <SeverityBadge severity={e.severity} short />
              <span className="truncate text-xs">{e.kind}</span>
              <Mono className="truncate">{e.code}</Mono>
              <span className="truncate">{e.message}</span>
            </button>
          ))}
        </div>
      </Card>
      <EventSheet id={picked} onClose={() => setPicked(null)} onPick={setPicked} />
    </div>
  );
}
