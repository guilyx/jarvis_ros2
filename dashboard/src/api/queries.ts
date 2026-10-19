import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, client, unwrap } from "./client";
import type {
  Backtest,
  Bag,
  ComponentHealth,
  ExecutionRecord,
  Execution,
  Fault,
  FaultEpisode,
  FaultTransition,
  FleetRobot,
  Flapping,
  HeatCell,
  Health,
  Incident,
  IncidentDetail,
  JarvisEvent,
  LintFinding,
  Me,
  Outcome,
  Plan,
  PlanStats,
  RatePoint,
  RecoveryMode,
  Rule,
  RuleFires,
  RuleMatch,
  Stats,
  SystemInfo,
  TopCode,
} from "./types";

// -- who and how ------------------------------------------------------------------------------------

export const useMe = () =>
  useQuery({ queryKey: ["me"], queryFn: () => unwrap<Me>(client.GET("/api/v1/me")), staleTime: 60_000 });

export const useHealth = () =>
  useQuery({
    queryKey: ["health"],
    queryFn: () => unwrap<Health>(client.GET("/api/v1/health")),
    refetchInterval: 10_000,
  });

export const useSystem = () =>
  useQuery({
    queryKey: ["system"],
    queryFn: () => unwrap<SystemInfo>(client.GET("/api/v1/system")),
    refetchInterval: 10_000,
  });

// Each robot's gateway is asked when the view is: refresh at the pace of a glance.
export const useFleet = () =>
  useQuery({
    queryKey: ["fleet"],
    queryFn: () => unwrap<{ robots: FleetRobot[] }>(client.GET("/api/v1/fleet")),
    refetchInterval: 5_000,
    placeholderData: keepPreviousData,
  });

// -- events ------------------------------------------------------------------------------------------

export interface EventFilter {
  kind?: string[];
  min_severity?: string;
  code?: string;
  component?: string;
  q?: string;
  correlation_id?: string;
}

export interface EventPage {
  events: JarvisEvent[];
  next_before_seq: string | null;
}

export const fetchEvents = (filter: EventFilter, before_seq?: string, limit = 200) =>
  unwrap<EventPage>(
    client.GET("/api/v1/events", {
      params: {
        query: {
          kind: filter.kind?.length ? filter.kind : undefined,
          code: filter.code ? [filter.code] : undefined,
          min_severity: filter.min_severity || undefined,
          component: filter.component || undefined,
          q: filter.q || undefined,
          correlation_id: filter.correlation_id || undefined,
          before_seq,
          limit,
        },
      },
    }),
  );

export const useEvents = (filter: EventFilter) =>
  useQuery({
    queryKey: ["events", filter],
    queryFn: () => fetchEvents(filter),
    placeholderData: keepPreviousData,
  });

export interface EventDetail {
  event: JarvisEvent;
  parent: JarvisEvent | null;
  children: JarvisEvent[];
}

export const useEvent = (id: string | null) =>
  useQuery({
    queryKey: ["event", id],
    enabled: !!id,
    queryFn: () =>
      unwrap<EventDetail>(client.GET("/api/v1/events/{event_id}", { params: { path: { event_id: id! } } })),
  });

export const useAudit = (limit = 200) =>
  useQuery({
    queryKey: ["audit", limit],
    queryFn: () =>
      unwrap<{ events: JarvisEvent[] }>(client.GET("/api/v1/audit", { params: { query: { limit } } })),
  });

// -- faults and incidents ----------------------------------------------------------------------------

export const useFaultHistory = (enabled: boolean) =>
  useQuery({
    queryKey: ["faults", "all"],
    enabled,
    queryFn: () =>
      unwrap<{ faults: Fault[]; history: FaultEpisode[] }>(
        client.GET("/api/v1/faults", { params: { query: { state: "all" } } }),
      ),
  });

export interface FaultDetail {
  fault: Fault | null;
  episode: FaultEpisode | null;
  transitions: FaultTransition[];
  executions: ExecutionRecord[];
}

export const useFault = (id: string) =>
  useQuery({
    queryKey: ["fault", id],
    queryFn: () =>
      unwrap<FaultDetail>(client.GET("/api/v1/faults/{fault_id}", { params: { path: { fault_id: id } } })),
  });

