// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// What the panel shows, from Jarvis's messages as Foxglove decodes them (jarvis_interfaces):
// /jarvis/faults (FaultArray, latched: the open faults) and /jarvis/event_batches (EventBatch: the
// stream, as it is recorded). No React here: tested on its own.
//
// Two event logs: live, the events as they come (EventLog); in a recording, every event of the
// file, shown up to the playhead (History).

export type Time = { sec: number; nsec?: number; nanosec?: number };

export const SEVERITIES: Record<number, string> = {
  10: "DEBUG",
  20: "INFO",
  30: "WARN",
  40: "ERROR",
  50: "CRITICAL",
};
export const KINDS: Record<number, string> = {
  1: "LOG",
  2: "STATE",
  3: "FAULT_RAISE",
  4: "FAULT_CLEAR",
  5: "FAULT_STATE",
  6: "RULE_MATCH",
  7: "RECOVERY",
  8: "INSIGHT",
  9: "OPERATOR",
  10: "SYSTEM",
};
const STATES: Record<number, string> = { 1: "PENDING", 2: "ACTIVE", 3: "LATCHED", 4: "CLEARED" };
const OPEN = new Set([1, 2, 3]);

type Source = { robot?: string; node?: string; component?: string };

export type FaultMsg = {
  fault_id: string;
  code: string;
  source?: Source;
  state: number;
  severity: number;
  occurrences?: number;
  first_seen?: Time;
  latch?: boolean;
  acknowledged?: boolean;
  muted_by?: string;
  incident_id?: string;
  message?: string;
};
export type FaultArrayMsg = { faults?: FaultMsg[] };

export type EventMsg = {
  id: string;
  seq?: number | bigint;
  stamp?: Time;
  source?: Source;
  kind: number;
  code: string;
  severity: number;
  message?: string;
};
export type EventBatchMsg = { events?: EventMsg[] };

export type FaultRow = {
  id: string;
  code: string;
  severity: string;
  state: string;
  component: string;
  message: string;
  since: Time | undefined;
  occurrences: number;
  incident: string;
  acknowledged: boolean;
};

export type EventRow = {
  id: string;
  seq: string;
  stamp: Time | undefined;
  /** When the batch carrying it was logged: the recording's clock, where a seek goes. */
  at: Time | undefined;
  severity: string;
  kind: string;
  code: string;
  robot: string;
  component: string;
  message: string;
};

export function severityName(level: number): string {
  return SEVERITIES[level] ?? "INFO";
}

export function rank(severity: string): number {
  return Number(Object.keys(SEVERITIES).find((k) => SEVERITIES[Number(k)] === severity) ?? 20);
}

export function toSeconds(t: Time | undefined): number {
  return t ? t.sec + (t.nanosec ?? t.nsec ?? 0) / 1e9 : 0;
}

/** The open faults, worst first (then latched first, then the oldest): muted symptoms left out. */
export function openFaults(msg: FaultArrayMsg | undefined): FaultRow[] {
  const rows = (msg?.faults ?? [])
    .filter((f) => OPEN.has(f.state) && (f.muted_by ?? "") === "")
    .map((f) => ({
      id: f.fault_id,
      code: f.code,
      severity: severityName(f.severity),
      state: STATES[f.state] ?? String(f.state),
      component: where(f.source),
      message: f.message ?? "",
      since: f.first_seen,
      occurrences: f.occurrences ?? 1,
      incident: f.incident_id ?? "",
      acknowledged: f.acknowledged ?? false,
    }));
  const latched = (r: FaultRow) => (r.state === "LATCHED" ? 1 : 0);
  return rows.sort((a, b) => {
    const bySeverity = rank(b.severity) - rank(a.severity);
    if (bySeverity !== 0) {
      return bySeverity;
    }
    const byLatch = latched(b) - latched(a);
    return byLatch !== 0 ? byLatch : toSeconds(a.since) - toSeconds(b.since);
  });
}

/** What a fault or an event is about: its component, else the node that reported it. */
function where(source: Source | undefined): string {
  const component = source?.component ?? "";
  return component !== "" ? component : (source?.node ?? "");
}

