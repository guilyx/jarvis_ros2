import { describe, expect, it } from "vitest";
import type { FleetHistory } from "@/api/types";
import { byFaults, trendOption } from "./FleetHistory";

const robot = (name: string, faults: number) => ({
  robot: name,
  history_until: "2026-10-19T07:00:00Z",
  events: 10,
  warnings_or_worse: 5,
  faults_confirmed: faults,
  mttr_s: 60,
  incidents_opened: 1,
  incidents_escalated: 0,
  recoveries_finished: 1,
  auto_recovery_rate: 1,
  recovery_success_rate: 1,
});

const HISTORY: FleetHistory = {
  target: "s3://fleet/history",
  hours: 168,
  window: { since: "2026-10-12T07:00:00Z", until: "2026-10-19T07:00:00Z" },
  bucket_s: 86400,
  robots: [robot("amr-7", 3), robot("amr-8", 9)],
  top_codes: [],
  trend: [
    { bucket: "2026-10-17T00:00:00Z", robot: "amr-7", faults: 2 },
    { bucket: "2026-10-17T00:00:00Z", robot: "amr-8", faults: 4 },
    { bucket: "2026-10-18T00:00:00Z", robot: "amr-8", faults: 5 },
  ],
  updated_at: 0,
};

describe("the fleet's history", () => {
  it("stacks each robot's confirmed faults per day, a day without any at zero", () => {
    const o = trendOption(HISTORY, true) as {
      xAxis: { data: string[] };
      series: { name: string; data: number[]; stack: string }[];
    };
    expect(o.xAxis.data).toEqual(["10-17", "10-18"]);
    expect(o.series.map((s) => [s.name, s.data, s.stack])).toEqual([
      ["amr-7", [2, 0], "faults"],
      ["amr-8", [4, 5], "faults"],
    ]);
    const hourly = trendOption({ ...HISTORY, bucket_s: 3600 }, false) as { xAxis: { data: string[] } };
    expect(hourly.xAxis.data).toEqual(["00:00", "00:00"]);
  });

  it("puts the robot with the most faults first", () => {
    expect([robot("a", 1), robot("b", 9), robot("c", 9)].sort(byFaults).map((r) => r.robot)).toEqual([
      "b",
      "c",
      "a",
    ]);
  });
});