/** The runbook of a fault code: Markdown, or null when its catalog entry has none. */
export const useRunbook = (code: string) =>
  useQuery({
    queryKey: ["runbook", code],
    enabled: !!code,
    staleTime: 60_000,
    queryFn: async () => {
      try {
        const book = await unwrap<{ code: string; markdown: string }>(
          client.GET("/api/v1/runbooks/{code}", { params: { path: { code } } }),
        );
        return book.markdown;
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
  });

export const useIncidents = (state = "") =>
  useQuery({
    queryKey: ["incidents", state],
    queryFn: () =>
      unwrap<{ incidents: Incident[] }>(
        client.GET("/api/v1/incidents", { params: { query: { state, limit: 500 } } }),
      ),
  });

export const useIncident = (id: string) =>
  useQuery({
    queryKey: ["incident", id],
    queryFn: () =>
      unwrap<IncidentDetail>(
        client.GET("/api/v1/incidents/{incident_id}", { params: { path: { incident_id: id } } }),
      ),
  });

// -- recovery, plans, rules ---------------------------------------------------------------------------

export interface Recoveries {
  mode: RecoveryMode;
  live: Execution[];
  recent: Execution[];
  history: ExecutionRecord[];
}

export const useRecoveries = () =>
  useQuery({
    queryKey: ["recoveries"],
    queryFn: () =>
      unwrap<Recoveries>(client.GET("/api/v1/recoveries", { params: { query: { limit: 200 } } })),
  });

export const useRecovery = (id: string) =>
  useQuery({
    queryKey: ["recovery", id],
    queryFn: () =>
      unwrap<{ execution: Execution | null; history: ExecutionRecord | null }>(
        client.GET("/api/v1/recoveries/{execution_id}", { params: { path: { execution_id: id } } }),
      ),
  });

export const usePlans = () =>
  useQuery({
    queryKey: ["plans"],
    queryFn: () => unwrap<{ mode: RecoveryMode; plans: Plan[] }>(client.GET("/api/v1/plans")),
    staleTime: 30_000,
  });

export const useRules = () =>
  useQuery({ queryKey: ["rules"], queryFn: () => unwrap<{ rules: Rule[] }>(client.GET("/api/v1/rules")) });

export const useRule = (id: string) =>
  useQuery({
    queryKey: ["rule", id],
    queryFn: () =>
      unwrap<Rule & { yaml: string; matches: RuleMatch[] }>(
        client.GET("/api/v1/rules/{rule_id}", { params: { path: { rule_id: id } } }),
      ),
    enabled: !!id,
  });

// -- history and analytics ------------------------------------------------------------------------------

export const useStats = (hours: number) =>
  useQuery({
    queryKey: ["stats", hours],
    queryFn: () => unwrap<Stats>(client.GET("/api/v1/stats", { params: { query: { hours } } })),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  });

interface AnalyticsRows {
  components: ComponentHealth;
  top_codes: TopCode;
  heatmap: HeatCell;
  flapping: Flapping;
  plans: PlanStats;
  rules: RuleFires;
  event_rate: RatePoint;
}

export function useAnalytics<N extends keyof AnalyticsRows>(
  name: N,
  hours: number,
  extra: { bucket_s?: number; min_cycles?: number } = {},
) {
  return useQuery({
    queryKey: ["analytics", name, hours, extra],
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
    queryFn: async () =>
      (
        await unwrap<{ rows: AnalyticsRows[N][] }>(
          client.GET("/api/v1/analytics/{name}", {
            params: { path: { name }, query: { hours, ...extra } },
          }),
        )
      ).rows,
  });
}

export const useInsights = (limit = 50) =>
  useQuery({
    queryKey: ["insights", limit],
    queryFn: () =>
      unwrap<{ insights: JarvisEvent[] }>(client.GET("/api/v1/insights", { params: { query: { limit } } })),
  });

export const useBags = () =>
  useQuery({
    queryKey: ["bags"],
    queryFn: () => unwrap<{ bags: Bag[] }>(client.GET("/api/v1/bags")),
    refetchInterval: 30_000,
  });

// -- commands ------------------------------------------------------------------------------------------

/** A command: on success the views it changes refetch (the stream also pushes the new state). */
function useCommand<A>(run: (args: A) => Promise<Outcome>, invalidate: string[][]) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSettled: () => Promise.all(invalidate.map((queryKey) => qc.invalidateQueries({ queryKey }))),
  });
}

