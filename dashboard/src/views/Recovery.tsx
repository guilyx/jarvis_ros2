import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { Check, CircleDashed, Loader2, X } from "lucide-react";
import { useCancel, useRecoveries, useRecovery } from "@/api/queries";
import type { Execution, Step } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DecideButtons } from "@/components/DecideButtons";
import {
  Empty,
  ErrorNote,
  IncidentLink,
  KeyValues,
  Loading,
  Mono,
  PageHeader,
  SeverityBadge,
  StateBadge,
  Table,
  Td,
  Th,
} from "@/components/domain";
import { RunPlanDialog } from "@/components/RunPlanDialog";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useLive } from "@/live/context";
import { useCan } from "@/lib/auth";
import { ago, between, clock, dateTime, duration } from "@/lib/format";

export interface RecoverySearch {
  tab?: "running" | "pending" | "history";
}

function StepIcon({ step }: { step: Step }) {
  if (step.state === "SUCCEEDED") return <Check size={13} className="text-emerald-400" />;
  if (step.state === "FAILED" || step.state === "ABORTED") return <X size={13} className="text-red-400" />;
  if (step.state === "RUNNING" || step.state === "RETRYING")
    return <Loader2 size={13} className="animate-spin text-sky-400" />;
  return <CircleDashed size={13} className="text-muted-fg" />;
}

