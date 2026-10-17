import { useNavigate, useSearch } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Download, Pause, Play } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchEvents, useEvents, type EventFilter } from "@/api/queries";
import { KINDS, SEVERITIES, type JarvisEvent } from "@/api/types";
import { ErrorNote, PageHeader, SeverityBadge } from "@/components/domain";
import { EventSheet } from "@/components/EventSheet";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { useLive } from "@/live/context";
import { cn } from "@/lib/cn";
import { atLeast, clock, glob } from "@/lib/format";

export interface EventsSearch {
  kind?: string;
  min_severity?: string;
  code?: string;
  component?: string;
  q?: string;
  incident?: string;
  event?: string;
}

export function matches(e: JarvisEvent, f: EventFilter): boolean {
  if (f.kind?.length && !f.kind.includes(e.kind)) return false;
  if (!atLeast(e.severity, f.min_severity)) return false;
  if (f.code && !glob(f.code).test(e.code)) return false;
  if (f.component && e.source.component !== f.component) return false;
  if (f.correlation_id && e.correlation_id !== f.correlation_id && e.attrs?.incident_id !== f.correlation_id)
    return false;
  if (f.q) {
    const q = f.q.toLowerCase();
    if (!(e.message ?? "").toLowerCase().includes(q) && !e.code.toLowerCase().includes(q) && e.id !== f.q)
      return false;
  }
  return true;
}

/** History (newest first) plus live events above it, deduplicated by seq. */
export function merge(history: JarvisEvent[], live: JarvisEvent[], older: JarvisEvent[], f: EventFilter) {
  const seen = new Set<string>();
  const out: JarvisEvent[] = [];
  const top = history[0] ? BigInt(history[0].seq) : -1n;
  for (let i = live.length - 1; i >= 0; i--) {
    const e = live[i]!;
    if (BigInt(e.seq) > top && matches(e, f) && !seen.has(e.seq)) {
      seen.add(e.seq);
      out.push(e);
    }
  }
  for (const e of [...history, ...older]) {
    if (!seen.has(e.seq)) {
      seen.add(e.seq);
      out.push(e);
    }
  }
  return out;
}

