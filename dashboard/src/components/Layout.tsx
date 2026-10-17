import { Link, Outlet } from "@tanstack/react-router";
import {
  Activity,
  BarChart3,
  Bell,
  Cpu,
  Gauge,
  HeartPulse,
  ListTree,
  Moon,
  RotateCcw,
  Sparkles,
  Sun,
  TriangleAlert,
  Workflow,
} from "lucide-react";
import { useMemo } from "react";
import { useHealth } from "@/api/queries";
import { Tip } from "@/components/ui/tooltip";
import { useLive } from "@/live/context";
import type { LiveStatus } from "@/live/stream";
import { cn } from "@/lib/cn";
import { useTheme } from "@/lib/theme";
import { ModeMenu } from "./ModeMenu";
import { TokenDialog } from "./TokenDialog";

const NAV = [
  { to: "/", label: "Overview", icon: Gauge },
  { to: "/events", label: "Events", icon: ListTree },
  { to: "/faults", label: "Faults", icon: TriangleAlert },
  { to: "/incidents", label: "Incidents", icon: Activity },
  { to: "/recovery", label: "Recovery", icon: RotateCcw },
  { to: "/rules", label: "Rules", icon: Workflow },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/agent", label: "Agent", icon: Sparkles },
  { to: "/system", label: "System", icon: HeartPulse },
] as const;

const STATUS: Record<LiveStatus, { text: string; dot: string }> = {
  connecting: { text: "CONNECTING", dot: "bg-slate-400" },
  live: { text: "LIVE", dot: "bg-emerald-400 animate-pulse" },
  reconnecting: { text: "RECONNECTING", dot: "bg-amber-400 animate-pulse" },
  unauthorized: { text: "NO ACCESS", dot: "bg-red-500" },
};

function LiveIndicator() {
  const status = useLive((s) => s.status);
  const s = STATUS[status];
  return (
    <Tip
      content={
        status === "unauthorized"
          ? "The gateway refused the token: set one under Identity"
          : "The live stream"
      }
    >
      <span
        className="flex items-center gap-1.5 text-xs font-semibold tracking-wider"
        data-testid="live-status"
      >
        <span className={cn("h-2 w-2 rounded-full", s.dot)} />
        {s.text}
      </span>
    </Tip>
  );
}

function Attention() {
  const faults = useLive((s) => s.faults);
  const executions = useLive((s) => s.executions);
  const count = useMemo(() => {
    const pending = (executions?.executions ?? []).filter((x) => x.state === "PENDING_APPROVAL").length;
    const urgent = (faults ?? []).filter(
      (f) => !f.muted_by && !f.acknowledged && (f.state === "LATCHED" || f.severity === "CRITICAL"),
    ).length;
    return pending + urgent;
  }, [faults, executions]);
  return (
    <Tip content="Pending approvals, and latched or critical faults nobody acknowledged">
      <Link
        to="/"
        className="relative rounded-md p-1.5 hover:bg-muted"
        aria-label={`${count} need attention`}
      >
        <Bell size={16} />
        {count ? (
          <span className="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-red-500 px-1 text-center text-[10px] font-bold text-white">
            {count}
          </span>
        ) : null}
      </Link>
    </Tip>
  );
}

export function Layout() {
  const health = useHealth();
  const { dark, toggle } = useTheme();
  return (
    <div className="flex h-full">
      <aside className="flex w-52 shrink-0 flex-col border-r border-border bg-card">
        <div className="flex items-center gap-2 px-4 py-3.5">
          <Cpu size={18} className="text-sky-400" />
          <div className="min-w-0">
            <div className="text-sm font-bold tracking-[0.2em]">JARVIS</div>
            <div className="truncate text-xs text-muted-fg" data-testid="robot">
              {health.data?.robot || "—"}
            </div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-2 py-2">
          {NAV.map(({ to, label, icon: Icon }) => (
            <Link
              key={to}
              to={to}
              activeOptions={{ exact: to === "/" }}
              className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm text-muted-fg hover:bg-muted hover:text-fg"
              activeProps={{ className: "bg-muted !text-fg font-medium" }}
            >
              <Icon size={15} /> {label}
            </Link>
          ))}
        </nav>
        <div className="px-4 py-3 text-[11px] text-muted-fg">
          <a href="/docs" className="hover:underline">
            API docs
          </a>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-border bg-card px-4">
          <div className="flex items-center gap-4">
            <LiveIndicator />
            <ModeMenu />
          </div>
          <div className="flex items-center gap-1">
            <Attention />
            <button
              type="button"
              onClick={toggle}
              className="rounded-md p-1.5 hover:bg-muted cursor-pointer"
              aria-label={dark ? "Light theme" : "Dark theme"}
            >
              {dark ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <TokenDialog />
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-5">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
