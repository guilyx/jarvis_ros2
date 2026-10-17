import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useMemo } from "react";
import { useAnalytics } from "@/api/queries";
import type { HeatCell, PlanStats, RuleFires, TopCode } from "@/api/types";
import { Chart, chartColors, type ChartOption } from "@/components/Chart";
import { Empty, ErrorNote, Loading, PageHeader, SeverityBadge, Table, Td, Th } from "@/components/domain";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ago, clock, dateTime, duration, percent } from "@/lib/format";
import { useTheme } from "@/lib/theme";

export interface AnalyticsSearch {
  hours?: number;
}

const WINDOWS = [
  { hours: 1, label: "1 h" },
  { hours: 6, label: "6 h" },
  { hours: 24, label: "24 h" },
  { hours: 168, label: "7 d" },
];

function axes(dark: boolean) {
  const c = chartColors(dark);
  return {
    axisLine: { lineStyle: { color: c.line } },
    axisLabel: { color: c.text },
    splitLine: { lineStyle: { color: c.line } },
  };
}

export function paretoOption(rows: TopCode[], dark: boolean): ChartOption {
  const a = axes(dark);
  return {
    grid: { left: 40, right: 44, top: 16, bottom: 72 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      data: rows.map((r) => r.code),
      ...a,
      axisLabel: { ...a.axisLabel, rotate: 30, fontSize: 10 },
    },
    yAxis: [
      { type: "value", name: "episodes", minInterval: 1, ...a },
      {
        type: "value",
        max: 1,
        axisLabel: { ...a.axisLabel, formatter: (v: number) => `${v * 100}%` },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        type: "bar",
        name: "episodes",
        data: rows.map((r) => r.episodes),
        itemStyle: { color: "#f97316", borderRadius: [3, 3, 0, 0] },
      },
      {
        type: "line",
        name: "cumulative",
        yAxisIndex: 1,
        data: rows.map((r) => r.cumulative_share),
        itemStyle: { color: "#38bdf8" },
        smooth: true,
      },
    ],
  };
}

export function heatmapOption(cells: HeatCell[], dark: boolean): ChartOption {
  const a = axes(dark);
  const buckets = [...new Set(cells.map((c) => c.bucket))].sort();
  const components = [...new Set(cells.map((c) => c.component))].sort();
  const max = Math.max(1, ...cells.map((c) => c.faults));
  return {
    grid: { left: 110, right: 16, top: 8, bottom: 56 },
    tooltip: {
      formatter: (p: { value: number[] }) =>
        `${components[p.value[1]!]} · ${dateTime(buckets[p.value[0]!])}<br/>${p.value[2]} fault(s)`,
    },
    xAxis: {
      type: "category",
      data: buckets.map((b) => clock(b, false).slice(0, 5)),
      ...a,
      splitArea: { show: false },
    },
    yAxis: { type: "category", data: components, ...a },
    visualMap: {
      min: 0,
      max,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      itemHeight: 120,
      textStyle: { color: chartColors(dark).text },
      inRange: { color: dark ? ["#13203a", "#f59e0b", "#ef4444"] : ["#fef3c7", "#f59e0b", "#ef4444"] },
    },
    series: [
      {
        type: "heatmap",
        data: cells.map((c) => [buckets.indexOf(c.bucket), components.indexOf(c.component), c.faults]),
        itemStyle: { borderColor: dark ? "#0b1120" : "#ffffff", borderWidth: 1 },
      },
    ],
  };
}

export function plansOption(rows: PlanStats[], dark: boolean): ChartOption {
  const a = axes(dark);
  return {
    grid: { left: 150, right: 24, top: 24, bottom: 24 },
    legend: { top: 0, textStyle: { color: chartColors(dark).text } },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value", minInterval: 1, ...a },
    yAxis: {
      type: "category",
      data: rows.map((r) => r.plan),
      ...a,
      axisLabel: { ...a.axisLabel, width: 140, overflow: "truncate" },
    },
    series: [
      {
        name: "succeeded",
        type: "bar",
        stack: "runs",
        data: rows.map((r) => r.succeeded),
        itemStyle: { color: "#10b981" },
      },
      {
        name: "failed",
        type: "bar",
        stack: "runs",
        data: rows.map((r) => r.failed),
        itemStyle: { color: "#ef4444" },
      },
      {
        name: "not run",
        type: "bar",
        stack: "runs",
        data: rows.map((r) => r.not_run),
        itemStyle: { color: "#64748b" },
      },
    ],
  };
}

export function rulesOption(rows: RuleFires[], dark: boolean): ChartOption {
  const a = axes(dark);
  return {
    grid: { left: 150, right: 24, top: 24, bottom: 24 },
    legend: { top: 0, textStyle: { color: chartColors(dark).text } },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value", ...a, minInterval: 1 },
    yAxis: {
      type: "category",
      data: rows.map((r) => r.rule),
      ...a,
      axisLabel: { ...a.axisLabel, width: 140, overflow: "truncate" },
    },
    series: [
      {
        name: "live",
        type: "bar",
        stack: "f",
        data: rows.map((r) => r.fires),
        itemStyle: { color: "#38bdf8" },
      },
      {
        name: "dry run",
        type: "bar",
        stack: "f",
        data: rows.map((r) => r.dry_run_fires),
        itemStyle: { color: "#a78bfa" },
      },
    ],
  };
}

