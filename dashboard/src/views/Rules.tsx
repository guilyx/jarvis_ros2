import { Link, useParams } from "@tanstack/react-router";
import { FileCheck2, FlaskConical, RefreshCw } from "lucide-react";
import { useState } from "react";
import { useReload, useRule, useRules, useSetRuleState, useValidateRules } from "@/api/queries";
import type { LintFinding, Rule } from "@/api/types";
import {
  Empty,
  ErrorNote,
  IncidentLink,
  KeyValues,
  Loading,
  Mono,
  PageHeader,
  Table,
  Td,
  Th,
} from "@/components/domain";
import { Button, buttonStyle } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useCan } from "@/lib/auth";
import { ago, clock, dateTime } from "@/lib/format";

function RuleSwitches({ rule }: { rule: Rule }) {
  const set = useSetRuleState();
  const can = useCan();
  const admin = can("admin");
  return (
    <>
      <Td>
        <Switch
          checked={rule.enabled}
          disabled={!admin || set.isPending}
          label={`${rule.id} enabled`}
          onCheckedChange={(enabled) => set.mutate({ id: rule.id, enabled, reason: "dashboard" })}
        />
      </Td>
      <Td>
        <Switch
          checked={rule.dry_run}
          disabled={!admin || set.isPending}
          label={`${rule.id} dry run`}
          onCheckedChange={(dry_run) => set.mutate({ id: rule.id, dry_run, reason: "dashboard" })}
        />
      </Td>
    </>
  );
}