export function Steps({ x }: { x: Execution }) {
  return (
    <ol className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
      {x.steps.map((s) => (
        <li key={s.id} className="flex items-center gap-1.5" title={s.message}>
          <StepIcon step={s} />
          <span className="font-mono text-xs">{s.id}</span>
          <span className="text-xs text-muted-fg">
            {s.started ? duration(between(s.started, s.finished)) : ""}
            {s.attempts && s.attempts > 1 ? ` · try ${s.attempts}` : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function Gates({ x }: { x: Execution }) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
      {x.gates.map((g) => (
        <span key={g.gate} title={g.detail} className={g.passed ? "text-emerald-400" : "text-red-400"}>
          {g.gate} {g.passed ? "✔" : "✖"}
        </span>
      ))}
    </div>
  );
}

function requester(x: Execution) {
  return `${x.requester.type}${x.requester.id ? ` ${x.requester.id}` : ""}`;
}

function PendingCard({ x }: { x: Execution }) {
  const can = useCan();
  return (
    <Card className="ring-amber-500/30" data-testid="pending-execution">
      <CardBody className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm">
            <Link
              to="/recovery/$executionId"
              params={{ executionId: x.execution_id }}
              className="font-semibold hover:underline"
            >
              {x.plan_id}
            </Link>{" "}
            <span className="text-muted-fg">
              · requested by {requester(x)}
              {x.confidence ? ` · conf ${x.confidence.toFixed(2)}` : ""}
              {x.expires ? ` · expires ${ago(x.expires)}` : ""}
              {x.dry_run ? " · dry run" : ""}
            </span>
          </div>
          {can("operator") ? (
            <DecideButtons
              x={x}
              description={`It runs right away, with these arguments: ${
                Object.entries(x.args)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(", ") || "none"
              }.`}
            />
          ) : null}
        </div>
        {x.rationale ? <p className="text-sm">Rationale: {x.rationale}</p> : null}
        <Gates x={x} />
      </CardBody>
    </Card>
  );
}

function RunningRow({ x }: { x: Execution }) {
  const cancel = useCancel();
  const can = useCan();
  return (
    <div
      className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 px-4 py-3 last:border-0"
      data-testid="running-execution"
    >
      <div className="min-w-0 space-y-1.5">
        <div className="text-sm">
          <Link
            to="/recovery/$executionId"
            params={{ executionId: x.execution_id }}
            className="font-semibold hover:underline"
          >
            {x.plan_id}
          </Link>{" "}
          <span className="text-muted-fg">
            ({requester(x)}){x.dry_run ? " · dry run" : ""} · {duration(between(x.started ?? x.created))}
          </span>{" "}
          <StateBadge state={x.state} />
          {x.ladder?.running ? (
            <span className="ml-2 text-xs text-amber-400">escalation ladder running</span>
          ) : null}
        </div>
        <Steps x={x} />
      </div>
      {can("operator") ? (
        <ConfirmDialog
          trigger={<Button size="sm">Cancel</Button>}
          title={`Cancel ${x.plan_id}`}
          description="The step in flight is asked to stop; the plan's on-failure does not run."
          confirm="Cancel the execution"
          danger
          noteLabel="Reason"
          onConfirm={(reason) => cancel.mutateAsync({ id: x.execution_id, reason })}
        />
      ) : null}
    </div>
  );
}

export function Recovery() {
  const search = useSearch({ from: "/recovery" });
  const navigate = useNavigate({ from: "/recovery" });
  const snapshot = useLive((s) => s.executions);
  const recoveries = useRecoveries();
  const can = useCan();
  const live = snapshot?.executions ?? [];
  const pending = live.filter((x) => x.state === "PENDING_APPROVAL");
  const running = live.filter((x) => x.state !== "PENDING_APPROVAL");
  const tab = search.tab ?? (pending.length ? "pending" : "running");

  return (
    <div>
      <PageHeader title="Recovery">{can("operator") ? <RunPlanDialog /> : null}</PageHeader>
      <Tabs
        value={tab}
        onValueChange={(v) => void navigate({ search: { tab: v as RecoverySearch["tab"] }, replace: true })}
      >
        <TabsList className="mb-3">
          <TabsTrigger value="running">Running ({running.length})</TabsTrigger>
          <TabsTrigger value="pending">Pending approval ({pending.length})</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>
      </Tabs>
      {tab === "pending" ? (
        <div className="space-y-3">
          {pending.length ? (
            pending.map((x) => <PendingCard key={x.execution_id} x={x} />)
          ) : (
            <Card>
              <Empty>Nothing waits for an approval.</Empty>
            </Card>
          )}
        </div>
      ) : tab === "running" ? (
        <div className="space-y-4">
          <Card>
            {running.length ? (
              running.map((x) => <RunningRow key={x.execution_id} x={x} />)
            ) : (
              <Empty>Nothing is running.</Empty>
            )}
          </Card>
          <Card>
            <CardHeader title="Just finished" />
            {snapshot?.recent.length ? (
              [...snapshot.recent].reverse().map((x) => (
                <div
                  key={x.execution_id}
                  className="flex flex-wrap items-center gap-3 border-b border-border/60 px-4 py-2 text-sm last:border-0"
                  data-testid="recent-execution"
                >
                  <StateBadge state={x.state} />
                  <Link
                    to="/recovery/$executionId"
                    params={{ executionId: x.execution_id }}
                    className="font-mono text-xs hover:underline"
                  >
                    {x.plan_id}
                  </Link>
                  <span className="text-xs text-muted-fg">
                    {requester(x)}
                    {x.dry_run ? " · dry run" : ""} · {ago(x.finished)}
                  </span>
                  <span className="truncate text-xs text-muted-fg">{x.reason}</span>
                </div>
              ))
            ) : (
              <Empty>Nothing finished recently.</Empty>
            )}
          </Card>
        </div>
      ) : (
        <Card>
          <ErrorNote error={recoveries.error} />
          {recoveries.isLoading ? (
            <Loading />
          ) : !recoveries.data?.history.length ? (
            <Empty>No recovery in history yet.</Empty>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>plan</Th>
                  <Th>outcome</Th>
                  <Th>requester</Th>
                  <Th>started</Th>
                  <Th>took</Th>
                  <Th>incident</Th>
                </tr>
              </thead>
              <tbody>
                {recoveries.data.history.map((x) => (
                  <tr key={x.execution_id}>
                    <Td>
                      <Link
                        to="/recovery/$executionId"
                        params={{ executionId: x.execution_id }}
                        className="font-mono text-xs hover:underline"
                      >
                        {x.plan}
                      </Link>
                      {x.dry_run ? <span className="ml-2 text-[11px] text-muted-fg">dry run</span> : null}
                      {x.escalated ? <span className="ml-2 text-[11px] text-red-400">escalated</span> : null}
                    </Td>
                    <Td>
                      <StateBadge state={x.outcome ?? x.last_phase} />
                    </Td>
                    <Td className="text-xs">
                      {x.requester} {x.requester_id}
                    </Td>
                    <Td className="text-xs tabular">{dateTime(x.started_at ?? x.created_at)}</Td>
                    <Td className="text-xs tabular">{duration(between(x.started_at, x.finished_at))}</Td>
                    <Td>
                      <IncidentLink id={x.incident_id} />
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      )}
    </div>
  );
}

export function ExecutionDetail() {
  const { executionId } = useParams({ from: "/recovery/$executionId" });
  const detail = useRecovery(executionId);
  const live = useLive(
    (s) =>
      s.executions?.executions.find((x) => x.execution_id === executionId) ??
      s.executions?.recent.find((x) => x.execution_id === executionId) ??
      null,
  );
  if (detail.isLoading && !live) return <Loading />;
  const x = live ?? detail.data?.execution ?? null;
  const past = detail.data?.history ?? null;
  if (!x && !past) return <ErrorNote error={detail.error ?? "unknown execution"} />;
  return (
    <div className="space-y-4">
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <span className="font-mono">{x?.plan_id ?? past?.plan}</span>
            <StateBadge state={x?.state ?? past?.outcome ?? past?.last_phase} />
            {(x?.dry_run ?? past?.dry_run) ? <span className="text-sm text-muted-fg">dry run</span> : null}
          </span>
        }
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Execution" />
          <CardBody>
            <KeyValues
              rows={[
                ["id", <Mono key="i">{executionId}</Mono>],
                ["requested by", x ? requester(x) : `${past?.requester} ${past?.requester_id}`],
                ["rationale", x?.rationale || "—"],
                [
                  "arguments",
                  x ? (
                    <Mono key="a">
                      {Object.entries(x.args)
                        .map(([k, v]) => `${k}=${v}`)
                        .join("  ") || "—"}
                    </Mono>
                  ) : (
                    "—"
                  ),
                ],
                ["reason", x?.reason || "—"],
                ["approved by", x?.approved_by || "—"],
                ["locks", x?.locks?.join(", ") || "—"],
                ["created", dateTime(x?.created ?? past?.created_at)],
                ["took", duration(between(x?.started ?? past?.started_at, x?.finished ?? past?.finished_at))],
                ["incident", <IncidentLink key="c" id={x?.correlation_id || past?.incident_id} />],
                [
                  "started by",
                  x?.parent_id || past?.parent_id ? (
                    <Link
                      key="p"
                      to="/recovery/$executionId"
                      params={{ executionId: (x?.parent_id || past?.parent_id)! }}
                      className="font-mono text-xs text-sky-400 hover:underline"
                    >
                      {(x?.parent_id || past?.parent_id)!.slice(0, 8)}
                    </Link>
                  ) : (
                    "—"
                  ),
                ],
                [
                  "escalation of",
                  x?.ladder_of || past?.ladder_of ? (
                    <Link
                      key="l"
                      to="/recovery/$executionId"
                      params={{ executionId: (x?.ladder_of || past?.ladder_of)! }}
                      className="font-mono text-xs text-sky-400 hover:underline"
                    >
                      {(x?.ladder_of || past?.ladder_of)!.slice(0, 8)}
                    </Link>
                  ) : (
                    "—"
                  ),
                ],
              ]}
            />
          </CardBody>
        </Card>
        {x ? (
          <Card>
            <CardHeader title="Gates and steps" />
            <CardBody className="space-y-4">
              <Gates x={x} />
              <ol className="space-y-2">
                {x.steps.map((s) => (
                  <li key={s.id} className="flex items-start gap-2 text-sm">
                    <span className="mt-0.5">
                      <StepIcon step={s} />
                    </span>
                    <div>
                      <span className="font-mono text-xs">{s.id}</span>{" "}
                      <span className="text-xs text-muted-fg">{s.action}</span> <StateBadge state={s.state} />
                      <div className="text-xs text-muted-fg">
                        {s.started ? duration(between(s.started, s.finished)) : "not started"}
                        {s.attempts ? ` · ${s.attempts} attempt(s)` : ""} {s.message ? `· ${s.message}` : ""}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            </CardBody>
          </Card>
        ) : null}
      </div>
      {past?.events?.length ? (
        <Card>
          <CardHeader title="Its events" />
          {past.events.map((e) => (
            <div
              key={e.id}
              className="grid grid-cols-[7rem_5rem_minmax(10rem,1fr)_2fr] items-center gap-3 border-b border-border/50 px-4 py-1.5 text-sm"
            >
              <Mono className="tabular">{clock(e.stamp)}</Mono>
              <SeverityBadge severity={e.severity} short />
              <Mono className="truncate">{e.code}</Mono>
              <span className="truncate">{e.message}</span>
            </div>
          ))}
        </Card>
      ) : null}
    </div>
  );
}
