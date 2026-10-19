import { createRootRoute, createRoute, createRouter, type RouterHistory } from "@tanstack/react-router";
import { Layout } from "@/components/Layout";
import { Empty } from "@/components/domain";
import { Agent, type AgentSearch } from "@/views/Agent";
import { Analytics, type AnalyticsSearch } from "@/views/Analytics";
import { Events, type EventsSearch } from "@/views/Events";
import { FaultDetail } from "@/views/FaultDetail";
import { Fleet } from "@/views/Fleet";
import { Faults, type FaultsSearch } from "@/views/Faults";
import { IncidentTimeline } from "@/views/IncidentTimeline";
import { Incidents, type IncidentsSearch } from "@/views/Incidents";
import { Overview } from "@/views/Overview";
import { ExecutionDetail, Recovery, type RecoverySearch } from "@/views/Recovery";
import { RuleLab, type RuleLabSearch } from "@/views/RuleLab";
import { RuleDetail, Rules } from "@/views/Rules";
import { System } from "@/views/System";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);
function pick<T extends string>(v: unknown, allowed: readonly T[]): T | undefined {
  return allowed.includes(v as T) ? (v as T) : undefined;
}

const root = createRootRoute({
  component: Layout,
  notFoundComponent: () => <Empty>Nothing here.</Empty>,
});

// Filters live in the URL: every view is shareable as it is.
const routes = [
  createRoute({ getParentRoute: () => root, path: "/", component: Overview }),
  createRoute({
    getParentRoute: () => root,
    path: "/events",
    component: Events,
    validateSearch: (s: Record<string, unknown>): EventsSearch => ({
      kind: str(s.kind),
      min_severity: str(s.min_severity),
      code: str(s.code),
      component: str(s.component),
      q: str(s.q),
      incident: str(s.incident),
      event: str(s.event),
    }),
  }),
  createRoute({
    getParentRoute: () => root,
    path: "/faults",
    component: Faults,
    validateSearch: (s: Record<string, unknown>): FaultsSearch => ({
      tab: pick(s.tab, ["active", "history"] as const),
      min_severity: str(s.min_severity),
      component: str(s.component),
      ack: pick(s.ack, ["yes", "no"] as const),
    }),
  }),
  createRoute({ getParentRoute: () => root, path: "/faults/$faultId", component: FaultDetail }),
  createRoute({
    getParentRoute: () => root,
    path: "/incidents",
    component: Incidents,
    validateSearch: (s: Record<string, unknown>): IncidentsSearch => ({ state: str(s.state) }),
  }),
  createRoute({ getParentRoute: () => root, path: "/incidents/$incidentId", component: IncidentTimeline }),
  createRoute({
    getParentRoute: () => root,
    path: "/recovery",
    component: Recovery,
    validateSearch: (s: Record<string, unknown>): RecoverySearch => ({
      tab: pick(s.tab, ["running", "pending", "history"] as const),
    }),
  }),
  createRoute({ getParentRoute: () => root, path: "/recovery/$executionId", component: ExecutionDetail }),
  createRoute({ getParentRoute: () => root, path: "/rules", component: Rules }),
  createRoute({
    getParentRoute: () => root,
    path: "/rules/lab",
    component: RuleLab,
    validateSearch: (s: Record<string, unknown>): RuleLabSearch => ({ rule: str(s.rule) }),
  }),
  createRoute({ getParentRoute: () => root, path: "/rules/$ruleId", component: RuleDetail }),
  createRoute({
    getParentRoute: () => root,
    path: "/analytics",
    component: Analytics,
    validateSearch: (s: Record<string, unknown>): AnalyticsSearch => {
      const hours = Number(s.hours);
      return { hours: Number.isFinite(hours) && hours > 0 ? hours : undefined };
    },
  }),
  createRoute({
    getParentRoute: () => root,
    path: "/agent",
    component: Agent,
    validateSearch: (s: Record<string, unknown>): AgentSearch => ({
      incident: typeof s.incident === "string" && s.incident ? s.incident : undefined,
    }),
  }),
  createRoute({ getParentRoute: () => root, path: "/fleet", component: Fleet }),
  createRoute({ getParentRoute: () => root, path: "/system", component: System }),
] as const;

export const routeTree = root.addChildren(routes);

export function makeRouter(history?: RouterHistory) {
  return createRouter({ routeTree, history, defaultPreload: "intent", scrollRestoration: true });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof makeRouter>;
  }
}
