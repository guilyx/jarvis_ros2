import { ChevronDown, ShieldCheck, ShieldOff, ShieldQuestion } from "lucide-react";
import { useState } from "react";
import { useSetMode } from "@/api/queries";
import type { RecoveryMode } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label } from "@/components/ui/input";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/components/ui/dropdown";
import { useLive } from "@/live/context";
import { useCan } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { STATE_STYLE } from "@/lib/severity";
import { ErrorNote } from "./domain";

const MODES: { mode: Exclude<RecoveryMode, "UNKNOWN">; text: string; icon: typeof ShieldCheck }[] = [
  { mode: "ENABLED", text: "Rules and operators run plans", icon: ShieldCheck },
  { mode: "DRY_RUN", text: "Decide everything, move nothing", icon: ShieldQuestion },
  { mode: "DISABLED", text: "Nothing runs (kill switch)", icon: ShieldOff },
];

/** The recovery kill switch, in the header: anyone sees it, admins change it (with a reason). */
export function ModeMenu() {
  const mode = useLive((s) => s.executions?.mode ?? "UNKNOWN");
  const can = useCan();
  const setMode = useSetMode();
  const [target, setTarget] = useState<Exclude<RecoveryMode, "UNKNOWN"> | null>(null);
  const [reason, setReason] = useState("");

  const label = (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
        STATE_STYLE[mode] ?? "ring-border",
      )}
    >
      {mode.replace("_", " ")}
    </span>
  );

  return (
    <>
      <Menu>
        <MenuTrigger asChild disabled={!can("admin")}>
          <button
            type="button"
            className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm hover:bg-muted disabled:cursor-default disabled:hover:bg-transparent cursor-pointer"
            aria-label="Recovery mode"
          >
            <span className="text-muted-fg">Recovery</span> {label}
            {can("admin") ? <ChevronDown size={14} className="text-muted-fg" /> : null}
          </button>
        </MenuTrigger>
        <MenuContent>
          <MenuLabel>Recovery mode</MenuLabel>
          {MODES.map(({ mode: m, text, icon: Icon }) => (
            <MenuItem key={m} disabled={m === mode} onSelect={() => setTarget(m)}>
              <Icon size={14} />
              <div>
                <div className="font-medium">{m.replace("_", " ")}</div>
                <div className="text-xs text-muted-fg">{text}</div>
              </div>
            </MenuItem>
          ))}
        </MenuContent>
      </Menu>
      <Dialog open={target !== null} onOpenChange={(o) => !o && setTarget(null)}>
        <DialogContent
          title={`Set recovery to ${target?.replace("_", " ")}`}
          description={
            target === "ENABLED"
              ? "Rules and operators run plans again."
              : "Plans already running finish; new ones do not move anything."
          }
        >
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!target) return;
              void setMode.mutateAsync({ mode: target, reason }).then(() => {
                setTarget(null);
                setReason("");
              });
            }}
          >
            <label className="block">
              <Label hint="(required, recorded with your name)">Reason</Label>
              <Input
                autoFocus
                className="w-full"
                aria-label="Reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <ErrorNote error={setMode.error} />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setTarget(null)}>
                Cancel
              </Button>
              <Button
                type="submit"
                variant={target === "ENABLED" ? "default" : "danger"}
                disabled={!reason.trim() || setMode.isPending}
              >
                Set {target?.replace("_", " ")}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
