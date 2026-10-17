import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useEvent } from "@/api/queries";
import type { JarvisEvent } from "@/api/types";
import { Sheet } from "@/components/ui/dialog";
import { dateTime } from "@/lib/format";
import { Empty, ErrorNote, IncidentLink, Json, KeyValues, Mono, SeverityBadge } from "./domain";

function EventLine({ e, onPick }: { e: JarvisEvent; onPick: (id: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onPick(e.id)}
      className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-muted cursor-pointer"
    >
      <SeverityBadge severity={e.severity} short />
      <Mono className="text-muted-fg">{e.seq}</Mono>
      <Mono>{e.code}</Mono>
      <span className="truncate text-muted-fg">{e.message}</span>
    </button>
  );
}

/** One event: every field, its attributes and payload, what caused it and what it caused. */
export function EventSheet({
  id,
  onClose,
  onPick,
}: {
  id: string | null;
  onClose: () => void;
  onPick?: (id: string) => void;
}) {
  const detail = useEvent(id);
  const e = detail.data?.event;
  const pick = onPick ?? (() => {});
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()} title={e ? `${e.kind} · ${e.code}` : "Event"}>
      <ErrorNote error={detail.error} />
      {!e ? (
        <Empty>Loading…</Empty>
      ) : (
        <div className="space-y-5">
          <div className="flex items-center gap-2">
            <SeverityBadge severity={e.severity} />
            <span className="text-sm">{e.message}</span>
          </div>
          <KeyValues
            rows={[
              ["seq", <Mono key="s">{e.seq}</Mono>],
              ["id", <Mono key="i">{e.id}</Mono>],
              ["time", dateTime(e.stamp)],
              ["received", dateTime(e.recv_stamp)],
              ["robot", e.source.robot],
              ["node", <Mono key="n">{e.source.node}</Mono>],
              ["component", e.source.component || "—"],
              ["host", e.source.host || "—"],
              [
                "incident",
                <IncidentLink
                  key="c"
                  id={e.correlation_id || (e.attrs?.incident_id as string | undefined)}
                />,
              ],
              ["tags", e.tags?.join(", ") || "—"],
            ]}
          />
          {e.attrs && Object.keys(e.attrs).length ? (
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-fg">
                Attributes
              </h3>
              <KeyValues
                rows={Object.entries(e.attrs).map(([k, v]) => [
                  <Mono key={k}>{k}</Mono>,
                  <Mono key={`${k}v`}>{String(v)}</Mono>,
                ])}
              />
            </section>
          ) : null}
          {e.payload ? (
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-fg">
                Payload {e.payload_schema ? <span className="normal-case">({e.payload_schema})</span> : null}
              </h3>
              <Json value={typeof e.payload === "string" ? safeParse(e.payload) : e.payload} />
            </section>
          ) : null}
          <section>
            <h3 className="mb-2 flex items-center gap-1 text-xs font-semibold uppercase tracking-wider text-muted-fg">
              <ArrowUp size={12} /> Caused by
            </h3>
            {detail.data?.parent ? (
              <EventLine e={detail.data.parent} onPick={pick} />
            ) : (
              <p className="text-sm text-muted-fg">—</p>
            )}
          </section>
          <section>
            <h3 className="mb-2 flex items-center gap-1 text-xs font-semibold uppercase tracking-wider text-muted-fg">
              <ArrowDown size={12} /> Caused
            </h3>
            {detail.data?.children.length ? (
              detail.data.children.map((c) => <EventLine key={c.id} e={c} onPick={pick} />)
            ) : (
              <p className="text-sm text-muted-fg">—</p>
            )}
          </section>
          {e.correlation_id ? (
            <Link
              to="/incidents/$incidentId"
              params={{ incidentId: e.correlation_id }}
              className="inline-block text-sm text-sky-400 hover:underline"
            >
              Open the incident timeline →
            </Link>
          ) : null}
        </div>
      )}
    </Sheet>
  );
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
