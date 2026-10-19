import { Wrench } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useEndMaintenance, useStartMaintenance } from "@/api/queries";
import type { Maintenance } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { Tip } from "@/components/ui/tooltip";
import { useLive } from "@/live/context";
import { useCan } from "@/lib/auth";
import { ago, clock, dateTime, toMs } from "@/lib/format";
import { ErrorNote } from "./domain";

const DURATIONS = [
  { minutes: 15, text: "15 min" },
  { minutes: 30, text: "30 min" },
  { minutes: 60, text: "1 h" },
  { minutes: 120, text: "2 h" },
  { minutes: 240, text: "4 h" },
  { minutes: 480, text: "8 h" },
  { minutes: 1440, text: "24 h" },
];

/** The components under maintenance now, from the recovery node's live snapshot. */
export function useMaintenance(): Maintenance[] {
  const all = useLive((s) => s.executions?.maintenance);
  const now = Date.now();
  return (all ?? []).filter((m) => (toMs(m.until) ?? 0) > now);
}

export function useUnderMaintenance(component: string | undefined): Maintenance | undefined {
  const windows = useMaintenance();
  return component ? windows.find((m) => m.component === component) : undefined;
}

/**
 * Puts a component under maintenance (again: renews it). Rules and the agent keep off it until
 * then; operators still act, and notifications about it are silenced (dropped, not queued).
 */
export function MaintenanceDialog({ trigger, component }: { trigger: ReactNode; component?: string }) {
  const start = useStartMaintenance();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(component ?? "");
  const [minutes, setMinutes] = useState(30);
  const [reason, setReason] = useState("");

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) start.reset();
      }}
    >
      <span onClick={() => setOpen(true)} className="contents">
        {trigger}
      </span>
      <DialogContent
        title={component ? `Maintenance on ${component}` : "Maintenance"}
        description="Rules and the agent keep off the component until then; operators still act. Notifications about it are silenced."
      >
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void start.mutateAsync({ component: name.trim(), minutes, reason: reason.trim() }).then(() => {
              setOpen(false);
              setReason("");
              if (!component) setName("");
            });
          }}
        >
          <div className="flex gap-3">
            <label className="block flex-1">
              <Label hint="(source.component on its events)">Component</Label>
              <Input
                autoFocus={!component}
                readOnly={!!component}
                className="w-full"
                aria-label="Component"
                placeholder="lidar"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="block">
              <Label>For</Label>
              <Select aria-label="For" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
                {DURATIONS.map((d) => (
                  <option key={d.minutes} value={d.minutes}>
                    {d.text}
                  </option>
                ))}
              </Select>
            </label>
          </div>
          <label className="block">
            <Label hint="(recorded with your name)">Reason</Label>
            <Input
              autoFocus={!!component}
              className="w-full"
              aria-label="Reason"
              placeholder="recalibrating"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <ErrorNote error={start.error} />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || start.isPending}>
              <Wrench size={14} /> Start maintenance
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** In the header: operators put any component under maintenance. */
export function MaintenanceButton() {
  const can = useCan();
  if (!can("operator")) return null;
  return (
    <MaintenanceDialog
      trigger={
        <Tip content="Keep rules and the agent off a component for a while">
          <button
            type="button"
            className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-muted-fg hover:bg-muted hover:text-fg cursor-pointer"
            aria-label="Start maintenance"
          >
            <Wrench size={14} /> Maintenance
          </button>
        </Tip>
      }
    />
  );
}

/** Under the header, on every page, while anything is under maintenance. */
export function MaintenanceBanner() {
  const windows = useMaintenance();
  const can = useCan();
  const end = useEndMaintenance();
  if (!windows.length) return null;
  return (
    <div
      className="shrink-0 space-y-1 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm"
      data-testid="maintenance"
    >
      {windows.map((m) => (
        <div key={m.component} className="flex items-center gap-2">
          <Tip content="Rules and the agent keep off it; operators still act. Notifications about it are silenced.">
            <Wrench size={14} className="shrink-0 text-amber-500" />
          </Tip>
          <span className="min-w-0 flex-1 truncate">
            <span className="font-semibold">{m.component}</span> under maintenance until{" "}
            <Tip content={dateTime(m.until)}>
              <span className="font-medium tabular-nums">{clock(m.until, false)}</span>
            </Tip>
            <span className="text-muted-fg">
              {" "}
              ({ago(m.until)}) · {m.operator || "operator"}
              {m.reason ? `: ${m.reason}` : ""}
            </span>
            <span className="hidden text-muted-fg 2xl:inline">
              {" "}
              · rules and the agent keep off it, its notifications are silenced
            </span>
          </span>
          {can("operator") ? (
            <Button
              size="sm"
              variant="outline"
              aria-label={`End maintenance of ${m.component}`}
              disabled={end.isPending}
              onClick={() => end.mutate(m.component)}
            >
              End
            </Button>
          ) : null}
        </div>
      ))}
      <ErrorNote error={end.error} />
    </div>
  );
}

/** On a fault: its component is under maintenance (a badge), or an operator may put it there. */
export function FaultMaintenance({ component }: { component: string }) {
  const m = useUnderMaintenance(component);
  const can = useCan();
  if (m)
    return (
      <Tip content={`Until ${clock(m.until, false)} (${m.operator || "operator"}): rules keep off it`}>
        <span
          className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium text-amber-500 ring-1 ring-inset ring-amber-500/40"
          aria-label={`${component} under maintenance`}
        >
          <Wrench size={12} /> maintenance
        </span>
      </Tip>
    );
  if (!can("operator")) return null;
  return (
    <MaintenanceDialog
      component={component}
      trigger={
        <Tip content={`Keep rules and the agent off ${component} for a while`}>
          <Button size="sm" variant="ghost" aria-label={`Maintenance on ${component}`}>
            <Wrench size={12} />
          </Button>
        </Tip>
      }
    />
  );
}
