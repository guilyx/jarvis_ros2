import { Link } from "@tanstack/react-router";
import { ChevronDown, ChevronRight, Play, Send, Sparkles } from "lucide-react";
import { useState } from "react";
import { useRecovery } from "@/api/queries";
import { analystLabel, insightConfidence, insightOf, type JarvisEvent } from "@/api/types";
import { DecideButtons } from "@/components/DecideButtons";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { useCan } from "@/lib/auth";
import { ago } from "@/lib/format";
import { IncidentLink, SeverityBadge } from "./domain";
import { RunPlanDialog } from "./RunPlanDialog";

export function ConfidenceBar({ value }: { value: number }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-fg">
      <span className="h-1.5 w-16 rounded bg-muted">
        <span className="block h-1.5 rounded bg-violet-400" style={{ width: `${value * 100}%` }} />
      </span>
      {value.toFixed(2)}
    </span>
  );
}

/** A plan the agent sent to the recovery gates: where it stands, and Approve / Reject while it waits. */
function Proposal({ id }: { id: string }) {
  const can = useCan();
  const { data } = useRecovery(id);
  const x = data?.execution ?? null;
  const state = x?.state ?? data?.history?.outcome ?? data?.history?.last_phase ?? "";
  return (
    <div
      data-testid="insight-proposal"
      className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-violet-500/5 px-3 py-2 text-sm ring-1 ring-violet-500/20"
    >
      <div className="min-w-0">
        <Send size={12} className="mr-1.5 inline text-violet-400" />
        Proposed{" "}
        <Link
          to="/recovery/$executionId"
          params={{ executionId: id }}
          className="font-medium hover:underline"
        >
          {x?.plan_id ?? data?.history?.plan ?? id}
        </Link>
        {state ? <span className="text-muted-fg"> · {state.toLowerCase().replace("_", " ")}</span> : null}
      </div>
      {x?.state === "PENDING_APPROVAL" && can("operator") ? (
        <DecideButtons x={x} description={x.rationale || undefined} />
      ) : null}
    </div>
  );
}

/** One insight: what the agent thinks happened, why, and what to do. Advice has a Run button; a
 * proposal went to the recovery gates and may wait for an operator's approval. */
export function InsightCard({
  event,
  onEvidence,
}: {
  event: JarvisEvent;
  onEvidence?: (id: string) => void;
}) {
  const insight = insightOf(event);
  const can = useCan();
  const [open, setOpen] = useState(false);
  if (!insight) return null;
  return (
    <Card data-testid="insight-card">
      <CardBody className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Sparkles size={14} className="shrink-0 text-violet-400" />
              <SeverityBadge severity={insight.assessment} short />
              <h3 className="font-medium">{insight.title}</h3>
            </div>
            <div className="mt-1 text-xs text-muted-fg">
              {ago(event.stamp)} · {insight.trigger.type.toLowerCase().replace("_", " ")} · incident{" "}
              <IncidentLink id={event.correlation_id || insight.window.correlation_id} /> ·{" "}
              {analystLabel(insight)}
            </div>
          </div>
          <ConfidenceBar value={insightConfidence(insight)} />
        </div>
        {insight.hypotheses.length ? (
          <ol className="list-decimal space-y-1 pl-5 text-sm">
            {insight.hypotheses.map((h, i) => (
              <li key={i}>
                {h.cause}{" "}
                <span className="text-xs text-muted-fg">
                  ({h.confidence.toFixed(2)}
                  {h.evidence.length ? ", " : ""}
                  {h.evidence.map((id, n) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => onEvidence?.(id)}
                      className="mx-0.5 font-mono text-sky-400 hover:underline cursor-pointer"
                      title={id}
                    >
                      ev{n + 1}
                    </button>
                  ))}
                  ){h.flagged ? " · unsupported" : ""}
                </span>
              </li>
            ))}
          </ol>
        ) : null}
        {insight.recommendations.length ? (
          <ul className="space-y-1.5 text-sm">
            {insight.recommendations.map((r, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2">
                <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-fg">
                  {r.kind}
                </span>
                <span>{r.text}</span>
                {r.kind === "RECOVER" && r.plan_id && can("operator") ? (
                  <RunPlanDialog
                    planId={r.plan_id}
                    preset={r.args ?? {}}
                    correlationId={insight.window.correlation_id}
                    trigger={
                      <Button size="sm" variant="default" aria-label={`Run ${r.plan_id}`}>
                        <Play size={12} /> Run {r.plan_id}
                      </Button>
                    }
                  />
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
        {insight.proposals?.length ? (
          <div className="space-y-1.5">
            {insight.proposals.map((id) => (
              <Proposal key={id} id={id} />
            ))}
          </div>
        ) : null}
        {insight.facts?.length || insight.flags?.length ? (
          <div>
            <button
              type="button"
              onClick={() => setOpen(!open)}
              className="flex items-center gap-1 text-xs text-muted-fg hover:text-fg cursor-pointer"
            >
              {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              {insight.facts?.length ?? 0} facts
              {insight.flags?.length ? `, ${insight.flags.length} harness notes` : ""}
            </button>
            {open ? (
              <ul className="mt-2 space-y-0.5 font-mono text-xs text-muted-fg">
                {insight.facts?.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
                {insight.flags?.map((f, i) => (
                  <li key={`flag${i}`} className="text-amber-400">
                    harness: {f}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}
