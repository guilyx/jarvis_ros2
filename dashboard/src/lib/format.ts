import { SEVERITY_LEVEL, type Severity } from "@/api/types";

/** Nanosecond ISO stamps ("...T09:14:03.120000000Z") parse as milliseconds. */
export function toMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const m = /^(.*T\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:?\d\d)?$/.exec(iso);
  if (!m) {
    const t = Date.parse(iso);
    return Number.isNaN(t) ? null : t;
  }
  const frac = m[2] ? m[2].slice(0, 4) : "";
  const t = Date.parse(`${m[1]}${frac}${m[3] ?? "Z"}`);
  return Number.isNaN(t) ? null : t;
}

const pad = (n: number, w = 2) => String(n).padStart(w, "0");

/** 09:14:03.120 (local time). */
export function clock(iso: string | null | undefined, millis = true): string {
  const t = toMs(iso);
  if (t === null) return "—";
  const d = new Date(t);
  const base = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return millis ? `${base}.${pad(d.getMilliseconds(), 3)}` : base;
}

/** 2026-10-17 09:14:03 (local time). */
export function dateTime(iso: string | null | undefined): string {
  const t = toMs(iso);
  if (t === null) return "—";
  const d = new Date(t);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${clock(iso, false)}`;
}

/** 4.2 s, 3 min 12 s, 2 h 5 min, 3 d 4 h. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const s = Math.abs(seconds);
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const r = Math.round(s % 60);
    return r ? `${m} min ${r} s` : `${m} min`;
  }
  if (s < 86400) {
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    return m ? `${h} h ${m} min` : `${h} h`;
  }
  const d = Math.floor(s / 86400);
  const h = Math.round((s % 86400) / 3600);
  return h ? `${d} d ${h} h` : `${d} d`;
}

export function between(
  from: string | null | undefined,
  to?: string | null,
  now = Date.now(),
): number | null {
  const a = toMs(from);
  if (a === null) return null;
  const b = to ? toMs(to) : now;
  return b === null ? null : (b - a) / 1000;
}

/** "3 min ago", "in 4 min". */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  const s = between(iso, null, now);
  if (s === null) return "—";
  return s >= 0 ? `${duration(s)} ago` : `in ${duration(-s)}`;
}

export function percent(x: number | null | undefined, digits = 0): string {
  return x === null || x === undefined ? "—" : `${(x * 100).toFixed(digits)} %`;
}

export function bytes(n: number | null | undefined): string {
  if (!n) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function severityName(level: number | null | undefined): Severity {
  const found = (Object.entries(SEVERITY_LEVEL) as [Severity, number][]).filter(
    ([, l]) => l <= (level ?? 20),
  );
  return found.length ? found[found.length - 1]![0] : "DEBUG";
}

export function atLeast(severity: string, min: string | undefined): boolean {
  if (!min) return true;
  return (SEVERITY_LEVEL[severity as Severity] ?? 20) >= (SEVERITY_LEVEL[min as Severity] ?? 0);
}

/** fnmatch-style globs (`ros.*`), as the gateway matches codes. */
export function glob(pattern: string): RegExp {
  const re = pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*")
    .replace(/\?/g, ".");
  return new RegExp(`^${re}$`);
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—";
}
