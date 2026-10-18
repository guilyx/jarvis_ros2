import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Execution } from "@/api/types";
import { DecideButtons } from "./DecideButtons";

// openapi-fetch takes `fetch` when the client is created: nothing may reach the network here.
vi.hoisted(() => {
  globalThis.fetch = vi.fn() as unknown as typeof fetch;
});

const run = (type: Execution["requester"]["type"], id: string) =>
  ({
    execution_id: "x1",
    plan_id: "safe-stop",
    args: {},
    requester: { type, id },
    dry_run: false,
    state: "PENDING_APPROVAL",
  }) as Execution;

function show(x: Execution, me = "olga") {
  const client = new QueryClient();
  client.setQueryData(["me"], { name: me, role: "operator" });
  render(
    <QueryClientProvider client={client}>
      <DecideButtons x={x} />
    </QueryClientProvider>,
  );
}

describe("DecideButtons", () => {
  it("offers Approve on someone else's run", () => {
    show(run("OPERATOR", "ada"));
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });

  it("does not let an operator approve their own run (four-eyes), only withdraw it", () => {
    show(run("OPERATOR", "Olga "));
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Withdraw" })).toBeInTheDocument();
    expect(screen.getByText("Another operator approves")).toBeInTheDocument();
  });

  it("lets any operator approve what a rule or the agent asked for", () => {
    show(run("AGENT", "olga"));
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });
});
