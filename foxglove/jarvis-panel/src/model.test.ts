// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";

import { EventLog, EventMsg, History, ago, clock, counts, openFaults, robotOf } from "./model";

const t = (sec: number) => ({ sec, nanosec: 0 });
const fault = (code: string, severity: number, state: number, extra = {}) => ({
  fault_id: `f-${code}`,
  code,
  severity,
  state,
  source: { robot: "amr-7", component: "lidar", node: "/scan_relay" },
  first_seen: t(100),
  ...extra,
});
const event = (id: string, severity: number, sec = 100): EventMsg => ({
  id,
  seq: BigInt(sec),
  stamp: { sec, nsec: 500_000_000 },
  source: { robot: "amr-7", component: "lidar" },
  kind: 5,
  code: "sensor.scan.stale",
  severity,
  message: `event ${id}`,
});

describe("openFaults", () => {
  it("keeps the open ones, worst first, latched before active, then the oldest", () => {
    const rows = openFaults({
      faults: [
        fault("diag.a", 30, 2),
        fault("sensor.scan.stale", 40, 2, { first_seen: t(200) }),
        fault("safety.estop.engaged", 40, 3, { first_seen: t(300) }),
        fault("old.one", 40, 2, { first_seen: t(50) }),
        fault("gone", 50, 4), // cleared
        fault("symptom", 50, 2, { muted_by: "f-root" }), // a muted symptom
      ],
    });
    expect(rows.map((r) => r.code)).toEqual([
      "safety.estop.engaged",
      "old.one",
      "sensor.scan.stale",
      "diag.a",
    ]);
    expect(rows[0]).toMatchObject({ severity: "ERROR", state: "LATCHED", component: "lidar" });
    expect(counts(rows)).toEqual({ CRITICAL: 0, ERROR: 3, WARN: 1 });
  });

  it("is empty without a message", () => {
    expect(openFaults(undefined)).toEqual([]);
  });
});

describe("EventLog", () => {
  it("keeps events once, from the minimum severity, the last ones", () => {
    const log = new EventLog(3, "WARN");
    expect(log.add({ events: [event("a", 20), event("b", 30), event("c", 40)] })).toBe(true);
    expect(log.all.map((r) => r.id)).toEqual(["b", "c"]); // INFO left out
    expect(log.add({ events: [event("c", 40)] })).toBe(false); // seen
    log.add({ events: [event("d", 50), event("e", 40)] });
    expect(log.all.map((r) => r.id)).toEqual(["c", "d", "e"]);
    expect(log.all[0]).toMatchObject({ seq: "100", kind: "FAULT_STATE", severity: "ERROR" });
    log.configure(2, "CRITICAL");
    expect(log.all.map((r) => r.id)).toEqual(["d"]);
    log.clear();
    expect(log.all).toEqual([]);
    expect(log.add({ events: [event("d", 50)] })).toBe(true); // a seek: shown again
  });
});

describe("text", () => {
  it("formats times and ages, finds the robot", () => {
    expect(clock({ sec: 3600 * 7 + 62, nsec: 250_000_000 })).toBe("07:01:02.250");
    expect(ago(t(100), t(130))).toBe("30 s");
    expect(ago(t(100), t(100 + 600))).toBe("10 min");
    expect(ago(t(0), t(7200))).toBe("2.0 h");
    expect(robotOf({ faults: [fault("x", 40, 2)] }, [])).toBe("amr-7");
    const log = new EventLog();
    log.add({ events: [event("a", 20)] });
    expect(robotOf(undefined, log.all)).toBe("amr-7");
  });
});

describe("History", () => {
  it("shows a recording's events up to the playhead, each once, the last `limit` of them", () => {
    const h = new History();
    h.add({ events: [event("a", 20, 10), event("b", 10, 11)] }, t(10));
    h.add({ events: [event("b", 10, 11), event("c", 40, 20)] }, t(20)); // b again: a seek's backfill
    h.add({ events: [event("d", 30, 30)] }, t(30));
    expect(h.size).toBe(4);
    expect(h.until(t(5), "INFO", 10)).toEqual([]);
    expect(h.until(t(20), "INFO", 10).map((r) => r.id)).toEqual(["a", "c"]);
    expect(h.until(t(20), "DEBUG", 10).map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(h.until(undefined, "INFO", 2).map((r) => r.id)).toEqual(["c", "d"]);
    const shown = h.until(t(30), "INFO", 10);
    expect(shown[shown.length - 1]?.at).toEqual(t(30)); // where a click seeks
  });
});