function download(name: string, rows: JarvisEvent[]) {
  const blob = new Blob([rows.map((r) => JSON.stringify(r)).join("\n") + "\n"], {
    type: "application/x-ndjson",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

const COLUMNS =
  "grid grid-cols-[9.5rem_6.5rem_7.5rem_4.5rem_minmax(8rem,1.2fr)_7rem_minmax(10rem,2fr)] gap-3";

export function Events() {
  const search = useSearch({ from: "/events" });
  const navigate = useNavigate({ from: "/events" });
  const filter = useMemo<EventFilter>(
    () => ({
      kind: search.kind ? search.kind.split(",") : undefined,
      min_severity: search.min_severity,
      code: search.code,
      component: search.component,
      q: search.q,
      correlation_id: search.incident,
    }),
    [search.kind, search.min_severity, search.code, search.component, search.q, search.incident],
  );
  const history = useEvents(filter);
  const live = useLive((s) => s.events);
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState<JarvisEvent[]>([]);
  const [older, setOlder] = useState<JarvisEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);

  useEffect(() => {
    setOlder([]);
    setCursor(history.data?.next_before_seq ?? null);
  }, [history.data]);

  const all = useMemo(
    () => merge(history.data?.events ?? [], live, older, filter),
    [history.data, live, older, filter],
  );
  // Paused: the list stops moving under the cursor; new rows wait above.
  useEffect(() => {
    if (!paused) setFrozen(all);
  }, [all, paused]);
  const rows = paused ? frozen : all;
  const waiting = paused ? all.length - frozen.length : 0;

  const scroller = useRef<HTMLDivElement>(null);
  const virtual = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => 34,
    overscan: 20,
  });

  const set = (patch: Partial<EventsSearch>) =>
    void navigate({ search: (s: EventsSearch) => ({ ...s, ...patch }), replace: true });

  const loadOlder = async () => {
    if (!cursor) return;
    setLoadingOlder(true);
    try {
      const page = await fetchEvents(filter, cursor);
      setOlder((o) => [...o, ...page.events]);
      setCursor(page.next_before_seq);
    } finally {
      setLoadingOlder(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Events">
        <Button onClick={() => setPaused(!paused)} aria-pressed={paused}>
          {paused ? <Play size={14} /> : <Pause size={14} />}
          {paused ? `Resume${waiting ? ` (${waiting} new)` : ""}` : "Pause"}
        </Button>
        <Button onClick={() => download("jarvis-events.ndjson", rows)}>
          <Download size={14} /> Export
        </Button>
      </PageHeader>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select
          aria-label="Kind"
          value={search.kind ?? ""}
          onChange={(e) => set({ kind: e.target.value || undefined })}
        >
          <option value="">All kinds</option>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </Select>
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
        <Input
          aria-label="Code"
          placeholder="code, e.g. ros.*"
          defaultValue={search.code ?? ""}
          onKeyDown={(e) => e.key === "Enter" && set({ code: e.currentTarget.value || undefined })}
          onBlur={(e) => set({ code: e.currentTarget.value || undefined })}
          className="w-40 font-mono"
        />
        <Input
          aria-label="Component"
          placeholder="component"
          defaultValue={search.component ?? ""}
          onKeyDown={(e) => e.key === "Enter" && set({ component: e.currentTarget.value || undefined })}
          onBlur={(e) => set({ component: e.currentTarget.value || undefined })}
          className="w-32"
        />
        <Input
          aria-label="Search"
          placeholder="search messages…"
          defaultValue={search.q ?? ""}
          onKeyDown={(e) => e.key === "Enter" && set({ q: e.currentTarget.value || undefined })}
          onBlur={(e) => set({ q: e.currentTarget.value || undefined })}
          className="w-56"
        />
        {search.incident ? (
          <Button size="sm" onClick={() => set({ incident: undefined })}>
            incident {search.incident.slice(0, 8)} ✕
          </Button>
        ) : null}
        <span className="ml-auto text-xs text-muted-fg tabular">{rows.length.toLocaleString()} events</span>
      </div>
      <ErrorNote error={history.error} />
      <Card className="flex min-h-0 flex-1 flex-col">
        <div
          className={cn(
            COLUMNS,
            "border-b border-border px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-muted-fg",
          )}
        >
          <span>seq</span>
          <span>time</span>
          <span>kind</span>
          <span>sev</span>
          <span>code</span>
          <span>component</span>
          <span>message</span>
        </div>
        <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto" data-testid="events-table">
          <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
            {virtual.getVirtualItems().map((item) => {
              const e = rows[item.index]!;
              return (
                <button
                  type="button"
                  key={e.seq}
                  data-testid="event-row"
                  onClick={() => set({ event: e.id })}
                  className={cn(
                    COLUMNS,
                    "absolute left-0 w-full cursor-pointer items-center border-b border-border/50 px-3 text-left text-sm hover:bg-muted/60",
                  )}
                  style={{ top: item.start, height: item.size }}
                >
                  <span className="truncate font-mono text-xs text-muted-fg tabular" title={e.seq}>
                    {e.seq}
                  </span>
                  <span className="font-mono text-xs tabular">{clock(e.stamp)}</span>
                  <span className="truncate text-xs">{e.kind}</span>
                  <span>
                    <SeverityBadge severity={e.severity} short />
                  </span>
                  <span className="truncate font-mono text-xs">{e.code}</span>
                  <span className="truncate text-xs text-muted-fg">{e.source.component}</span>
                  <span className="truncate">{e.message}</span>
                </button>
              );
            })}
          </div>
          {cursor ? (
            <div className="p-3 text-center">
              <Button size="sm" onClick={() => void loadOlder()} disabled={loadingOlder}>
                {loadingOlder ? "Loading…" : "Load older"}
              </Button>
            </div>
          ) : null}
        </div>
      </Card>
      <EventSheet
        id={search.event ?? null}
        onClose={() => set({ event: undefined })}
        onPick={(id) => set({ event: id })}
      />
    </div>
  );
}
