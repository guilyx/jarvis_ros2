import { Sparkles } from "lucide-react";
import { useInsights } from "@/api/queries";
import { Empty, ErrorNote, IncidentLink, Loading, PageHeader } from "@/components/domain";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ago } from "@/lib/format";

interface InsightPayload {
  title?: string;
  summary?: string;
  confidence?: number;
  evidence?: (string | { event_id?: string; seq?: string })[];
  recommendations?: (string | { text?: string; action?: string })[];
}

export function Agent() {
  const insights = useInsights(50);
  return (
    <div className="space-y-4">
      <PageHeader title="Agent" />
      <Card>
        <CardBody className="flex flex-wrap items-center gap-2">
          <Sparkles size={16} className="text-violet-400" />
          <Input
            disabled
            className="min-w-64 flex-1"
            placeholder="Ask Jarvis why something happened… (arrives with milestone 4)"
          />
          <Button disabled>Ask</Button>
        </CardBody>
      </Card>
      <ErrorNote error={insights.error} />
      {insights.isLoading ? (
        <Loading />
      ) : !insights.data?.insights.length ? (
        <Card>
          <Empty>
            No insights yet. The agent (milestone 4) reads windows of events and writes evidence-backed
            insights and recovery proposals here.
          </Empty>
        </Card>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {insights.data.insights.map((e) => {
            const p = (e.payload ?? {}) as InsightPayload;
            return (
              <Card key={e.id}>
                <CardBody className="space-y-2">
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="font-medium">{p.title ?? e.message}</h3>
                    <span className="shrink-0 text-xs text-muted-fg">{ago(e.stamp)}</span>
                  </div>
                  {p.confidence !== undefined ? (
                    <div className="flex items-center gap-2 text-xs text-muted-fg">
                      <div className="h-1.5 w-24 rounded bg-muted">
                        <div
                          className="h-1.5 rounded bg-violet-400"
                          style={{ width: `${p.confidence * 100}%` }}
                        />
                      </div>
                      conf {p.confidence.toFixed(2)}
                    </div>
                  ) : null}
                  {p.summary ? <p className="text-sm">{p.summary}</p> : null}
                  {p.recommendations?.length ? (
                    <ul className="list-disc pl-5 text-sm text-muted-fg">
                      {p.recommendations.map((r, i) => (
                        <li key={i}>{typeof r === "string" ? r : (r.text ?? r.action)}</li>
                      ))}
                    </ul>
                  ) : null}
                  <div className="text-xs text-muted-fg">
                    incident <IncidentLink id={e.correlation_id} /> · {p.evidence?.length ?? 0} evidence
                  </div>
                </CardBody>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
