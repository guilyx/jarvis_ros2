import { describe, expect, it } from "vitest";
import type { IncidentDetail, JarvisEvent } from "@/api/types";
import { buildTimeline } from "./IncidentTimeline";

const ev = (seq: number, kind: JarvisEvent["kind"], code: string, attrs = {}): JarvisEvent => ({
  id: `e${seq}`,
  seq: String(seq),
  stamp: `2026-10-17T09:00:0${seq}Z`,
  source: { robot: "r", node: "/n", component: "lidar", host: "" },
  kind,
  code,
  severity: "INFO",
  attrs,
});

describe("incident timeline", () => {
  it("has a lane per fault and per execution, then one per kind of point", () => {
    const detail: IncidentDetail = {
      incident_id: "i1",
      component: "lidar",
      title: "lidar stalled",
      severity_level: 40,
      opened_at: "2026-10-17T09:00:01Z",
      escalated_at: null,
      resolved_at: "2026-10-17T09:00:08Z",
      closed_at: null,
      escalated: false,
      state: "RESOLVED",
      faults: [
        {
          fault_id: "f1",
          code: "ros.topic.stale",
          component: "lidar",
          severity_level: 40,
          first_seen: "2026-10-17T09:00:01Z",
          confirmed_at: "2026-10-17T09:00:01Z",
          cleared_at: "2026-10-17T09:00:07Z",
          occurrences: 1,
          muted: false,
          incident_id: "i1",
          last_state: "CLEARED",
        },
      ],
      executions: [
        {
          execution_id: "x1",
          plan: "restart-lifecycle-node",
          requester: "RULE",
          requester_id: "lidar-stale-restart",
          dry_run: false,
          parent_id: null,
          ladder_of: null,
          incident_id: "i1",
          created_at: "2026-10-17T09:00:02Z",
          started_at: "2026-10-17T09:00:02Z",
          finished_at: "2026-10-17T09:00:06Z",
          outcome: "SUCCEEDED",
          escalated: false,
          last_phase: "SUCCEEDED",
        },
      ],
      timeline: [
        ev(1, "FAULT_STATE", "ros.topic.stale", { fault_id: "f1", state: "ACTIVE" }),
        ev(2, "RULE_MATCH", "jarvis.rule.matched", { rule: "lidar-stale-restart" }),
        ev(3, "OPERATOR", "fault.acknowledge", { user: "olga" }),
        ev(7, "FAULT_STATE", "ros.topic.stale", { fault_id: "f1", state: "CLEARED" }),
        ev(8, "SYSTEM", "jarvis.incident.resolved", { state: "RESOLVED" }),
      ],
    };
    const { lanes, bars, points } = buildTimeline(detail);
    expect(lanes).toEqual([
      "⚠ ros.topic.stale · lidar",
      "↻ restart-lifecycle-node",
      "◆ Rules",
      "● Operator",
      "▲ Incident",
    ]);
    expect(bars).toHaveLength(2);
    expect(bars[1]!.link).toEqual({ type: "execution", id: "x1" });
    expect(bars[1]!.value[2]! - bars[1]!.value[1]!).toBe(4000);
    expect(points.filter((p) => p.value[1] === 0)).toHaveLength(2); // the fault's transitions
  });
});
