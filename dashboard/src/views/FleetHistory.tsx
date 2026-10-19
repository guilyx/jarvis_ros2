import { useMemo, useState } from "react";
import { ApiError } from "@/api/client";
import { useFleetHistory } from "@/api/queries";
import type { FleetHistory as History, FleetHistoryRobot } from "@/api/types";
import { Chart, chartColors, type ChartOption } from "@/components/Chart";
import { Empty, ErrorNote, Loading, Mono, Table, Td, Th } from "@/components/domain";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ago, dateTime, duration, percent } from "@/lib/format";
import { useTheme } from "@/lib/theme";

const WINDOWS = [
  { hours: 24, label: "24 h" },
  { hours: 168, label: "7 d" },
  { hours: 720, label: "30 d" },
];
// One color per robot, in their order (up to six; then they repeat).
const ROBOT_COLORS = ["#38bdf8", "#f97316", "#a78bfa", "#10b981", "#f43f5e", "#eab308"];

/** Confirmed faults per bucket, one stacked bar series per robot. */
export function trendOption(history: History, dark: boolean): ChartOption {
  const c = chartColors(dark);
  const axis = {
    axisLine: { lineStyle: { color: c.line } },
    axisLabel: { color: c.text },
    splitLine: { lineStyle: { color: c.line } },
  };
  const buckets = [...new Set(history.trend.map((p) => p.bucket))].sort();
  const label = (b: string) => (history.bucket_s >= 86400 ? b.slice(5, 10) : b.slice(11, 16));
  return {
    grid: { left: 40, right: 16, top: 32, bottom: 28 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { color: c.text } },
    xAxis: { type: "category", data: buckets.map(label), ...axis },
    yAxis: { type: "value", minInterval: 1, ...axis },
    series: history.robots.map((r, i) => ({
      name: r.robot,
      type: "bar",
      stack: "faults",
      data: buckets.map((b) => history.trend.find((p) => p.bucket === b && p.robot === r.robot)?.faults ?? 0),
      itemStyle: { color: ROBOT_COLORS[i % ROBOT_COLORS.length] },
    })),
  };
}

/** Most faults first: the robot that needs looking at. */
export function byFaults(a: FleetHistoryRobot, b: FleetHistoryRobot): number {
  return b.faults_confirmed - a.faults_confirmed || a.robot.localeCompare(b.robot);
}

function NotSetUp() {
  return (
    <Empty>
      No fleet history here. The robots upload theirs with <Mono>lake_upload_to</Mono>; then set{" "}
      <Mono>fleet.history</Mono> in this gateway's settings to where they do (docs/guides/fleet.md).
    </Empty>
  );
}

/** The Fleet page's History: what the robots uploaded, over a window. */
export function FleetHistory() {
  const [hours, setHours] = useState(168);
  const history = useFleetHistory(hours);
  const { dark } = useTheme();
  const option = useMemo(() => (history.data ? trendOption(history.data, dark) : null), [history.data, dark]);
  const notSetUp = history.error instanceof ApiError && history.error.status === 404;
  const h = history.data;
  return (
    <Card data-testid="fleet-history">
      <CardHeader
        title="History"
        actions={
          <Tabs value={String(hours)} onValueChange={(v) => setHours(Number(v))}>
            <TabsList>
              {WINDOWS.map((w) => (
                <TabsTrigger key={w.hours} value={String(w.hours)}>
                  {w.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        }
      />
      <CardBody className="space-y-4">
        {history.isLoading ? (
          <Loading />
        ) : notSetUp ? (
          <NotSetUp />
        ) : history.error ? (
          <ErrorNote error={history.error} />
        ) : !h?.robots.length ? (
          <Empty>Nothing uploaded yet.</Empty>
        ) : (
          <>
            <div className="text-xs text-muted-fg">
              What the robots uploaded to <Mono>{h.target}</Mono>, read{" "}
              {ago(new Date(h.updated_at * 1000).toISOString())}
            </div>
            <Table>
              <thead>
                <tr>
                  <Th>robot</Th>
                  <Th className="text-right">faults</Th>
                  <Th className="text-right">MTTR</Th>
                  <Th className="text-right">incidents</Th>
                  <Th className="text-right">escalated</Th>
                  <Th className="text-right">recovered alone</Th>
                  <Th className="text-right">recoveries OK</Th>
                  <Th>history until</Th>
                </tr>
              </thead>
              <tbody>
                {[...h.robots].sort(byFaults).map((r) => (
                  <tr key={r.robot} data-testid="fleet-history-robot">
                    <Td className="font-medium">{r.robot}</Td>
                    <Td className="text-right tabular-nums">{r.faults_confirmed}</Td>
                    <Td className="text-right tabular-nums">{duration(r.mttr_s)}</Td>
                    <Td className="text-right tabular-nums">{r.incidents_opened}</Td>
                    <Td className="text-right tabular-nums">{r.incidents_escalated}</Td>
                    <Td className="text-right tabular-nums">{percent(r.auto_recovery_rate)}</Td>
                    <Td className="text-right tabular-nums">{percent(r.recovery_success_rate)}</Td>
                    <Td className="text-muted-fg">
                      <span title={r.history_until ? dateTime(r.history_until) : undefined}>
                        {ago(r.history_until)}
                      </span>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <div className="grid gap-4 xl:grid-cols-2">
              <div>
                <div className="mb-2 text-sm text-muted-fg">
                  Confirmed faults per {h.bucket_s >= 86400 ? "day" : "hour"}
                </div>
                {option ? (
                  <Chart option={option} height={240} label="Confirmed faults per robot over time" />
                ) : null}
              </div>
              <div>
                <div className="mb-2 text-sm text-muted-fg">What costs the fleet most</div>
                <Table>
                  <thead>
                    <tr>
                      <Th>code</Th>
                      <Th className="text-right">episodes</Th>
                      <Th>on</Th>
                      <Th className="text-right">MTTR</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {h.top_codes.map((c) => (
                      <tr key={c.code} data-testid="fleet-history-code">
                        <Td>
                          <Mono>{c.code}</Mono>
                        </Td>
                        <Td className="text-right tabular-nums">{c.episodes}</Td>
                        <Td className="text-muted-fg">{c.robot_names.join(", ")}</Td>
                        <Td className="text-right tabular-nums">{duration(c.mttr_s)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}
