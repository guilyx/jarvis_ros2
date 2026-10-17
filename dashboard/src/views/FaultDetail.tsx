import { Link, useParams } from "@tanstack/react-router";
import { useFault } from "@/api/queries";
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
import { FaultActions } from "@/components/FaultActions";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useLive } from "@/live/context";
import { between, clock, dateTime, duration, severityName } from "@/lib/format";

export function FaultDetail() {
  const { faultId } = useParams({ from: "/faults/$faultId" });
  const detail = useFault(faultId);
  const live = useLive((s) => s.faults?.find((f) => f.fault_id === faultId) ?? null);
  if (detail.isLoading) return <Loading />;
  if (detail.error) return <ErrorNote error={detail.error} />;
  const d = detail.data!;
  const fault = live ?? d.fault;
  const ep = d.episode;
  const code = fault?.code ?? ep?.code ?? "";
  const severity = fault?.severity ?? severityName(ep?.severity_level);
  const incident = fault?.incident_id || ep?.incident_id;

  return (
    <div className="space-y-4">
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <SeverityBadge severity={severity} />
            <span className="font-mono">{code}</span>
            <StateBadge state={fault?.state ?? ep?.last_state} />
          </span>
        }
      >
        {fault && fault.state !== "CLEARED" ? <FaultActions fault={fault} /> : null}
      </PageHeader>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Fault" />
          <CardBody>
            <KeyValues
              rows={[
                ["component", fault?.source.component ?? ep?.component ?? "—"],
                ["node", <Mono key="n">{fault?.source.node ?? "—"}</Mono>],
                ["message", fault?.message || "—"],
                ["description", fault?.description || "—"],
                ["first seen", dateTime(fault?.first_seen ?? ep?.first_seen)],
                ["confirmed", dateTime(fault?.confirmed_at ?? ep?.confirmed_at)],
                ["cleared", dateTime(fault?.cleared_at ?? ep?.cleared_at)],
                [
                  "active for",
                  duration(
                    between(fault?.confirmed_at ?? ep?.confirmed_at, fault?.cleared_at ?? ep?.cleared_at),
                  ),
                ],
                ["occurrences", fault?.occurrences ?? ep?.occurrences ?? "—"],
                ["acknowledged", fault?.acknowledged ? `by ${fault.acknowledged_by}` : "no"],
                ["latch", fault?.latch ? "yes: stays until a human clears it" : "no"],
                [
                  "muted by",
                  fault?.muted_by ? (
                    <Link
                      key="m"
                      to="/faults/$faultId"
                      params={{ faultId: fault.muted_by }}
                      className="font-mono text-xs text-sky-400 hover:underline"
                    >
                      {fault.muted_by.slice(0, 8)} (root cause)
                    </Link>
                  ) : (
                    "—"
                  ),
                ],
                ["incident", <IncidentLink key="i" id={incident} />],
                [
                  "black box",
                  fault?.snapshot_ref ? (
                    <Link key="b" to="/system" className="font-mono text-xs text-sky-400 hover:underline">
                      {fault.snapshot_ref}
                    </Link>
                  ) : (
                    "—"
                  ),
                ],
                ["reported by", fault?.reporting_sources?.join(", ") || "—"],
              ]}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Freeze frame" />
          <CardBody>
            {fault?.freeze_frame && Object.keys(fault.freeze_frame).length ? (
              <KeyValues
                rows={Object.entries(fault.freeze_frame).map(([k, v]) => [
                  <Mono key={k}>{k}</Mono>,
                  <Mono key={`${k}v`}>{String(v)}</Mono>,
                ])}
              />
            ) : (
              <Empty className="py-4">
                No freeze frame: what the reporter attached when it was confirmed shows here.
              </Empty>
            )}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader title="State transitions" />
        {d.transitions.length ? (
          <Table>
            <thead>
              <tr>
                <Th>time</Th>
                <Th>from</Th>
                <Th>to</Th>
                <Th className="text-right">occ.</Th>
                <Th>message</Th>
              </tr>
            </thead>
            <tbody>
              {d.transitions.map((t) => (
                <tr key={t.seq}>
                  <Td className="font-mono text-xs tabular">{clock(t.ts)}</Td>
                  <Td>
                    <StateBadge state={t.prev_state} />
                  </Td>
                  <Td>
                    <StateBadge state={t.state} />
                  </Td>
                  <Td className="text-right tabular">{t.occurrences ?? "—"}</Td>
                  <Td className="text-sm">{t.message}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <Empty>No transitions recorded yet.</Empty>
        )}
      </Card>
      <Card>
        <CardHeader title="Recoveries for its incident" />
        {d.executions.length ? (
          <Table>
            <thead>
              <tr>
                <Th>plan</Th>
                <Th>requester</Th>
                <Th>outcome</Th>
                <Th>started</Th>
              </tr>
            </thead>
            <tbody>
              {d.executions.map((x) => (
                <tr key={x.execution_id}>
                  <Td>
                    <Link
                      to="/recovery/$executionId"
                      params={{ executionId: x.execution_id }}
                      className="font-mono text-xs hover:underline"
                    >
                      {x.plan}
                    </Link>
                  </Td>
                  <Td className="text-xs">
                    {x.requester} {x.requester_id}
                  </Td>
                  <Td>
                    <StateBadge state={x.outcome ?? x.last_phase} />
                  </Td>
                  <Td className="text-xs tabular">{dateTime(x.started_at ?? x.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <Empty>No recovery ran for it.</Empty>
        )}
      </Card>
    </div>
  );
}
