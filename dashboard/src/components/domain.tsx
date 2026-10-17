import { Link } from "@tanstack/react-router";
import { AlertOctagon, AlertTriangle, Bug, Info, OctagonAlert } from "lucide-react";
import type { ReactNode } from "react";
import type { Severity } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { shortId } from "@/lib/format";
import { SEVERITY_STYLE, STATE_STYLE } from "@/lib/severity";

const SEVERITY_ICON: Record<Severity, typeof Info> = {
  DEBUG: Bug,
  INFO: Info,
  WARN: AlertTriangle,
  ERROR: OctagonAlert,
  CRITICAL: AlertOctagon,
};

export function SeverityBadge({ severity, short = false }: { severity: string; short?: boolean }) {
  const sev = (severity in SEVERITY_STYLE ? severity : "INFO") as Severity;
  const s = SEVERITY_STYLE[sev];
  const Icon = SEVERITY_ICON[sev];
  return (
    <Badge className={cn(s.bg, s.text)} title={sev}>
      <Icon size={11} aria-hidden />
      {short ? s.short : sev}
    </Badge>
  );
}

export function StateBadge({ state, className }: { state: string | null | undefined; className?: string }) {
  if (!state) return <span className="text-muted-fg">—</span>;
  return (
    <Badge className={cn(STATE_STYLE[state] ?? "bg-muted text-fg ring-border", className)}>
      {state.replace("_", " ")}
    </Badge>
  );
}

export function Empty({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("py-10 text-center text-sm text-muted-fg", className)}>{children}</div>;
}

export function Loading() {
  return <Empty>Loading…</Empty>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-400 ring-1 ring-red-500/30">
      {error instanceof Error ? error.message : String(error)}
    </div>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-xs", className)}>{children}</span>;
}

export function IncidentLink({ id }: { id: string | null | undefined }) {
  if (!id) return <span className="text-muted-fg">—</span>;
  return (
    <Link
      to="/incidents/$incidentId"
      params={{ incidentId: id }}
      className="font-mono text-xs text-sky-400 hover:underline"
    >
      {shortId(id)}
    </Link>
  );
}

export function PageHeader({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-lg font-semibold">{title}</h1>
      {children ? <div className="flex flex-wrap items-center gap-2">{children}</div> : null}
    </div>
  );
}

export function KeyValues({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-sm">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted-fg">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Json({ value }: { value: unknown }) {
  return (
    <pre className="max-h-96 overflow-auto rounded-md bg-bg p-3 font-mono text-xs ring-1 ring-border">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full text-left text-sm">{children}</table>
    </div>
  );
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th
      className={cn(
        "border-b border-border px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-muted-fg",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({ children, className }: { children?: ReactNode; className?: string }) {
  return <td className={cn("border-b border-border/60 px-3 py-2 align-middle", className)}>{children}</td>;
}
