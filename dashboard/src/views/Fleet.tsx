import { Link } from "@tanstack/react-router";
import { ExternalLink, WifiOff } from "lucide-react";
import { useFleet, useFleetAlerts } from "@/api/queries";
import type { FleetRobot } from "@/api/types";
import {
  Empty,
  ErrorNote,
  Loading,
  Mono,
  PageHeader,
  SeverityBadge,
  StateBadge,
  Table,
  Td,
  Th,
} from "@/components/domain";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { cn } from "@/lib/cn";
import { ago, dateTime, duration } from "@/lib/format";
import { SEVERITY_STYLE } from "@/lib/severity";
import { FleetHistory } from "./FleetHistory";

const COUNTS = ["CRITICAL", "ERROR", "WARN"] as const;

/** Worst first: offline, then by their worst open fault, then by name. */
export function fleetOrder(a: FleetRobot, b: FleetRobot): number {
  const rank = (r: FleetRobot) =>
    !r.online ? 0 : r.faults.CRITICAL ? 1 : r.faults.ERROR ? 2 : r.faults.WARN ? 3 : r.faults.total ? 4 : 5;
  return rank(a) - rank(b) || a.name.localeCompare(b.name);
}

export interface Rollout {
  source: string;
  revisions: { revision: string; robots: string[] }[]; // most robots first
  refused: { revision: string; robots: string[]; error: string }[];
  own: string[]; // online, with their own rules and plans (no config_from)
}