function ValidateDialog() {
  const validate = useValidateRules();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("schema: jarvis.rules/v1\nrules:\n  - id: my-rule\n");
  const findings: LintFinding[] = validate.data?.data.findings ?? [];
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button onClick={() => setOpen(true)}>
        <FileCheck2 size={14} /> Validate YAML…
      </Button>
      <DialogContent
        className="w-[min(760px,calc(100vw-2rem))]"
        title="Validate rules"
        description="Checked with `jarvis lint` against the loaded plans and fault catalog. Nothing changes on the robot."
      >
        <div className="space-y-3">
          <Textarea
            className="h-72 w-full"
            aria-label="Rules YAML"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <ErrorNote error={validate.error} />
          {validate.data ? (
            <div className={validate.data.success ? "text-sm text-emerald-400" : "text-sm text-red-400"}>
              {validate.data.message || (validate.data.success ? "Valid." : "Invalid.")}
            </div>
          ) : null}
          {findings.length ? (
            <ul className="space-y-1 text-sm">
              {findings.map((f, i) => (
                <li key={i} className={f.level === "error" ? "text-red-400" : "text-amber-400"}>
                  {f.level}: <Mono>{f.message}</Mono>
                </li>
              ))}
            </ul>
          ) : null}
          <div className="flex justify-end">
            <Button variant="default" onClick={() => validate.mutate(text)} disabled={validate.isPending}>
              Validate
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function Rules() {
  const rules = useRules();
  const reload = useReload();
  const can = useCan();
  return (
    <div>
      <PageHeader title="Rules">
        {can("admin") ? (
          <>
            <Link to="/rules/lab" search={{}} className={buttonStyle()}>
              <FlaskConical size={14} /> Rule lab
            </Link>
            <ValidateDialog />
            <Button
              onClick={() => reload.mutate({ target: "rules", validate_only: false })}
              disabled={reload.isPending}
            >
              <RefreshCw size={14} /> Reload from disk
            </Button>
          </>
        ) : null}
      </PageHeader>
      <ErrorNote error={rules.error ?? reload.error} />
      {reload.data ? <p className="mb-3 text-sm text-muted-fg">{reload.data.message}</p> : null}
      <Card>
        {rules.isLoading ? (
          <Loading />
        ) : !rules.data?.rules.length ? (
          <Empty>No rules loaded.</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>rule</Th>
                <Th>enabled</Th>
                <Th>dry run</Th>
                <Th className="text-right">fires 24 h</Th>
                <Th>last fire</Th>
                <Th className="text-right">fired</Th>
                <Th className="text-right">suppressed</Th>
                <Th className="text-right">guard blocked</Th>
                <Th className="text-right">action failures</Th>
              </tr>
            </thead>
            <tbody>
              {rules.data.rules.map((r) => (
                <tr key={r.id} data-testid="rule-row">
                  <Td>
                    <Link
                      to="/rules/$ruleId"
                      params={{ ruleId: r.id }}
                      className="font-mono text-xs hover:underline"
                    >
                      {r.id}
                    </Link>
                    <div className="max-w-md truncate text-xs text-muted-fg">{r.description}</div>
                  </Td>
                  <RuleSwitches rule={r} />
                  <Td className="text-right tabular">
                    {r.fires_window}
                    {r.dry_run_fires_window ? (
                      <span className="text-muted-fg"> +{r.dry_run_fires_window} shadow</span>
                    ) : null}
                  </Td>
                  <Td className="text-xs">{r.last_fire ? ago(r.last_fire) : "—"}</Td>
                  <Td className="text-right tabular">{r.fires ?? "—"}</Td>
                  <Td className="text-right tabular">{r.suppressed ?? "—"}</Td>
                  <Td className="text-right tabular">{r.guard_blocked ?? "—"}</Td>
                  <Td className="text-right tabular">{r.action_failures ?? "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}

export function RuleDetail() {
  const { ruleId } = useParams({ from: "/rules/$ruleId" });
  const rule = useRule(ruleId);
  if (rule.isLoading) return <Loading />;
  if (rule.error) return <ErrorNote error={rule.error} />;
  const r = rule.data!;
  return (
    <div className="space-y-4">
      <PageHeader title={<span className="font-mono">{r.id}</span>}>
        {r.yaml ? (
          <Link to="/rules/lab" search={{ rule: r.id }} className={buttonStyle()}>
            <FlaskConical size={14} /> Try changes in the rule lab
          </Link>
        ) : null}
      </PageHeader>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Rule" />
          <CardBody>
            <KeyValues
              rows={[
                ["description", r.description || "—"],
                ["file", <Mono key="f">{r.file || "—"}</Mono>],
                ["enabled", r.enabled ? "yes" : "no"],
                ["dry run", r.dry_run ? "yes: it decides, nothing runs" : "no"],
                ["fired", `${r.fires ?? 0} (dry run ${r.dry_fires ?? 0}) since the rule engine started`],
                ["suppressed", r.suppressed ?? 0],
                ["cooled down", r.cooled_down ?? 0],
                ["guard blocked", r.guard_blocked ?? 0],
                ["partitions", r.partitions ?? 0],
                ["last fire", dateTime(r.last_fire)],
              ]}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Definition" actions={<span className="text-xs text-muted-fg">read-only</span>} />
          <CardBody>
            {r.yaml ? (
              <pre className="max-h-96 overflow-auto rounded-md bg-bg p-3 font-mono text-xs ring-1 ring-border">
                {r.yaml}
              </pre>
            ) : (
              <Empty className="py-4">The rule's file is not readable from the gateway.</Empty>
            )}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader title="Recent matches" />
        {r.matches.length ? (
          <Table>
            <thead>
              <tr>
                <Th>time</Th>
                <Th>seq</Th>
                <Th>component</Th>
                <Th>mode</Th>
                <Th>incident</Th>
              </tr>
            </thead>
            <tbody>
              {r.matches.map((m) => (
                <tr key={m.id}>
                  <Td className="font-mono text-xs tabular">{clock(m.ts)}</Td>
                  <Td className="font-mono text-xs">{m.seq}</Td>
                  <Td className="text-xs">{m.component || "—"}</Td>
                  <Td className="text-xs">{m.dry_run ? "dry run" : "live"}</Td>
                  <Td>
                    <IncidentLink id={m.incident_id} />
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <Empty>It has not matched yet.</Empty>
        )}
      </Card>
    </div>
  );
}
