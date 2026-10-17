import { describe, expect, it } from "vitest";

import { analystLabel, type Insight } from "./types";

const base = {
  insight_id: "i",
  trigger: { type: "ON_DEMAND" },
  window: { from: "", to: "", event_count: 0 },
  title: "t",
  summary: "",
  assessment: "ERROR",
  hypotheses: [],
  recommendations: [],
  degraded: false,
} as unknown as Insight;
const usage = { provider: "openrouter", name: "openrouter/a/b", input_tokens: 12000, output_tokens: 400 };

describe("analystLabel", () => {
  it("names the model and what it cost", () => {
    expect(analystLabel({ ...base, analyst: "llm", model: { ...usage, cost_usd: 0.0042 } })).toBe(
      "openrouter/a/b · 12.4k tokens · $0.0042",
    );
    expect(analystLabel({ ...base, analyst: "llm", model: { ...usage, cost_usd: 0.25 } })).toBe(
      "openrouter/a/b · 12.4k tokens · $0.25",
    );
    expect(analystLabel({ ...base, analyst: "llm", model: { ...usage, cost_usd: 0 } })).toBe(
      "openrouter/a/b · 12.4k tokens",
    );
  });

  it("says when the heuristic analyst answered, and why", () => {
    expect(analystLabel({ ...base, analyst: "heuristic", model: null })).toBe("heuristic analyst (no model)");
    expect(analystLabel({ ...base, analyst: "heuristic", model: { ...usage, cost_usd: 0.01 } })).toBe(
      "heuristic analyst (openrouter/a/b could not answer)",
    );
  });
});
