import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useIncidents } from "@/api/queries";
import {
  Empty,
  ErrorNote,
  Loading,
  PageHeader,
  SeverityBadge,
  StateBadge,
  Table,
  Td,
  Th,
} from "@/components/domain";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { between, dateTime, duration, severityName } from "@/lib/format";

export interface IncidentsSearch {
  state?: string;
}

export function Incidents() {
  const search = useSearch({ from: "/incidents" });
  const navigate = useNavigate({ from: "/incidents" });
  const incidents = useIncidents(search.state ?? "");
  return (
    <div>
      <PageHeader title="Incidents">
        <Select
          aria-label="State"
          value={search.state ?? ""}
          onChange={(e) => void navigate({ search: { state: e.target.value || undefined }, replace: true })}
        >
          <option value="">All</option>
          <option value="open">Open (open, recovering, escalated)</option>
          {["OPEN", "RECOVERING", "ESCALATED", "RESOLVED", "CLOSED"].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
      </PageHeader>
      <ErrorNote error={incidents.error} />
      <Card>
        {incidents.isLoading ? (
          <Loading />
        ) : !incidents.data?.incidents.length ? (
          <Empty>No incidents{search.state ? " in this state" : ""}.</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>sev</Th>
                <Th>incident</Th>
                <Th>component</Th>
                <Th>state</Th>
                <Th>opened</Th>
                <Th>lasted</Th>
                <Th>escalated</Th>
              </tr>
            </thead>
            <tbody>
              {incidents.data.incidents.map((i) => (
                <tr key={i.incident_id} data-testid="incident-row">
                  <Td>
                    <SeverityBadge severity={severityName(i.severity_level)} short />
                  </Td>
                  <Td>
                    <Link
                      to="/incidents/$incidentId"
                      params={{ incidentId: i.incident_id }}
                      className="hover:underline"
                    >
                      {i.title}
                    </Link>
                    <div className="font-mono text-[11px] text-muted-fg">{i.incident_id.slice(0, 8)}</div>
                  </Td>
                  <Td className="text-xs">{i.component}</Td>
                  <Td>
                    <StateBadge state={i.state} />
                  </Td>
                  <Td className="text-xs tabular">{dateTime(i.opened_at)}</Td>
                  <Td className="text-xs tabular">
                    {duration(between(i.opened_at, i.resolved_at ?? i.closed_at))}
                  </Td>
                  <Td className="text-xs">{i.escalated ? <span className="text-red-400">yes</span> : "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
