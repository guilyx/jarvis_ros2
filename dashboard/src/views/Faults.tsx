import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { RotateCcw } from "lucide-react";
import { useMemo } from "react";
import { useFaultHistory } from "@/api/queries";
import { SEVERITIES, SEVERITY_LEVEL } from "@/api/types";
import {
  Empty,
  ErrorNote,
  IncidentLink,
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
import { RunPlanDialog } from "@/components/RunPlanDialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useLive } from "@/live/context";
import { useCan } from "@/lib/auth";
import { atLeast, between, dateTime, duration, severityName } from "@/lib/format";

export interface FaultsSearch {
  tab?: "active" | "history";
  min_severity?: string;
  component?: string;
  ack?: "yes" | "no";
}

export function Faults() {
  const search = useSearch({ from: "/faults" });
  const navigate = useNavigate({ from: "/faults" });
  const tab = search.tab ?? "active";
  const live = useLive((s) => s.faults);
  const history = useFaultHistory(tab === "history");
  const can = useCan();
  const set = (patch: Partial<FaultsSearch>) =>
    void navigate({ search: (s: FaultsSearch) => ({ ...s, ...patch }), replace: true });

  const active = useMemo(
    () =>
      (live ?? [])
        .filter((f) => atLeast(f.severity, search.min_severity))
        .filter((f) => !search.component || f.source.component === search.component)
        .filter((f) => !search.ack || (search.ack === "yes") === f.acknowledged)
        .sort(
          (a, b) =>
            SEVERITY_LEVEL[b.severity] - SEVERITY_LEVEL[a.severity] ||
            (a.first_seen ?? "").localeCompare(b.first_seen ?? ""),
        ),
    [live, search.min_severity, search.component, search.ack],
  );
  const components = useMemo(() => [...new Set((live ?? []).map((f) => f.source.component))].sort(), [live]);

  return (
    <div>
      <PageHeader title="Faults">
        <Select
          aria-label="Minimum severity"
          value={search.min_severity ?? ""}
          onChange={(e) => set({ min_severity: e.target.value || undefined })}
        >
          <option value="">Any severity</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              ≥ {s}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Component"
          value={search.component ?? ""}
          onChange={(e) => set({ component: e.target.value || undefined })}
        >
          <option value="">Any component</option>
          {components.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Acknowledged"
          value={search.ack ?? ""}
          onChange={(e) => set({ ack: (e.target.value || undefined) as FaultsSearch["ack"] })}
        >
          <option value="">Ack: any</option>
          <option value="yes">Acknowledged</option>
          <option value="no">Not acknowledged</option>
        </Select>
      </PageHeader>
      <Tabs value={tab} onValueChange={(v) => set({ tab: v as FaultsSearch["tab"] })}>
        <TabsList className="mb-3">
          <TabsTrigger value="active">Active ({live?.length ?? "…"})</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>
      </Tabs>
      <Card>
        {tab === "active" ? (
          live === null ? (
            <Loading />
          ) : !active.length ? (
            <Empty>No open faults.</Empty>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>sev</Th>
                  <Th>code</Th>
                  <Th>component</Th>
                  <Th>state</Th>
                  <Th className="text-right">occ.</Th>
                  <Th>age</Th>
                  <Th>ack</Th>
                  <Th>incident</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {active.map((f) => (
                  <tr key={f.fault_id} data-testid="fault-row" className={f.muted_by ? "opacity-60" : ""}>
                    <Td>
                      <SeverityBadge severity={f.severity} short />
                    </Td>
                    <Td>
                      <Link
                        to="/faults/$faultId"
                        params={{ faultId: f.fault_id }}
                        className="font-mono text-xs hover:underline"
                      >
                        {f.code}
                      </Link>
                      {f.muted_by ? (
                        <div className="text-[11px] text-muted-fg">muted: symptom of another fault</div>
                      ) : null}
                      {f.message ? (
                        <div className="max-w-md truncate text-xs text-muted-fg">{f.message}</div>
                      ) : null}
                    </Td>
                    <Td className="text-xs">{f.source.component}</Td>
                    <Td>
                      <StateBadge state={f.state} />
                    </Td>
                    <Td className="text-right tabular">{f.occurrences}</Td>
                    <Td className="text-xs tabular">{duration(between(f.confirmed_at ?? f.first_seen))}</Td>
                    <Td className="text-xs">{f.acknowledged ? f.acknowledged_by : "—"}</Td>
                    <Td>
                      <IncidentLink id={f.incident_id} />
                    </Td>
                    <Td>
                      <div className="flex justify-end gap-1.5">
                        <FaultActions fault={f} />
                        {can("operator") ? (
                          <RunPlanDialog
                            preset={{ component: f.source.component, fault: f.code }}
                            correlationId={f.incident_id}
                            trigger={
                              <Button size="sm" aria-label={`Recover ${f.code}`}>
                                <RotateCcw size={12} /> Recover
                              </Button>
                            }
                          />
                        ) : null}
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )
        ) : history.isLoading ? (
          <Loading />
        ) : (
          <>
            <ErrorNote error={history.error} />
            <Table>
              <thead>
                <tr>
                  <Th>sev</Th>
                  <Th>code</Th>
                  <Th>component</Th>
                  <Th>last state</Th>
                  <Th>first seen</Th>
                  <Th>active for</Th>
                  <Th className="text-right">occ.</Th>
                  <Th>incident</Th>
                </tr>
              </thead>
              <tbody>
                {(history.data?.history ?? [])
                  .filter((f) => atLeast(severityName(f.severity_level), search.min_severity))
                  .filter((f) => !search.component || f.component === search.component)
                  .map((f) => (
                    <tr key={f.fault_id}>
                      <Td>
                        <SeverityBadge severity={severityName(f.severity_level)} short />
                      </Td>
                      <Td>
                        <Link
                          to="/faults/$faultId"
                          params={{ faultId: f.fault_id }}
                          className="font-mono text-xs hover:underline"
                        >
                          {f.code}
                        </Link>
                        {f.muted ? <span className="ml-2 text-[11px] text-muted-fg">muted</span> : null}
                      </Td>
                      <Td className="text-xs">{f.component}</Td>
                      <Td>
                        <StateBadge state={f.last_state} />
                      </Td>
                      <Td className="text-xs tabular">{dateTime(f.first_seen)}</Td>
                      <Td className="text-xs tabular">
                        {f.confirmed_at ? duration(between(f.confirmed_at, f.cleared_at)) : "—"}
                      </Td>
                      <Td className="text-right tabular">
                        <Mono>{f.occurrences ?? "—"}</Mono>
                      </Td>
                      <Td>
                        <IncidentLink id={f.incident_id} />
                      </Td>
                    </tr>
                  ))}
              </tbody>
            </Table>
          </>
        )}
      </Card>
    </div>
  );
}
