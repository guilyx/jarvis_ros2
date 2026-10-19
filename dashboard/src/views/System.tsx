import { Download } from "lucide-react";
import type { ReactNode } from "react";
import { getToken } from "@/api/client";
import { useAudit, useBags, useSystem } from "@/api/queries";
import {
  Empty,
  ErrorNote,
  IncidentLink,
  KeyValues,
  Loading,
  Mono,
  PageHeader,
  StateBadge,
  Table,
  Td,
  Th,
} from "@/components/domain";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useLive } from "@/live/context";
import { ago, bytes, dateTime, duration } from "@/lib/format";

export function System() {
  const system = useSystem();
  const bags = useBags();
  const audit = useAudit(50);
  const status = useLive((s) => s.status);
  const token = getToken();
  const s = system.data;
  if (system.isLoading) return <Loading />;
  const lake = s?.lake;
  return (
    <div className="space-y-4">
      <PageHeader title="System" />
      <ErrorNote error={system.error} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader title="Gateway" />
          <CardBody>
            <KeyValues
              rows={[
                ["robot", s?.robot || "—"],
                ["version", s?.version],
                ["up for", duration(s?.uptime_s)],
                [
                  "Jarvis",
                  s?.connected ? (
                    <span key="c" className="text-emerald-400">
                      connected
                    </span>
                  ) : (
                    <span key="c" className="text-red-400">
                      not answering
                    </span>
                  ),
                ],
                ["live stream", status],
                ["events received", s?.events_received.toLocaleString()],
                ...((s?.events_dropped
                  ? [
                      [
                        "dropped",
                        <span key="d" className="text-amber-400">
                          {s.events_dropped.toLocaleString()} (the gateway fell behind; the lake has them)
                        </span>,
                      ],
                    ]
                  : []) as [ReactNode, ReactNode][]),
                ["held in memory", `${s?.recent_events.toLocaleString()} (until the lake has them)`],
                ["stream clients", s?.stream_clients],
              ]}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Lake (history)" />
          <CardBody>
            {lake ? (
              <KeyValues
                rows={[
                  ["rows", lake.rows?.toLocaleString() ?? "—"],
                  ["splits", `${lake.splits_converted ?? 0} converted, ${lake.splits_pending ?? 0} pending`],
                  ["failed", Object.keys(lake.splits_failed ?? {}).length || "none"],
                  ["recordings", bytes(lake.bags_bytes)],
                  ["parquet", bytes(lake.lake_bytes)],
                  ["black box", bytes(lake.blackbox_bytes)],
                  ["days", lake.partitions?.length ?? 0],
                  ["updated", lake.updated_at ? ago(new Date(lake.updated_at * 1000).toISOString()) : "—"],
                  ["last error", lake.last_error || "—"],
                ]}
              />
            ) : (
              <Empty className="py-4">The lake service has not reported yet.</Empty>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Lifecycle of watched nodes" />
          {s && Object.keys(s.components).length ? (
            <Table>
              <tbody>
                {Object.entries(s.components).map(([node, c]) => (
                  <tr key={node}>
                    <Td>
                      <Mono>{node}</Mono>
                    </Td>
                    <Td>
                      <StateBadge state={c.state} />
                    </Td>
                    <Td className="text-xs text-muted-fg">{ago(c.since)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <Empty className="py-4">No lifecycle bridge reported yet.</Empty>
          )}
        </Card>
      </div>
      <Card>
        <CardHeader
          title="Notifications"
          actions={
            <span className="text-xs text-muted-fg">
              webhooks: {s?.webhooks.join(", ") || "none configured"}
            </span>
          }
        />
        {s?.notifications.length ? (
          <Table>
            <thead>
              <tr>
                <Th>when</Th>
                <Th>webhook</Th>
                <Th>channel</Th>
                <Th>result</Th>
                <Th className="text-right">attempts</Th>
              </tr>
            </thead>
            <tbody>
              {[...s.notifications].reverse().map((d, i) => (
                <tr key={i}>
                  <Td className="text-xs">{ago(new Date(d.at * 1000).toISOString())}</Td>
                  <Td>{d.webhook || "—"}</Td>
                  <Td className="text-xs">{d.channel}</Td>
                  <Td className={d.ok ? "text-emerald-400" : d.silenced ? "text-amber-500" : "text-red-400"}>
                    {d.ok ? `delivered (${d.status})` : d.error}
                  </Td>
                  <Td className="text-right tabular">{d.attempts}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <Empty>No notification sent yet.</Empty>
        )}
      </Card>
      <Card>
        <CardHeader title="Recordings" />
        <ErrorNote error={bags.error} />
        {bags.data?.bags.length ? (
          <div className="max-h-96 overflow-y-auto">
            <Table>
              <thead>
                <tr>
                  <Th>kind</Th>
                  <Th>file</Th>
                  <Th>from</Th>
                  <Th>to</Th>
                  <Th className="text-right">size</Th>
                  <Th>status</Th>
                  <Th>incidents</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {bags.data.bags.map((b) => (
                  <tr key={`${b.kind}/${b.bag}/${b.file}`}>
                    <Td className="text-xs">{b.kind === "blackbox" ? "black box" : "recording"}</Td>
                    <Td>
                      <Mono>
                        {b.bag}/{b.file}
                      </Mono>
                    </Td>
                    <Td className="text-xs tabular">{dateTime(b.start)}</Td>
                    <Td className="text-xs tabular">{dateTime(b.end)}</Td>
                    <Td className="text-right text-xs tabular">{bytes(b.size)}</Td>
                    <Td className="text-xs">
                      {b.open
                        ? "recording"
                        : b.converted
                          ? "in the lake"
                          : b.kind === "blackbox"
                            ? "—"
                            : "closed"}
                    </Td>
                    <Td className="space-x-1">
                      {b.incidents.slice(0, 3).map((id) => (
                        <IncidentLink key={id} id={id} />
                      ))}
                      {b.incidents.length > 3 ? (
                        <span className="text-xs text-muted-fg">+{b.incidents.length - 3}</span>
                      ) : null}
                    </Td>
                    <Td>
                      {!b.open ? (
                        <a
                          href={`/api/v1/bags/${b.kind}/${encodeURIComponent(b.bag)}/${encodeURIComponent(b.file)}${token ? `?token=${encodeURIComponent(token)}` : ""}`}
                          className="inline-flex items-center gap-1 text-xs text-sky-400 hover:underline"
                          download
                        >
                          <Download size={12} /> MCAP
                        </a>
                      ) : null}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
        ) : (
          <Empty>No recordings.</Empty>
        )}
      </Card>
      <Card>
        <CardHeader title="Audit trail" />
        {audit.data?.events.length ? (
          <div className="max-h-96 overflow-y-auto">
            {audit.data.events.map((e) => (
              <div
                key={e.id}
                className="grid grid-cols-[10rem_12rem_1fr] gap-3 border-b border-border/50 px-4 py-1.5 text-sm"
                data-testid="audit-row"
              >
                <span className="text-xs tabular text-muted-fg">{dateTime(e.stamp)}</span>
                <Mono className="truncate">{e.code}</Mono>
                <span className="truncate">{e.message}</span>
              </div>
            ))}
          </div>
        ) : (
          <Empty>Nobody did anything yet.</Empty>
        )}
      </Card>
    </div>
  );
}
