import { describe, expect, it } from "vitest";

import { parseEvent } from "./ask";

describe("parseEvent", () => {
  it("reads progress, insights and errors", () => {
    expect(parseEvent('event: progress\ndata: {"stage":"tool","detail":"query_events {}"}')).toEqual({
      kind: "progress",
      stage: "tool",
      detail: "query_events {}",
    });
    const insight = parseEvent('event: insight\ndata: {"title":"t"}');
    expect(insight?.kind === "insight" && insight.insight.title).toBe("t");
    expect(parseEvent('event: error\ndata: {"message":"no incident \'x\'"}')).toEqual({
      kind: "error",
      message: "no incident 'x'",
    });
  });

  it("ignores what it does not know", () => {
    expect(parseEvent(": keep-alive")).toBeNull();
    expect(parseEvent("event: progress\ndata: not json")).toBeNull();
    expect(parseEvent('event: other\ndata: {"a":1}')).toBeNull();
  });
});
