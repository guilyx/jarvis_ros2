import { Sparkles } from "lucide-react";
import { useState } from "react";
import { useInsights } from "@/api/queries";
import { Empty, ErrorNote, Loading, PageHeader } from "@/components/domain";
import { EventSheet } from "@/components/EventSheet";
import { InsightCard } from "@/components/InsightCard";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export function Agent() {
  const insights = useInsights(50);
  const [picked, setPicked] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      <PageHeader title="Agent" />
      <Card>
        <CardBody className="flex flex-wrap items-center gap-2">
          <Sparkles size={16} className="text-violet-400" />
          <Input
            disabled
            className="min-w-64 flex-1"
            placeholder="Ask Jarvis why something happened… (arrives with a model, milestone 4.2)"
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
