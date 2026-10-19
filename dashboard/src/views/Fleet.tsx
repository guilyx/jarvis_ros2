import { Link } from "@tanstack/react-router";
import { ExternalLink, WifiOff } from "lucide-react";
import { useFleet } from "@/api/queries";
import type { FleetRobot } from "@/api/types";
import { Empty, ErrorNote, Loading, Mono, PageHeader, SeverityBadge, StateBadge } from "@/components/domain";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/cn";
import { SEVERITY_STYLE } from "@/lib/severity";
import { FleetHistory } from "./FleetHistory";

const COUNTS = ["CRITICAL", "ERROR", "WARN"] as const;

/** Worst first: offline, then by their worst open fault, then by name. */
export function fleetOrder(a: FleetRobot, b: FleetRobot): number {
  const rank = (r: FleetRobot) =>
    !r.online ? 0 : r.faults.CRITICAL ? 1 : r.faults.ERROR ? 2 : r.faults.WARN ? 3 : r.faults.total ? 4 : 5;
  return rank(a) - rank(b) || a.name.localeCompare(b.name);
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
