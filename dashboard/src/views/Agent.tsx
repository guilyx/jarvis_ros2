import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Loader2, Sparkles, X } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";
import { type AskEvent, askAgent } from "@/api/ask";
import { useInsights } from "@/api/queries";
import type { Insight, JarvisEvent } from "@/api/types";
import { Empty, ErrorNote, Loading, PageHeader } from "@/components/domain";
import { EventSheet } from "@/components/EventSheet";
import { InsightCard } from "@/components/InsightCard";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { useCan } from "@/lib/auth";

export interface AgentSearch {
  incident?: string;
}

/** An insight as the event the agent published it in (the card reads that shape). */
function asEvent(insight: Insight): JarvisEvent {
  return {
    id: insight.insight_id,
    seq: "0",
    stamp: insight.created ?? new Date().toISOString(),
    source: { robot: "", node: "/jarvis/agent", component: "", host: "" },
    kind: "INSIGHT",
    code: "jarvis.insight",
    severity: "INFO",
    correlation_id: insight.window.correlation_id ?? "",
    payload: insight,
  } as unknown as JarvisEvent;
}

/** Ask the agent about an incident or the last minutes; its progress streams in as it works. */
function AskBox({ incident, onEvidence }: { incident?: string; onEvidence: (id: string) => void }) {
  const can = useCan();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState<{ stage: string; detail: string }[]>([]);
  const [answer, setAnswer] = useState<Insight | null>(null);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);

  async function ask(e: FormEvent) {
    e.preventDefault();
    if (busy || (!incident && !question.trim())) return;
    abort.current = new AbortController();
    setBusy(true);
    setSteps([]);
    setAnswer(null);
    setError("");
    const on = (ev: AskEvent) => {
      if (ev.kind === "progress") setSteps((s) => [...s, { stage: ev.stage, detail: ev.detail }]);
      else if (ev.kind === "insight") setAnswer(ev.insight);
      else setError(ev.message);
    };
    try {
      await askAgent({ incident_id: incident, question }, on, abort.current.signal);
    } catch (err) {
      if (!abort.current.signal.aborted) setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      void qc.invalidateQueries({ queryKey: ["insights"] });
    }
  }

  if (!can("operator")) {
    return (
      <Card>
        <CardBody className="flex items-center gap-2 text-sm text-muted-fg">
          <Sparkles size={16} className="text-violet-400" />
          Asking the agent needs an operator token (Settings): with a model, an answer costs money.
        </CardBody>
      </Card>
    );
  }
  return (
    <Card data-testid="ask-box">
      <CardBody className="space-y-3">
        <form onSubmit={ask} className="space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Sparkles size={16} className="text-violet-400" />
            <span className="font-medium">Ask the agent</span>
            {incident ? (
              <span className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-xs">
                about incident{" "}
                <Link
                  to="/incidents/$incidentId"
                  params={{ incidentId: incident }}
                  className="font-mono hover:underline"
                >
                  {incident.slice(0, 13)}
                </Link>
                <button
                  type="button"
                  aria-label="Not about this incident"
                  className="cursor-pointer text-muted-fg hover:text-fg"
                  onClick={() => navigate({ to: "/agent", search: {} })}
                >
                  <X size={12} />
                </button>
              </span>
            ) : (
              <span className="text-xs text-muted-fg">about the last minutes</span>
            )}
          </div>
          <Textarea
            aria-label="Question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={
              incident
                ? "What do you want to know? (optional: it explains the incident)"
                : "Why did the robot stop?"
            }
            className="font-sans text-sm"
            maxLength={2000}
          />
          <div className="flex items-center gap-2">
            <Button type="submit" variant="default" disabled={busy || (!incident && !question.trim())}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : null} Ask
            </Button>
            {busy ? (
              <Button type="button" onClick={() => abort.current?.abort()}>
                Stop waiting
              </Button>
            ) : null}
          </div>
        </form>
        {steps.length ? (
          <ol className="space-y-0.5 font-mono text-xs text-muted-fg" data-testid="ask-progress">
            {steps.map((s, i) => (
              <li key={i}>
                <span className="text-violet-400">{s.stage}</span> {s.detail}
              </li>
            ))}
          </ol>
        ) : null}
        {error ? <p className="text-sm text-red-400">{error}</p> : null}
        {answer ? <InsightCard event={asEvent(answer)} onEvidence={onEvidence} /> : null}
      </CardBody>
    </Card>
  );
}

export function Agent() {
  const insights = useInsights(50);
  const search = useSearch({ from: "/agent" });
  const [picked, setPicked] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      <PageHeader title="Agent" />
      <AskBox key={search.incident ?? ""} incident={search.incident} onEvidence={setPicked} />
      <ErrorNote error={insights.error} />
      {insights.isLoading ? (
        <Loading />
      ) : !insights.data?.insights.length ? (
        <Card>
          <Empty>
            No insights yet. The agent analyzes incidents that open at ERROR or worse and recoveries that
            fail, and writes what it finds here, with the events behind it.
          </Empty>
        </Card>
      ) : (
        <div className="grid gap-3 xl:grid-cols-2">
          {insights.data.insights.map((e) => (
            <InsightCard key={e.id} event={e} onEvidence={setPicked} />
          ))}
        </div>
      )}
      <EventSheet id={picked} onClose={() => setPicked(null)} onPick={setPicked} />
    </div>
  );
}
