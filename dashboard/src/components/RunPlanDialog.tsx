import { useNavigate } from "@tanstack/react-router";
import { Play } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { usePlans, useRecover } from "@/api/queries";
import type { Plan } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { ErrorNote } from "./domain";

function defaults(plan: Plan | undefined, preset: Record<string, string>) {
  const out: Record<string, string> = {};
  for (const [name, spec] of Object.entries(plan?.params ?? {})) {
    out[name] = preset[name] ?? (spec.default !== undefined ? String(spec.default) : "");
  }
  return out;
}

/** Run a plan as an operator: the same gates as a rule (mode, requester, preconditions, locks, ...). */
export function RunPlanDialog({
  trigger,
  planId,
  preset = {},
  correlationId,
}: {
  trigger?: ReactNode;
  planId?: string;
  preset?: Record<string, string>;
  correlationId?: string;
}) {
  const plans = usePlans();
  const recover = useRecover();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const operatorPlans = useMemo(
    () => (plans.data?.plans ?? []).filter((p) => p.allowed_requesters.includes("OPERATOR")),
    [plans.data],
  );
  const [selected, setSelected] = useState(planId ?? "");
  const plan = operatorPlans.find((p) => p.id === (selected || operatorPlans[0]?.id));
  const [args, setArgs] = useState<Record<string, string>>({});
  const [dryRun, setDryRun] = useState(false);
  const [rationale, setRationale] = useState("");

  const reset = (p: Plan | undefined) => {
    setArgs(defaults(p, preset));
  };

  const missing = Object.entries(plan?.params ?? {}).filter(([n, s]) => s.required && !args[n]?.trim());

  const submit = async () => {
    if (!plan) return;
    const filled = Object.fromEntries(Object.entries(args).filter(([, v]) => v.trim() !== ""));
    const outcome = await recover.mutateAsync({
      plan_id: plan.id,
      args: filled,
      dry_run: dryRun,
      rationale,
      correlation_id: correlationId,
    });
    setOpen(false);
    const id = outcome.data.execution_id;
    if (typeof id === "string" && id)
      void navigate({ to: "/recovery/$executionId", params: { executionId: id } });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) {
          recover.reset();
          reset(plan);
        }
      }}
    >
      <span
        className="contents"
        onClick={() => {
          setOpen(true);
          recover.reset();
          reset(plan);
        }}
      >
        {trigger ?? (
          <Button variant="default">
            <Play size={14} /> Run plan…
          </Button>
        )}
      </span>
      <DialogContent
        title="Run a recovery plan"
        description="As an operator, through the same gates as a rule."
      >
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void submit().catch(() => {});
          }}
        >
          <label className="block">
            <Label>Plan</Label>
            <Select
              className="w-full"
              aria-label="Plan"
              value={plan?.id ?? ""}
              onChange={(e) => {
                setSelected(e.target.value);
                reset(operatorPlans.find((p) => p.id === e.target.value));
              }}
            >
              {operatorPlans.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id}
                </option>
              ))}
            </Select>
            {plan ? <p className="mt-1 text-xs text-muted-fg">{plan.description}</p> : null}
          </label>
          {Object.entries(plan?.params ?? {}).map(([name, spec]) => (
            <label key={name} className="block">
              <Label hint={spec.description}>
                {name}
                {spec.required ? " *" : ""}
              </Label>
              {spec.one_of?.length ? (
                <Select
                  className="w-full"
                  aria-label={name}
                  value={args[name] ?? ""}
                  onChange={(e) => setArgs({ ...args, [name]: e.target.value })}
                >
                  <option value="" />
                  {spec.one_of.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </Select>
              ) : (
                <Input
                  className="w-full font-mono"
                  aria-label={name}
                  placeholder={spec.pattern ? `pattern ${spec.pattern}` : spec.type}
                  value={args[name] ?? ""}
                  onChange={(e) => setArgs({ ...args, [name]: e.target.value })}
                />
              )}
            </label>
          ))}
          <label className="block">
            <Label hint="(recorded with your name)">Rationale</Label>
            <Input
              className="w-full"
              aria-label="Rationale"
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={dryRun} onCheckedChange={setDryRun} label="Dry run" />
            Dry run <span className="text-muted-fg">(every gate, no step moves anything)</span>
          </label>
          {plan?.requires_approval.includes("OPERATOR") ? (
            <p className="text-xs text-amber-400">This plan waits for a second operator's approval.</p>
          ) : null}
          <ErrorNote error={recover.error} />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="default"
              disabled={!plan || missing.length > 0 || recover.isPending}
            >
              <Play size={14} /> Run
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