export const useAck = () =>
  useCommand(
    ({ id, note }: { id: string; note: string }) =>
      unwrap<Outcome>(
        client.POST("/api/v1/faults/{fault_id}:ack", { params: { path: { fault_id: id } }, body: { note } }),
      ),
    [["faults"], ["fault"], ["audit"]],
  );

export const useClear = () =>
  useCommand(
    ({ id, reason, skip_symptoms = false }: { id: string; reason: string; skip_symptoms?: boolean }) =>
      unwrap<Outcome>(
        client.POST("/api/v1/faults/{fault_id}:clear", {
          params: { path: { fault_id: id } },
          body: { reason, skip_symptoms },
        }),
      ),
    [["faults"], ["fault"], ["incidents"], ["incident"], ["audit"], ["stats"]],
  );

export const useCloseIncident = () =>
  useCommand(
    ({ id, note }: { id: string; note: string }) =>
      unwrap<Outcome>(
        client.POST("/api/v1/incidents/{incident_id}:close", {
          params: { path: { incident_id: id } },
          body: { note },
        }),
      ),
    [["incidents"], ["incident"], ["audit"]],
  );

export interface RecoverArgs {
  plan_id: string;
  args: Record<string, string>;
  dry_run: boolean;
  rationale: string;
  correlation_id?: string;
}

export const useRecover = () =>
  useCommand(
    (body: RecoverArgs) =>
      unwrap<Outcome>(
        client.POST("/api/v1/recoveries", { body: { ...body, correlation_id: body.correlation_id ?? "" } }),
      ),
    [["recoveries"], ["audit"]],
  );

export const useDecide = () =>
  useCommand(
    ({ id, approve, note }: { id: string; approve: boolean; note: string }) =>
      approve
        ? unwrap<Outcome>(
            client.POST("/api/v1/recoveries/{execution_id}:approve", {
              params: { path: { execution_id: id } },
              body: { note },
            }),
          )
        : unwrap<Outcome>(
            client.POST("/api/v1/recoveries/{execution_id}:reject", {
              params: { path: { execution_id: id } },
              body: { note },
            }),
          ),
    [["recoveries"], ["recovery"], ["audit"]],
  );

export const useCancel = () =>
  useCommand(
    ({ id, reason }: { id: string; reason: string }) =>
      unwrap<Outcome>(
        client.POST("/api/v1/recoveries/{execution_id}:cancel", {
          params: { path: { execution_id: id } },
          body: { reason },
        }),
      ),
    [["recoveries"], ["recovery"], ["audit"]],
  );

export const useSetMode = () =>
  useCommand(
    ({ mode, reason }: { mode: Exclude<RecoveryMode, "UNKNOWN">; reason: string }) =>
      unwrap<Outcome>(client.PUT("/api/v1/recovery/mode", { body: { mode, reason, cancel_running: false } })),
    [["recoveries"], ["plans"], ["audit"]],
  );

export const useSetRuleState = () =>
  useCommand(
    ({ id, ...body }: { id: string; enabled?: boolean; dry_run?: boolean; reason: string }) =>
      unwrap<Outcome>(client.PATCH("/api/v1/rules/{rule_id}", { params: { path: { rule_id: id } }, body })),
    [["rules"], ["rule"], ["audit"]],
  );

export const useValidateRules = () =>
  useMutation({
    mutationFn: (yaml: string) =>
      unwrap<Outcome & { data: { findings: LintFinding[] } }>(
        client.POST("/api/v1/rules:validate", { body: { yaml } }),
      ),
  });

export const useBacktestRules = () =>
  useMutation({
    mutationFn: ({ yaml, hours }: { yaml: string; hours: number }) =>
      unwrap<Backtest>(client.POST("/api/v1/rules:backtest", { body: { yaml, hours } })),
  });

export const useReload = () =>
  useCommand(
    ({ target, validate_only }: { target: "rules" | "plans"; validate_only: boolean }) =>
      target === "rules"
        ? unwrap<Outcome>(client.POST("/api/v1/rules:reload", { body: { validate_only } }))
        : unwrap<Outcome>(client.POST("/api/v1/plans:reload", { body: { validate_only } })),
    [["rules"], ["plans"]],
  );