const group = <T,>(items: T[], key: (t: T) => string) => {
  const out = new Map<string, T[]>();
  for (const t of items) out.set(key(t), [...(out.get(key(t)) ?? []), t]);
  return [...out.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
};

/** The fleet's rules and plans across the robots that take them: which revision runs where, what
 * was refused where and why. Null when no robot takes them from the fleet. */
export function rollout(robots: FleetRobot[]): Rollout | null {
  const online = robots.filter((r) => r.online);
  const taking = online.filter((r) => r.config);
  if (!taking.length) return null;
  const names = (rs: FleetRobot[]) => rs.map((r) => r.name).sort();
  return {
    source: taking[0]!.config!.source,
    revisions: group(taking, (r) => r.config!.revision ?? "none").map(([revision, rs]) => ({
      revision,
      robots: names(rs),
    })),
    refused: group(
      taking.filter((r) => r.config!.rejected),
      (r) => r.config!.rejected!,
    ).map(([revision, rs]) => ({ revision, robots: names(rs), error: rs[0]!.config!.error })),
    own: names(online.filter((r) => !r.config)),
  };
}

function Robots({ names }: { names: string[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {names.map((n) => (
        <span key={n} className="rounded bg-muted px-1.5 py-0.5 text-xs ring-1 ring-border">
          {n}
        </span>
      ))}
    </span>
  );
}

/** Which revision of the fleet's rules and plans runs where (docs/guides/fleet.md §5). */
function RolloutCard({ r }: { r: Rollout }) {
  return (
    <Card data-testid="fleet-rollout">
      <CardHeader
        title="Rules and plans"
        actions={
          <span className="text-xs text-muted-fg">
            from <Mono>{r.source}</Mono>
          </span>
        }
      />
      <CardBody className="space-y-2 text-sm">
        {r.revisions.map((v) => (
          <div key={v.revision} className="flex items-center gap-3" data-testid="fleet-rollout-revision">
            <span className="w-24 shrink-0 text-muted-fg">running</span>
            <Mono className="w-32 shrink-0">{v.revision}</Mono>
            <span className="w-20 shrink-0 text-muted-fg">
              {v.robots.length} robot{v.robots.length === 1 ? "" : "s"}
            </span>
            <Robots names={v.robots} />
          </div>
        ))}
        {r.refused.map((v) => (
          <div key={v.revision} className="flex items-start gap-3" data-testid="fleet-rollout-refused">
            <span className="w-24 shrink-0 text-amber-500">refused</span>
            <Mono className="w-32 shrink-0 text-amber-500">{v.revision}</Mono>
            <span className="w-20 shrink-0 text-muted-fg">
              {v.robots.length} robot{v.robots.length === 1 ? "" : "s"}
            </span>
            <div className="min-w-0 space-y-1">
              <Robots names={v.robots} />
              <Mono className="block break-all text-xs text-muted-fg">{v.error}</Mono>
            </div>
          </div>
        ))}
        {r.own.length ? (
          <div className="flex items-center gap-3 text-muted-fg">
            <span className="w-24 shrink-0">their own</span>
            <span className="w-32 shrink-0" />
            <span className="w-20 shrink-0">
              {r.own.length} robot{r.own.length === 1 ? "" : "s"}
            </span>
            <Robots names={r.own} />
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}

/** What happened to the fleet while nobody was looking (fleet.watch): the gateway's alerts. */
function Alerts({ several }: { several: boolean }) {
  const alerts = useFleetAlerts();
  const a = alerts.data;
  if (!a) return null;
  if (!a.watching) {
    return several ? (
      <Empty>
        No alerts: this gateway doesn't watch the fleet. Set <Mono>fleet.watch</Mono> and it tells the
        webhooks when a robot goes offline or refuses the fleet's rules (docs/guides/fleet.md §6).
      </Empty>
    ) : null;
  }
  return (
    <Card data-testid="fleet-alerts">
      <CardHeader
        title="Alerts"
        actions={
          <span className="text-xs text-muted-fg">
            every {duration(a.period_s)}, offline after {duration(a.offline_after_s)}, to channel{" "}
            <Mono>{a.channel}</Mono>
          </span>
        }
      />
      {a.alerts.length ? (
        <div className="max-h-80 overflow-y-auto">
          <Table>
            <thead>
              <tr>
                <Th>when</Th>
                <Th>robot</Th>
                <Th />
                <Th>what</Th>
              </tr>
            </thead>
            <tbody>
              {a.alerts.map((e) => (
                <tr key={e.id} data-testid="fleet-alert">
                  <Td className="whitespace-nowrap text-xs text-muted-fg">
                    <span title={dateTime(e.stamp)}>{ago(e.stamp)}</span>
                  </Td>
                  <Td className="font-medium">{e.source.robot}</Td>
                  <Td>
                    <SeverityBadge severity={e.severity} short />
                  </Td>
                  <Td className="text-sm">{e.message}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      ) : (
        <Empty className="py-4">Nothing happened to the fleet since the gateway started.</Empty>
      )}
    </Card>
  );
}

function Counts({ robot }: { robot: FleetRobot }) {
  if (!robot.online) return null;
  if (!robot.faults.total) return <span className="text-sm text-emerald-400">No open faults</span>;
  return (
    <div className="flex items-center gap-1.5">
      {COUNTS.map((sev) =>
        robot.faults[sev] ? (
          <Badge key={sev} className={cn(SEVERITY_STYLE[sev].bg, SEVERITY_STYLE[sev].text)} title={sev}>
            {robot.faults[sev]} {SEVERITY_STYLE[sev].short}
          </Badge>
        ) : null,
      )}
      <span className="text-xs text-muted-fg">
        {robot.faults.total} open · {robot.incidents} incident{robot.incidents === 1 ? "" : "s"}
      </span>
    </div>
  );
}

function RobotCard({ robot }: { robot: FleetRobot }) {
  const worst = robot.worst;
  return (
    <Card
      data-testid="fleet-robot"
      className={cn("flex flex-col gap-3 p-4", !robot.online && "opacity-70")}
      aria-label={robot.name}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "h-2 w-2 shrink-0 rounded-full",
                !robot.online
                  ? "bg-slate-500"
                  : robot.faults.CRITICAL
                    ? "bg-red-500"
                    : robot.faults.total
                      ? "bg-orange-400"
                      : "bg-emerald-400",
              )}
              aria-hidden
            />
            <span className="truncate font-semibold">{robot.name}</span>
            {robot.local ? <Badge className="bg-muted text-muted-fg ring-border">this robot</Badge> : null}
          </div>
          <div className="mt-0.5 truncate text-xs text-muted-fg">
            {robot.local ? "this gateway" : <Mono>{robot.url}</Mono>}
            {robot.online && robot.latency_ms != null ? ` · ${robot.latency_ms} ms` : ""}
          </div>
        </div>
        {robot.online ? <StateBadge state={robot.mode} /> : null}
      </div>

      {robot.online ? (
        <>
          <Counts robot={robot} />
          {worst ? (
            <div className="rounded-md bg-muted/50 p-2.5 text-sm" data-testid="fleet-worst">
              <div className="flex items-center gap-2">
                <SeverityBadge severity={worst.severity} short />
                <Mono className="truncate">{worst.code}</Mono>
                {worst.state === "LATCHED" ? <StateBadge state="LATCHED" /> : null}
              </div>
              <div className="mt-1 truncate text-xs text-muted-fg">
                {worst.component}
                {worst.message ? ` · ${worst.message}` : ""}
              </div>
            </div>
          ) : null}
          {robot.config ? (
            <div className="truncate text-xs text-muted-fg" data-testid="fleet-config">
              rules and plans <Mono>{robot.config.revision ?? "none"}</Mono>
              {robot.config.rejected ? (
                <span className="text-amber-500" title={robot.config.error}>
                  {" "}
                  · refused <Mono>{robot.config.rejected}</Mono>
                </span>
              ) : ["offline", "empty", "waiting"].includes(robot.config.state) ? (
                <span className="text-amber-500"> · {robot.config.state}</span>
              ) : null}
            </div>
          ) : null}
          {robot.status && robot.status !== "ok" ? (
            <div className="text-xs text-amber-400">Jarvis is {robot.status} on this robot</div>
          ) : null}
        </>
      ) : (
        <div className="flex items-center gap-2 text-sm text-muted-fg">
          <WifiOff size={14} aria-hidden /> Offline: {robot.error || "no answer"}
        </div>
      )}

      <div className="mt-auto text-sm">
        {robot.local ? (
          <Link to="/faults" className="text-sky-400 hover:underline">
            Its faults
          </Link>
        ) : (
          <a
            href={robot.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-sky-400 hover:underline"
          >
            Open its dashboard <ExternalLink size={12} aria-hidden />
          </a>
        )}
      </div>
    </Card>
  );
}

export function Fleet() {
  const fleet = useFleet();
  if (fleet.isLoading) return <Loading />;
  const robots = [...(fleet.data?.robots ?? [])].sort(fleetOrder);
  const offline = robots.filter((r) => !r.online).length;
  const faulted = robots.filter((r) => r.online && r.faults.total).length;
  const rolled = rollout(robots);
  return (
    <div className="space-y-4">
      <PageHeader title="Fleet">
        <span className="text-sm text-muted-fg" data-testid="fleet-summary">
          {robots.length} robot{robots.length === 1 ? "" : "s"} · {faulted} with open faults · {offline}{" "}
          offline
        </span>
      </PageHeader>
      <ErrorNote error={fleet.error} />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {robots.map((r) => (
          <RobotCard key={r.local ? "" : r.url} robot={r} />
        ))}
      </div>
      <Alerts several={robots.length > 1} />
      {rolled ? <RolloutCard r={rolled} /> : null}
      {robots.length <= 1 ? (
        <Empty>
          Only this robot. List the others under <Mono>fleet:</Mono> in the gateway's settings
          (docs/guides/fleet.md).
        </Empty>
      ) : null}
      <FleetHistory />
    </div>
  );
}