export function Analytics() {
  const search = useSearch({ from: "/analytics" });
  const navigate = useNavigate({ from: "/analytics" });
  const hours = search.hours ?? 24;
  const { dark } = useTheme();
  const bucket = hours <= 1 ? 300 : hours <= 6 ? 900 : hours <= 24 ? 3600 : 6 * 3600;
  const components = useAnalytics("components", hours);
  const top = useAnalytics("top_codes", hours);
  const heat = useAnalytics("heatmap", hours, { bucket_s: bucket });
  const flapping = useAnalytics("flapping", hours, { min_cycles: 3 });
  const plans = useAnalytics("plans", hours);
  const rules = useAnalytics("rules", hours);
  const pareto = useMemo(() => paretoOption(top.data ?? [], dark), [top.data, dark]);
  const heatmap = useMemo(() => heatmapOption(heat.data ?? [], dark), [heat.data, dark]);
  const planChart = useMemo(() => plansOption(plans.data ?? [], dark), [plans.data, dark]);
  const ruleChart = useMemo(() => rulesOption(rules.data ?? [], dark), [rules.data, dark]);
  const error = components.error ?? top.error ?? heat.error ?? flapping.error ?? plans.error ?? rules.error;

  return (
    <div className="space-y-4">
      <PageHeader title="Analytics">
        <Tabs
          value={String(hours)}
          onValueChange={(v) => void navigate({ search: { hours: Number(v) }, replace: true })}
        >
          <TabsList>
            {WINDOWS.map((w) => (
              <TabsTrigger key={w.hours} value={String(w.hours)}>
                {w.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </PageHeader>
      <ErrorNote error={error} />
      <Card>
        <CardHeader title="Health per component" />
        {components.isLoading ? (
          <Loading />
        ) : !components.data?.length ? (
          <Empty>No component reported anything in this window.</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>component</Th>
                <Th>worst</Th>
                <Th className="text-right">events</Th>
                <Th className="text-right">faults</Th>
                <Th className="text-right">open</Th>
                <Th className="text-right">MTTR</Th>
                <Th className="text-right">MTBF</Th>
                <Th>last fault</Th>
              </tr>
            </thead>
            <tbody>
              {components.data.map((c) => (
                <tr key={c.component} data-testid="component-row">
                  <Td>
                    <Link to="/events" search={{ component: c.component }} className="hover:underline">
                      {c.component}
                    </Link>
                  </Td>
                  <Td>
                    <SeverityBadge severity={c.worst_severity} short />
                  </Td>
                  <Td className="text-right tabular">{c.events.toLocaleString()}</Td>
                  <Td className="text-right tabular">{c.faults}</Td>
                  <Td className="text-right tabular">{c.open_faults || "—"}</Td>
                  <Td className="text-right tabular">{duration(c.mttr_s)}</Td>
                  <Td className="text-right tabular">
                    {c.mtbf_h === null ? "—" : duration(c.mtbf_h * 3600)}
                  </Td>
                  <Td className="text-xs">{c.last_fault ? ago(c.last_fault) : "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Fault codes (Pareto)" />
          <CardBody>
            {top.data?.length ? (
              <Chart option={pareto} height={280} label="Pareto of fault codes" />
            ) : (
              <Empty>No confirmed faults.</Empty>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="When things break (faults per component)" />
          <CardBody>
            {heat.data?.length ? (
              <Chart option={heatmap} height={280} label="Faults heatmap" />
            ) : (
              <Empty>No confirmed faults.</Empty>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Recovery effectiveness" />
          <CardBody>
            {plans.data?.length ? (
              <>
                <Chart
                  option={planChart}
                  height={Math.max(140, plans.data.length * 36 + 50)}
                  label="Recovery outcomes per plan"
                />
                <div className="mt-2 flex flex-wrap gap-4 text-xs text-muted-fg">
                  {plans.data.map((p) => (
                    <span key={p.plan}>
                      <span className="font-mono text-fg">{p.plan}</span> {percent(p.success_rate)} · mean{" "}
                      {duration(p.mean_duration_s)}
                      {p.escalated ? ` · ${p.escalated} escalated` : ""}
                    </span>
                  ))}
                </div>
              </>
            ) : (
              <Empty>No recoveries.</Empty>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Rule fires" />
          <CardBody>
            {rules.data?.length ? (
              <Chart
                option={ruleChart}
                height={Math.max(140, rules.data.length * 36 + 50)}
                label="Rule fires"
              />
            ) : (
              <Empty>No rule fired.</Empty>
            )}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader title="Flapping faults (3+ cycles)" />
        {flapping.data?.length ? (
          <Table>
            <thead>
              <tr>
                <Th>code</Th>
                <Th>component</Th>
                <Th className="text-right">cycles</Th>
                <Th className="text-right">mean active</Th>
                <Th>first</Th>
                <Th>last</Th>
              </tr>
            </thead>
            <tbody>
              {flapping.data.map((f) => (
                <tr key={`${f.code}/${f.component}`}>
                  <Td className="font-mono text-xs">{f.code}</Td>
                  <Td className="text-xs">{f.component}</Td>
                  <Td className="text-right tabular">{f.cycles}</Td>
                  <Td className="text-right tabular">{duration(f.mean_active_s)}</Td>
                  <Td className="text-xs">{dateTime(f.first)}</Td>
                  <Td className="text-xs">{ago(f.last)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <Empty>Nothing keeps coming back.</Empty>
        )}
      </Card>
    </div>
  );
}
