import { describe, expect, it } from "vitest";
import type { Backtest } from "@/api/types";
import { asDocument, timelineOption } from "./RuleLab";

type Points = { data: { value: number[]; tip: string }[] };

describe("rule lab", () => {
  it("turns a rule's definition into a rules document to edit", () => {
    const rule = "id: lidar-silent-restart\nwhen:\n  event: { kind: FAULT_STATE }\n\nthen: []\n";
    expect(asDocument(rule)).toBe(
      "schema: jarvis.rules/v1\nrules:\n  - id: lidar-silent-restart\n    when:\n      event: { kind: FAULT_STATE }\n\n    then: []\n",
    );
  });

  it("puts each fire and each recovery on its rule's lane", () => {
    const b: Backtest = {
      success: true,
      message: "",
      events: 3,
      from: "2026-10-19T01:00:00Z",
      to: "2026-10-19T02:00:00Z",
      hours: 1,
      truncated: false,
      match_count: 2,
      rules: [
        { rule: "a", fires: 1, dry_fires: 0, suppressed: 0, cooled_down: 0, guard_blocked: 0 },
        { rule: "b", fires: 1, dry_fires: 0, suppressed: 0, cooled_down: 0, guard_blocked: 0 },
      ],
      matches: [
        { at: "2026-10-19T01:10:00Z", rule: "b", match_id: "m1", cause_id: "c1", cause: null },
        {
          at: "2026-10-19T01:20:00Z",
          rule: "a",
          match_id: "m2",
          cause_id: "c2",
          cause: {
            id: "c2",
            kind: "FAULT_STATE",
            code: "x.<y>",
            component: "lidar",
            severity: "ERROR",
            message: "",
            at: "",
          },
        },
      ],
      recoveries: [
        { at: "2026-10-19T01:20:00Z", rule: "a", plan: "restart", state: "REJECTED", reason: "cooldown" },
      ],
    };
    const option = timelineOption(b, true) as {
      yAxis: { axisLabel: { formatter: (v: number) => string } };
      series: [Points, Points];
    };
    expect([0, 1, 2].map(option.yAxis.axisLabel.formatter)).toEqual(["a", "b", ""]);
    const [fires, recoveries] = option.series;
    expect(fires.data.map((d) => d.value[1])).toEqual([1, 0]);
    expect(fires.data[0]!.tip).toContain("another rule's event");
    expect(fires.data[1]!.tip).toContain("x.&lt;y&gt; on lidar");
    // Just below its rule's fire, at the same instant.
    expect(recoveries.data[0]).toMatchObject({ value: [Date.parse("2026-10-19T01:20:00Z"), 0.32] });
    expect(recoveries.data[0]!.tip).toContain("REJECTED");
  });
});
