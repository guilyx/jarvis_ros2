import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Fault } from "@/api/types";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LiveProvider } from "@/live/context";
import { LiveStream, type SocketLike } from "@/live/stream";
import { ThemeProvider } from "@/lib/theme";
import { makeRouter } from "./router";

// openapi-fetch takes `fetch` when the client is created: replace it before any import runs.
const fetchMock = vi.hoisted(() => {
  const f = vi.fn();
  globalThis.fetch = f as unknown as typeof fetch;
  return f;
});

vi.mock("@/components/Chart", () => ({
  Chart: ({ label }: { label: string }) => <div role="img" aria-label={label} />,
  chartColors: () => ({ text: "", line: "", fg: "", tooltip: "" }),
}));

const ESTOP: Fault = {
  fault_id: "f-estop",
  code: "safety.estop.engaged",
  source: { robot: "bot", node: "/safety", component: "base", host: "" },
  state: "LATCHED",
  severity: "CRITICAL",
  occurrences: 1,
  first_seen: "2026-10-17T09:00:00Z",
  confirmed_at: "2026-10-17T09:00:00Z",
  acknowledged: false,
  acknowledged_by: "",
  latch: true,
  muted_by: "",
  incident_id: "i1",
};

const KPIS = {
  window: { since: "", until: "" },
  events: 12,
  warnings_or_worse: 3,
  faults_confirmed: 1,
  mttr_s: 42,
  incidents_opened: 1,
  incidents_open: 1,
  incidents_escalated: 0,
  auto_recovery_rate: 0.5,
  recoveries_finished: 2,
  recovery_success_rate: 1,
};

const INSIGHT_EVENT = {
  id: "ins-1",
  seq: "40",
  stamp: "2026-10-17T09:01:00Z",
  source: { robot: "bot", node: "/jarvis/agent", component: "", host: "" },
  kind: "INSIGHT",
  code: "jarvis.insight",
  severity: "INFO",
  correlation_id: "i1",
  payload: {
    insight_id: "x",
    trigger: { type: "RECOVERY_FAILED", ref: "i1" },
    window: { from: "", to: "", event_count: 12, correlation_id: "i1" },
    title: "sensor.scan.stale on lidar: restart-lifecycle-node did not fix it",
    summary: "",
    assessment: "ERROR",
    hypotheses: [{ cause: "The lidar itself stopped", confidence: 0.55, evidence: ["e1", "e2"] }],
    recommendations: [
      { kind: "INVESTIGATE", text: "Look at the lidar" },
      {
        kind: "RECOVER",
        text: "it fixed it before",
        plan_id: "restart-lifecycle-node",
        args: { component: "lidar" },
      },
    ],
    degraded: true,
    analyst: "heuristic",
    facts: ["recovery restart-lifecycle-node: FAILED after 15 s"],
  },
};

// What a model-backed agent at autonomy supervised publishes: a proposal waiting for an operator.
const PROPOSING_INSIGHT = {
  ...INSIGHT_EVENT,
  id: "ins-2",
  payload: {
    ...INSIGHT_EVENT.payload,
    insight_id: "y",
    title: "The relay hangs; a restart should clear it",
    recommendations: [],
    proposals: ["x-9"],
    degraded: false,
    analyst: "llm",
    model: {
      provider: "openrouter",
      name: "openrouter/a/b",
      input_tokens: 9000,
      output_tokens: 600,
      cost_usd: 0.02,
    },
  },
};
const PROPOSED = {
  execution_id: "x-9",
  plan_id: "restart-lifecycle-node",
  args: { node: "/demo/scan_relay", component: "lidar" },
  requester: { type: "AGENT", id: "/jarvis/agent" },
  rationale: "The relay hangs again (insight y)",
  confidence: 0.8,
  dry_run: false,
  state: "PENDING_APPROVAL",
  gates: [],
  steps: [],
  created: "2026-10-17T09:02:00Z",
};

