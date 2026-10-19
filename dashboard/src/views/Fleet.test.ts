import { describe, expect, it } from "vitest";
import type { FleetRobot } from "@/api/types";
import { rollout } from "./Fleet";

const robot = (name: string, config: FleetRobot["config"], online = true): FleetRobot => ({
  name,
  url: `http://${name}`,
  online,
  mode: "ENABLED",
  faults: { total: 0 },
  incidents: 0,
  latched: 0,
  config,
});

const cfg = (revision: string, rejected: string | null = null) => ({
  revision,
  state: rejected ? "rejected" : "in-sync",
  source: "s3://fleet/config",
  since: 1,
  rejected,
  error: rejected ? "rules/lidar.yaml: unknown plan 'restart-lidar'" : "",
});

describe("rollout", () => {
  it("groups the robots by the revision they run, and by the one they refused", () => {
    const r = rollout([
      robot("amr-9", cfg("4f1c")),
      robot("amr-7", cfg("9b2e")),
      robot("amr-8", cfg("4f1c", "9b2e")),
      robot("amr-6", cfg("4f1c", "9b2e")),
      robot("amr-5", null),
      robot("amr-4", cfg("1111"), false), // offline: not counted
    ]);
    expect(r?.source).toBe("s3://fleet/config");
    expect(r?.revisions).toEqual([
      { revision: "4f1c", robots: ["amr-6", "amr-8", "amr-9"] },
      { revision: "9b2e", robots: ["amr-7"] },
    ]);
    expect(r?.refused).toEqual([
      {
        revision: "9b2e",
        robots: ["amr-6", "amr-8"],
        error: "rules/lidar.yaml: unknown plan 'restart-lidar'",
      },
    ]);
    expect(r?.own).toEqual(["amr-5"]);
  });

  it("is nothing when no robot takes the fleet's rules and plans", () => {
    expect(rollout([robot("amr-5", null), robot("amr-4", cfg("1111"), false)])).toBeNull();
  });
});
