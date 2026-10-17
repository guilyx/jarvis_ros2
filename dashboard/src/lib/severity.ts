import type { Severity } from "@/api/types";

/** Severity colors, the same in every view, always paired with an icon and the name. */
export const SEVERITY_STYLE: Record<Severity, { text: string; bg: string; chart: string; short: string }> = {
  DEBUG: { text: "text-slate-400", bg: "bg-slate-500/15 ring-slate-500/30", chart: "#64748b", short: "DBG" },
  INFO: { text: "text-sky-400", bg: "bg-sky-500/15 ring-sky-500/30", chart: "#38bdf8", short: "INFO" },
  WARN: { text: "text-amber-400", bg: "bg-amber-500/15 ring-amber-500/30", chart: "#f59e0b", short: "WARN" },
  ERROR: {
    text: "text-orange-500",
    bg: "bg-orange-500/15 ring-orange-500/30",
    chart: "#f97316",
    short: "ERR",
  },
  CRITICAL: { text: "text-red-500", bg: "bg-red-500/15 ring-red-500/40", chart: "#ef4444", short: "CRIT" },
};

export const STATE_STYLE: Record<string, string> = {
  // faults
  PENDING: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  ACTIVE: "bg-orange-500/15 text-orange-400 ring-orange-500/30",
  LATCHED: "bg-red-500/15 text-red-400 ring-red-500/40",
  CLEARED: "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30",
  // incidents
  OPEN: "bg-orange-500/15 text-orange-400 ring-orange-500/30",
  RECOVERING: "bg-sky-500/15 text-sky-400 ring-sky-500/30",
  ESCALATED: "bg-red-500/15 text-red-400 ring-red-500/40",
  RESOLVED: "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30",
  CLOSED: "bg-slate-500/15 text-slate-400 ring-slate-500/30",
  // executions
  PENDING_APPROVAL: "bg-amber-500/15 text-amber-400 ring-amber-500/30",
  QUEUED: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  RUNNING: "bg-sky-500/15 text-sky-400 ring-sky-500/30",
  RETRYING: "bg-amber-500/15 text-amber-400 ring-amber-500/30",
  SUCCEEDED: "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30",
  FAILED: "bg-red-500/15 text-red-400 ring-red-500/40",
  ABORTED: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  REJECTED: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  EXPIRED: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  SKIPPED: "bg-slate-500/15 text-slate-400 ring-slate-500/30",
  // recovery mode, lifecycle
  ENABLED: "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30",
  DRY_RUN: "bg-amber-500/15 text-amber-400 ring-amber-500/30",
  DISABLED: "bg-red-500/15 text-red-400 ring-red-500/40",
  active: "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30",
  inactive: "bg-amber-500/15 text-amber-400 ring-amber-500/30",
  unconfigured: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  finalized: "bg-red-500/15 text-red-400 ring-red-500/40",
};
