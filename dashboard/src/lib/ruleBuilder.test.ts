import { describe, expect, it } from "vitest";
import { defaultArgs, newRule, problems, ruleYaml, scalar, slug, type RuleSpec } from "./ruleBuilder";

function lidarRule(): RuleSpec {
  const r = newRule();
  r.id = "lidar-stale-restart";
  r.description = "The lidar went quiet: restart its relay.";
  r.trigger.code = "sensor.scan.stale";
  r.actions = [
    {
      type: "recover",
      plan: "restart-lifecycle-node",
      args: {
        node: "{{ event.attrs.node }}",
        component: "{{ event.source.component }}",
        fault: "sensor.scan.stale",
      },
    },
    { type: "notify", channel: "operators", message: "{{ event.message }}" },
  ];
  return r;
}

describe("ruleYaml", () => {
  it("writes the rule as the cookbook would", () => {
    expect(ruleYaml(lidarRule())).toBe(`schema: jarvis.rules/v1
rules:
  - id: lidar-stale-restart
    description: "The lidar went quiet: restart its relay."
    dry_run: true
    partition_by: [source.component]
    when:
      event: { kind: FAULT_STATE, code: sensor.scan.stale, attrs.state: ACTIVE }
    guard: { faults_inactive: ["safety.*"] }
    then:
      - recover:
          plan: restart-lifecycle-node
          args: { node: "{{ event.attrs.node }}", component: "{{ event.source.component }}", fault: sensor.scan.stale }
      - notify: { channel: operators, message: "{{ event.message }}" }
    policy: { cooldown: 30s, max_fires: { count: 3, per: 1h } }
`);
  });

  it("counts repeats, narrows to a component, and leaves out what is off", () => {
    const r = lidarRule();
    r.trigger = {
      kind: "LOG",
      code: "ros.log.warn",
      state: "ACTIVE",
      component: "lidar",
      repeat: { threshold: 20, within: "1m" },
    };
    r.perComponent = false;
    r.safetyGuard = false;
    r.dryRun = false;
    r.cooldown = "";
    r.maxFires = null;
    r.actions = [
      { type: "incident", op: "open" },
      { type: "ask_agent", focus: "Why does {{ event.source.component }} keep warning?" },
    ];
    expect(ruleYaml(r)).toBe(`schema: jarvis.rules/v1
rules:
  - id: lidar-stale-restart
    description: "The lidar went quiet: restart its relay."
    when:
      count:
        event: { kind: LOG, code: ros.log.warn, source.component: lidar }
        threshold: 20
        within: 1m
    then:
      - incident: { open: true }
      - ask_agent: { focus: "Why does {{ event.source.component }} keep warning?" }
`);
  });

  it("drops empty arguments", () => {
    const r = lidarRule();
    r.actions = [{ type: "recover", plan: "safe-stop", args: { reason: " " } }];
    expect(ruleYaml(r)).toContain("      - recover:\n          plan: safe-stop\n    policy:");
  });
});

describe("scalar", () => {
  it("quotes what YAML would misread", () => {
    expect(scalar("sensor.scan.stale")).toBe("sensor.scan.stale");
    expect(scalar("safety.*")).toBe("safety.*");
    expect(scalar("*")).toBe('"*"');
    expect(scalar("yes")).toBe('"yes"');
    expect(scalar("007")).toBe('"007"');
    expect(scalar("a: b")).toBe('"a: b"');
    expect(scalar("{{ robot }}")).toBe('"{{ robot }}"');
    expect(scalar("")).toBe('""');
    expect(scalar(3)).toBe("3");
  });
});

describe("the form's helpers", () => {
  it("names a rule after its description", () => {
    expect(slug("Restart the lidar relay!")).toBe("restart-the-lidar-relay");
    expect(slug("  ")).toBe("");
  });

  it("prefills a plan's arguments from the event", () => {
    const t = newRule().trigger;
    t.code = "sensor.scan.stale";
    expect(
      defaultArgs(
        {
          node: { required: true },
          component: { required: true },
          fault: { default: "*" },
          speed: { default: 0.5, required: true },
          note: {},
        },
        t,
      ),
    ).toEqual({
      node: "{{ event.attrs.node }}",
      component: "{{ event.source.component }}",
      fault: "sensor.scan.stale",
      speed: "0.5",
    });
  });

  it("says what is missing", () => {
    const r = newRule();
    expect(problems(r)).toEqual([
      "a name: lowercase words joined by dashes",
      "the code that triggers it",
      "at least one thing to do",
    ]);
    const l = lidarRule();
    expect(problems(l)).toEqual([]);
    l.actions = [{ type: "recover", plan: "restart-lifecycle-node", args: { node: "" } }];
    expect(problems(l, () => ["node", "component"])).toEqual(["the plan's node", "the plan's component"]);
  });
});