function api(role: "viewer" | "operator") {
  const calls: { method: string; path: string; body: unknown }[] = [];
  fetchMock.mockImplementation(async (input: Request) => {
    const url = new URL(input.url);
    const body = input.method === "GET" ? null : await input.json();
    calls.push({ method: input.method, path: url.pathname, body });
    const json = (data: unknown, status = 200) =>
      new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
    switch (url.pathname) {
      case "/api/v1/me":
        return json({ name: role === "operator" ? "olga" : "anonymous", role });
      case "/api/v1/health":
        return json({
          status: "ok",
          connected: true,
          faults_snapshot: true,
          executions_snapshot: true,
          robot: "bot-7",
        });
      case "/api/v1/stats":
        return json({ hours: 24, kpis: KPIS, faults_open: {}, event_rate: [], top_codes: [], plans: [] });
      case "/api/v1/incidents":
        return json({ incidents: [] });
      case "/api/v1/insights":
        return json({
          insights: url.searchParams.get("limit") === "50" ? [PROPOSING_INSIGHT, INSIGHT_EVENT] : [],
        });
      case "/api/v1/agent/ask": {
        const events = [
          ["progress", { stage: "window", detail: "12 events, incident i1" }],
          ["progress", { stage: "tool", detail: "query_events {}" }],
          [
            "insight",
            { ...PROPOSING_INSIGHT.payload, insight_id: "z", title: "Asked: the relay hangs", proposals: [] },
          ],
        ];
        return new Response(events.map(([k, d]) => `event: ${k}\ndata: ${JSON.stringify(d)}\n\n`).join(""), {
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      case "/api/v1/recoveries/x-9":
        return json({ execution: PROPOSED, history: null });
      case "/api/v1/recoveries/x-9:reject":
        return json({ success: true, message: "rejected", data: {} });
      case "/api/v1/fleet":
        return json({
          robots: [
            {
              name: "bot-7",
              url: "",
              local: true,
              online: true,
              mode: "ENABLED",
              faults: { total: 0 },
              incidents: 0,
              latched: 0,
            },
            {
              name: "amr-2",
              url: "http://amr-2:8080",
              online: true,
              mode: "DRY_RUN",
              latency_ms: 12,
              faults: { CRITICAL: 1, ERROR: 2, total: 3 },
              worst: {
                code: "safety.estop.engaged",
                severity: "CRITICAL",
                state: "LATCHED",
                component: "base",
                message: "e-stop pressed",
                fault_id: "f",
              },
              incidents: 2,
              latched: 1,
            },
            {
              name: "amr-3",
              url: "http://amr-3:8080",
              online: false,
              error: "unreachable",
              mode: "UNKNOWN",
              faults: {},
              incidents: 0,
              latched: 0,
            },
          ],
        });
      case "/api/v1/plans":
        return json({ mode: "ENABLED", plans: [] });
      case "/api/v1/fleet/history":
        if (role === "viewer") return json({ detail: "no fleet history here" }, 404);
        return json({
          target: "s3://fleet/history",
          hours: Number(url.searchParams.get("hours")),
          window: { since: "2026-10-12T00:00:00Z", until: "2026-10-19T00:00:00Z" },
          bucket_s: 86400,
          robots: ["amr-2", "bot-7"].map((robot, i) => ({
            robot,
            history_until: "2026-10-18T23:00:00Z",
            events: 100,
            warnings_or_worse: 40,
            faults_confirmed: i ? 4 : 31,
            mttr_s: 90,
            incidents_opened: 12,
            incidents_escalated: 2,
            recoveries_finished: 10,
            auto_recovery_rate: 0.8,
            recovery_success_rate: 0.9,
          })),
          top_codes: [
            {
              code: "sensor.scan.stale",
              episodes: 20,
              robots: 2,
              robot_names: ["amr-2", "bot-7"],
              mttr_s: 12,
            },
          ],
          trend: [{ bucket: "2026-10-18T00:00:00Z", robot: "amr-2", faults: 5 }],
          updated_at: Date.now() / 1000,
        });
      case "/api/v1/system":
        return json({
          version: "0.1.0",
          robot: "bot-7",
          uptime_s: 60,
          connected: true,
          events_received: 10,
          recent_events: 10,
          stream_clients: 1,
          components: {},
          notifications: [],
          webhooks: [],
          lake: {
            rows: 1200,
            upload: {
              target: "s3://fleet/history?endpoint_override=minio%3A9000",
              robot: "bot-7",
              blackbox: false,
              pending_files: 3,
              pending_bytes: 3 * 1024 * 1024,
              oldest_pending_s: 7200,
              behind: true,
              max_lag_s: 3600,
              uploaded_total: 40,
              uploaded_bytes_total: 1 << 30,
              last_upload_at: null,
              last_error: "b1_0.parquet: OSError: unreachable",
              retry_at: null,
              lost: 2,
            },
          },
        });
      case "/api/v1/bags":
        return json({ bags: [] });
      case "/api/v1/audit":
        return json({ events: [] });
      case "/api/v1/faults/f-estop:ack":
        return json({ success: true, message: "acknowledged", data: {} });
      default:
        return json({ detail: "not found" }, 404);
    }
  });
  return calls;
}

function liveWith(faults: Fault[]) {
  let socket: SocketLike | null = null;
  const stream = new LiveStream(
    () => "ws://test",
    () => {
      socket = { onopen: null, onmessage: null, onclose: null, send: () => {}, close: () => {} };
      queueMicrotask(() => {
        socket!.onmessage?.({ data: JSON.stringify({ type: "faults", data: { last_seq: "9", faults } }) });
        socket!.onmessage?.({
          data: JSON.stringify({ type: "executions", data: { mode: "ENABLED", executions: [], recent: [] } }),
        });
        socket!.onmessage?.({ data: JSON.stringify({ type: "backfill_done", seq: "9", truncated: false }) });
      });
      return socket;
    },
  );
  return stream;
}

function renderApp(path = "/") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = makeRouter(createMemoryHistory({ initialEntries: [path] }));
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <LiveProvider stream={liveWith([ESTOP])}>
          <TooltipProvider>
            <RouterProvider router={router} />
          </TooltipProvider>
        </LiveProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

describe("dashboard", () => {
  it("shows the KPIs, the live state and what needs a human", async () => {
    api("operator");
    renderApp();
    expect(await screen.findByText("bot-7")).toBeInTheDocument();
    expect(await screen.findByTestId("live-status")).toHaveTextContent("LIVE");
    expect(await screen.findByText("42 s")).toBeInTheDocument(); // MTTR
    expect(screen.getByText("50 %")).toBeInTheDocument(); // auto-recovery
    const row = await screen.findByTestId("attention-fault");
    expect(within(row).getByText("safety.estop.engaged")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Recovery mode" })).toHaveTextContent("ENABLED");
  });

  it("acknowledges a fault with a note, in the operator's name", async () => {
    const calls = api("operator");
    renderApp();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Acknowledge safety.estop.engaged" }));
    await user.type(await screen.findByRole("textbox", { name: "Note" }), "on my way");
    await user.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")).toEqual({
      method: "POST",
      path: "/api/v1/faults/f-estop:ack",
      body: { note: "on my way" },
    });
  });

  it("shows the agent's insights, with a way to act on its advice", async () => {
    api("operator");
    renderApp("/agent");
    const card = (await screen.findAllByTestId("insight-card")).find((c) =>
      within(c).queryByText(/did not fix it/),
    )!;
    expect(within(card).getByText("The lidar itself stopped")).toBeInTheDocument();
    expect(within(card).getByText("0.55")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Run restart-lifecycle-node" })).toBeInTheDocument();
    expect(within(card).getByText(/heuristic analyst/)).toBeInTheDocument();
  });

  it("shows the agent's proposals, and lets an operator decide", async () => {
    const calls = api("operator");
    renderApp("/agent");
    const proposal = await screen.findByTestId("insight-proposal");
    const card = proposal.closest("[data-testid=insight-card]") as HTMLElement;
    expect(within(card).getByText(/openrouter\/a\/b · 9.6k tokens · \$0.02/)).toBeInTheDocument();
    expect(await within(proposal).findByText("restart-lifecycle-node")).toBeInTheDocument();
    await within(proposal).findByText(/pending approval/);
    const user = userEvent.setup();
    await user.click(within(proposal).getByRole("button", { name: "Reject" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/recoveries/x-9:reject")).toBe(true));
  });

  it("asks the agent and streams its answer", async () => {
    const calls = api("operator");
    renderApp("/agent?incident=i1");
    const box = await screen.findByTestId("ask-box");
    expect(within(box).getByText(/about incident/)).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(within(box).getByRole("textbox", { name: "Question" }), "Why?");
    await user.click(within(box).getByRole("button", { name: "Ask" }));
    expect(await within(box).findByText("Asked: the relay hangs")).toBeInTheDocument();
    expect(within(within(box).getByTestId("ask-progress")).getByText("query_events {}")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/v1/agent/ask")?.body).toEqual({
      incident_id: "i1",
      question: "Why?",
    });
  });

  it("shows the fleet, the robots that need someone first", async () => {
    api("viewer");
    renderApp("/fleet");
    expect(await screen.findByTestId("fleet-summary")).toHaveTextContent(
      "3 robots · 1 with open faults · 1 offline",
    );
    const cards = await screen.findAllByTestId("fleet-robot");
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual(["amr-3", "amr-2", "bot-7"]);
    expect(within(cards[0]!).getByText(/Offline: unreachable/)).toBeInTheDocument();
    expect(within(cards[1]!).getByText("safety.estop.engaged")).toBeInTheDocument();
    expect(within(cards[1]!).getByRole("link", { name: /Open its dashboard/ })).toHaveAttribute(
      "href",
      "http://amr-2:8080",
    );
    expect(within(cards[2]!).getByText("No open faults")).toBeInTheDocument();
  });

  it("shows the history going off the robot, and when it is behind", async () => {
    api("operator");
    renderApp("/system");
    expect(await screen.findByText("s3://fleet/history?endpoint_override=minio%3A9000")).toBeInTheDocument();
    const waiting = screen.getByTestId("upload-waiting");
    expect(waiting).toHaveTextContent("3 file(s), 3.0 MiB, oldest 2 h");
    expect(waiting).toHaveTextContent("(behind)");
    expect(screen.getByText(/OSError: unreachable/)).toBeInTheDocument();
    expect(screen.getByText("2 file(s)")).toBeInTheDocument(); // lost before upload
  });

  it("shows the fleet's history: the worst robot first, what costs the fleet most", async () => {
    const calls = api("operator");
    renderApp("/fleet");
    const history = await screen.findByTestId("fleet-history");
    const rows = await within(history).findAllByTestId("fleet-history-robot");
    expect(rows.map((r) => r.firstChild?.textContent)).toEqual(["amr-2", "bot-7"]);
    expect(rows[0]).toHaveTextContent("31");
    expect(within(history).getByTestId("fleet-history-code")).toHaveTextContent(
      "sensor.scan.stale20amr-2, bot-7",
    );
    expect(within(history).getByText("s3://fleet/history")).toBeInTheDocument();
    await userEvent.click(within(history).getByRole("tab", { name: "30 d" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/fleet/history")).toBe(true));
  });

  it("says how to set up the fleet's history when there is none", async () => {
    api("viewer");
    renderApp("/fleet");
    const history = await screen.findByTestId("fleet-history");
    expect(await within(history).findByText(/No fleet history here/)).toBeInTheDocument();
  });

  it("hides commands from viewers", async () => {
    api("viewer");
    renderApp();
    await screen.findByTestId("attention-fault");
    await screen.findByText("anonymous");
    expect(screen.queryByRole("button", { name: /Acknowledge/ })).not.toBeInTheDocument();
  });
});
