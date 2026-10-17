import { describe, expect, it } from "vitest";
import type { JarvisEvent } from "@/api/types";
import { matches, merge } from "./Events";

const e = (seq: number, patch: Partial<JarvisEvent> = {}): JarvisEvent => ({
  id: `e${seq}`,
  seq: String(seq),
  stamp: "2026-10-17T09:00:00Z",
  source: { robot: "r", node: "/n", component: "lidar", host: "" },
  kind: "LOG",
  code: "ros.log.info",
  severity: "INFO",
  message: `event ${seq}`,
  ...patch,
});

describe("events", () => {
  it("filters live events like the gateway filters history", () => {
    expect(matches(e(1), { min_severity: "WARN" })).toBe(false);
    expect(matches(e(1, { severity: "ERROR" }), { min_severity: "WARN" })).toBe(true);
    expect(matches(e(1), { code: "ros.*", component: "lidar" })).toBe(true);
    expect(matches(e(1), { kind: ["FAULT_STATE"] })).toBe(false);
    expect(matches(e(1), { q: "EVENT 1" })).toBe(true);
    expect(matches(e(1, { attrs: { incident_id: "i1" } }), { correlation_id: "i1" })).toBe(true);
  });

  it("puts live events above history, once each", () => {
    const history = [e(5), e(4), e(3)];
    const live = [e(4), e(5), e(6), e(7, { severity: "DEBUG" })];
    const rows = merge(history, live, [e(2)], {});
    expect(rows.map((r) => r.seq)).toEqual(["7", "6", "5", "4", "3", "2"]);
    expect(merge(history, live, [], { min_severity: "INFO" }).map((r) => r.seq)).toEqual([
      "6",
      "5",
      "4",
      "3",
    ]);
  });
});