/** Open faults per severity, for the header. */
export function counts(rows: FaultRow[]): Record<string, number> {
  const out: Record<string, number> = { CRITICAL: 0, ERROR: 0, WARN: 0 };
  for (const r of rows) {
    if (r.severity in out) {
      out[r.severity] = (out[r.severity] ?? 0) + 1;
    }
  }
  return out;
}

export function eventRow(e: EventMsg, at?: Time): EventRow {
  return {
    id: e.id,
    seq: e.seq == undefined ? "" : String(e.seq),
    stamp: e.stamp,
    at: at ?? e.stamp,
    severity: severityName(e.severity),
    kind: KINDS[e.kind] ?? String(e.kind),
    code: e.code,
    robot: e.source?.robot ?? "",
    component: where(e.source),
    message: e.message ?? "",
  };
}

/** The event log: the last `limit` events at or above `minSeverity`, oldest first, each once
 * (a live stream and a recording can both deliver an event twice: after a seek, say). */
export class EventLog {
  #rows: EventRow[] = [];
  #seen = new Set<string>();

  #limit: number;
  #minSeverity: string;

  constructor(limit = 500, minSeverity = "INFO") {
    this.#limit = limit;
    this.#minSeverity = minSeverity;
  }

  configure(limit: number, minSeverity: string): void {
    this.#limit = limit;
    this.#minSeverity = minSeverity;
    this.#rows = this.#rows.filter((r) => rank(r.severity) >= rank(minSeverity)).slice(-limit);
  }

  add(batch: EventBatchMsg | undefined, at?: Time): boolean {
    let added = false;
    for (const e of batch?.events ?? []) {
      if (this.#seen.has(e.id)) {
        continue;
      }
      const row = eventRow(e, at);
      if (rank(row.severity) < rank(this.#minSeverity)) {
        continue;
      }
      this.#seen.add(e.id);
      this.#rows.push(row);
      added = true;
    }
    if (this.#rows.length > this.#limit) {
      for (const r of this.#rows.splice(0, this.#rows.length - this.#limit)) {
        this.#seen.delete(r.id);
      }
    }
    return added;
  }

  /** After a seek: what was shown may be from another time. */
  clear(): void {
    this.#rows = [];
    this.#seen.clear();
  }

  get all(): readonly EventRow[] {
    return this.#rows;
  }
}

/** A recording's events, all of them, in log order: what had happened by any time of the file. */
export class History {
  #rows: EventRow[] = [];
  #seen = new Set<string>();

  add(batch: EventBatchMsg | undefined, at: Time): void {
    for (const e of batch?.events ?? []) {
      if (!this.#seen.has(e.id)) {
        this.#seen.add(e.id);
        this.#rows.push(eventRow(e, at));
      }
    }
  }

  get size(): number {
    return this.#rows.length;
  }

  /** The last `limit` events at or above `minSeverity` logged by `now`, oldest first. */
  until(now: Time | undefined, minSeverity: string, limit: number): EventRow[] {
    const t = now ? toSeconds(now) : Infinity;
    let lo = 0;
    let hi = this.#rows.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (toSeconds(this.#rows[mid]?.at) <= t) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    const out: EventRow[] = [];
    const min = rank(minSeverity);
    for (let i = lo - 1; i >= 0 && out.length < limit; i--) {
      const row = this.#rows[i];
      if (row && rank(row.severity) >= min) {
        out.push(row);
      }
    }
    return out.reverse();
  }
}

/** The robot, from what Jarvis said last. */
export function robotOf(msg: FaultArrayMsg | undefined, events: readonly EventRow[]): string {
  return (
    msg?.faults?.find((f) => f.source?.robot)?.source?.robot ??
    events[events.length - 1]?.robot ??
    ""
  );
}

export function clock(t: Time | undefined): string {
  if (!t) {
    return "";
  }
  const d = new Date(toSeconds(t) * 1000);
  return d.toISOString().slice(11, 23);
}

export function ago(t: Time | undefined, now: Time | undefined): string {
  if (!t || !now) {
    return "";
  }
  const s = Math.max(0, toSeconds(now) - toSeconds(t));
  if (s < 90) {
    return `${s.toFixed(0)} s`;
  }
  if (s < 5400) {
    return `${(s / 60).toFixed(0)} min`;
  }
  return `${(s / 3600).toFixed(1)} h`;
}
