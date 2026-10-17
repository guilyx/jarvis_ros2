# Rules and recovery cookbook

Recipes for the things robots need: restart what hangs, call a human when that doesn't hold,
notice what *didn't* happen, act on a value, and recover in steps that verify themselves. Copy a
recipe into your rules or plans directory (`rules:=` and `plans:=` launch arguments), change the
codes and names to yours, then check it:

```bash
ros2 jarvis lint --rules my_rules/ --plans my_plans/ --catalog my_faults.yaml
ros2 jarvis backtest ~/.local/share/jarvis/bags/jarvis_<stamp> --rules my_rules/ --plans my_plans/
```

`lint` parses everything and cross-checks rules against plans (unknown plan, a plan that refuses
rules, missing or unknown arguments) and the catalog. `backtest` replays a recording through the
same engine the robot runs and lists every decision. Every recipe on this page is linted in CI,
next to the shipped plans (`core/jarvis_core/test/test_tooling.cpp`, `EveryCookbookRecipeIsClean`):
no errors, no warnings.

The language in full is [05 · Rules and recovery](../design/05-rules-and-recovery.md). The
[gotchas](#gotchas) at the end are the ones people hit.

- [Rules](#rules)
  1. [Restart a node whose topic went silent](#1-restart-a-node-whose-topic-went-silent)
  2. [Call a human when restarts don't hold](#2-call-a-human-when-restarts-dont-hold)
  3. [A burst: N events in a window](#3-a-burst-n-events-in-a-window)
  4. [Something that should have happened didn't](#4-something-that-should-have-happened-didnt)
  5. [Two signals together, or either one](#5-two-signals-together-or-either-one)
  6. [Act on a value](#6-act-on-a-value)
  7. [Diagnostics to recovery](#7-diagnostics-to-recovery)
  8. [Shadow mode first](#8-shadow-mode-first)
  9. [Page the right people](#9-page-the-right-people)
- [Plans](#plans)
  10. [An escalation ladder for Nav2](#10-an-escalation-ladder-for-nav2)
  11. [A plan that waits for a human](#11-a-plan-that-waits-for-a-human)
  12. [One at a time: locks and queues](#12-one-at-a-time-locks-and-queues)
  13. [What the agent may do](#13-what-the-agent-may-do)
- [Gotchas](#gotchas)

## Rules

A rule is `when` (a pattern over the event stream), an optional `guard` (the state of the robot
when it fires), `then` (actions) and a `policy` (how often). Faults reach rules as `FAULT_STATE`
events: `attrs.state` is `PENDING`, `ACTIVE`, `LATCHED` or `CLEARED`, and the attributes of the
report that raised the fault come along (`attrs.topic`, `attrs.node`, ...). Match `ACTIVE`, the
confirmed fault, not the raw `FAULT_RAISE` reports: the catalog's debounce has decided by then.

### 1. Restart a node whose topic went silent

The topic watchdog raises `ros.topic.stale` with `attrs.topic`; the driver that publishes it is
yours to name. The shipped `restart-lifecycle-node` plan cycles a managed node back to active and
waits for the fault to clear.

```yaml
schema: jarvis.rules/v1
rules:
  - id: lidar-silent-restart
    description: /scan went silent; restart the lidar driver.
    when:
      event: { kind: FAULT_STATE, code: ros.topic.stale, attrs.state: ACTIVE, attrs.topic: /scan }
    guard: { faults_inactive: ["safety.*"] }        # never while an e-stop or the like is on
    then:
      - recover:
          plan: restart-lifecycle-node
          args: { node: /lidar_driver, component: "{{ event.source.component }}", fault: ros.topic.stale }
    policy: { cooldown: 30s, max_fires: { count: 3, per: 1h } }
```

- `cooldown` is per partition (here the rule has one); `max_fires` caps the attempts. The plan
  has its own `max_attempts` too: whichever is lower wins.
- A guard that blocks a fire doesn't use up the cooldown: the next match tries again.
- Watch the topic in `jarvis.yaml` (the topic watchdog watches nothing by default): see the
  [integration guide](integration.md#3-let-the-bridges-watch).

### 2. Call a human when restarts don't hold

When a rule goes over `max_fires`, the engine emits `jarvis.rule.suppressed` (once per window)
with `attrs.rule`, in the incident of the match. Another rule turns that into a page:

```yaml
schema: jarvis.rules/v1
rules:
  - id: lidar-restarts-exhausted
    description: The lidar needed three restarts within the hour; get someone to look at it.
    when:
      event: { kind: SYSTEM, code: jarvis.rule.suppressed, attrs.rule: lidar-silent-restart }
    then:
      - incident: { escalate: true }
      - notify: { channel: operators, message: "{{ robot }}: the lidar keeps going silent, restarts are not holding" }
```

`incident: { escalate: true }` moves the incident of the match to `ESCALATED`. The shipped rule
`escalation-notify` already pages `operators` when a *plan* escalates (its `on_failure` ends in
`escalate`); this one covers the rule giving up.

### 3. A burst: N events in a window

`count` fires on the `threshold`-th match within `within`, then starts counting again.
`partition_by` keeps one count per component, so one noisy node doesn't trip it for another.

```yaml
schema: jarvis.rules/v1
rules:
  - id: warning-storm
    description: Twenty warnings from one component within a minute; ask the agent why.
    partition_by: [source.component]
    when:
      count: { event: { kind: LOG, code: ros.log.warn }, threshold: 20, within: 1m }
    then:
      - incident: { open: true }
      - ask_agent: { focus: "Why does {{ partition.source.component }} keep warning?" }
    policy: { cooldown: 15m }
```

`incident: { open: true }` makes it an incident people see and track, though no single warning is
a fault: the incident opens on the component (faults that follow on it join it), and an operator
closes it. Matched on a fault below the incident threshold, it opens that fault's incident
instead; inside an incident already, it changes nothing. `ask_agent` starts an analysis
(`jarvis.agent.request`); the insight shows up in the dashboard and on the incident.
Errors don't need this: the rosout bridge turns sustained `ERROR` logs into `ros.log.error`
faults on its own (debounced in the catalog: 3 within 10 s).

### 4. Something that should have happened didn't

`absent` is a step of a `sequence`: after the previous step, nothing matching may arrive for
`for`. Here the robot docks and the charger should report charging within a minute. A rule can
raise a fault of its own with `emit`, and clear it with another.

```yaml
schema: jarvis.rules/v1
rules:
  - id: docked-not-charging
    description: The robot docked but is not charging a minute later.
    when:
      sequence:
        within: 90s
        steps:
          - id: docked
            event: { kind: STATE, code: power.dock, attrs.value: DOCKED }
          - id: charging
            absent: { kind: STATE, code: power.charging, attrs.value: true }
            for: 60s
    then:
      - emit: { kind: FAULT_RAISE, code: power.charging.failed, severity: ERROR, message: "Docked, not charging after 60 s" }
      - notify: { channel: operators, message: "{{ robot }} docked but is not charging" }
  - id: charging-again
    when: { event: { kind: STATE, code: power.charging, attrs.value: true } }
    then:
      - emit: { kind: FAULT_CLEAR, code: power.charging.failed, message: charging }
```

- The robot reports both with its reporter: `jarvis.state("power.dock", "DOCKED")` and
  `jarvis.state("power.charging", True)`. `state` only sends when the value changes.
- `absent` can't be the first step, and `for` must be shorter than `within`. A sequence that ends
  in `absent` fires when the `for` runs out.
- Put `power.charging.failed` in your catalog (description, severity, a runbook) like any other.

### 5. Two signals together, or either one

`all` fires once each of its patterns has matched within `within`, in any order. `any` fires on
the first of its patterns to complete. (`nav.localization.lost` and `nav.odom.imu_mismatch` stand
for your own monitors' faults; the action status bridge reports every aborted goal as a
`FAULT_RAISE`, which is what `count` counts here.)

```yaml
schema: jarvis.rules/v1
rules:
  - id: wheels-slipping
    description: Odometry disagrees with the IMU while the drive draws high current.
    when:
      all:
        within: 10s
        of:
          - event: { kind: FAULT_STATE, code: nav.odom.imu_mismatch, attrs.state: ACTIVE }
          - event: { kind: STATE, code: drive.current_a, attrs.value: { gt: 12 } }
    then:
      - ask_agent: { focus: "Is the robot stuck or slipping?" }
    policy: { cooldown: 5m }

  - id: localization-trouble
    description: Localization lost, or three goals aborted within five minutes.
    when:
      any:
        - event: { kind: FAULT_STATE, code: nav.localization.lost, attrs.state: ACTIVE }
        - count:
            event: { kind: FAULT_RAISE, code: ros.action.navigate_to_pose.aborted }
            threshold: 3
            within: 5m
    guard: { faults_inactive: ["safety.*"] }
    then:
      - recover: { plan: relocalize }
    policy: { cooldown: 2m, max_fires: { count: 3, per: 1h } }
```

### 6. Act on a value

Matchers compare: `gt`, `gte`, `lt`, `lte`, `in`, `not`, `glob`, `regex`, `exists`. A `state`
guard tests the latest value of a `STATE` code at the time the rule fires.

```yaml
schema: jarvis.rules/v1
rules:
  - id: battery-low-go-dock
    description: Battery below 15 % and not on the dock; drive to the dock.
    when:
      event: { kind: STATE, code: power.battery_pct, attrs.value: { lt: 15 } }
    guard:
      faults_inactive: ["safety.*", "nav.localization.lost"]
      state: { power.dock: { not: DOCKED } }        # true too when power.dock was never reported
    then:
      - recover: { plan: go-to-dock }
    policy: { cooldown: 10m, max_fires: { count: 2, per: 1h } }
```

```yaml
schema: jarvis.plans/v1
plans:
  - id: go-to-dock
    description: Drive to the dock and wait until the robot reports it docked.
    allowed_requesters: [RULE, OPERATOR]
    preconditions: { faults_inactive: ["safety.*"] }
    locks: [nav2]
    max_attempts: { count: 3, per: 1h }
    timeout: 4m
    steps:
      - id: drive
        action: nav2.navigate_to_pose
        with: { pose: { x: 0.0, y: 0.0, yaw: 3.14 }, frame: map }     # your dock's approach pose
        timeout: 3m
      - id: docked
        action: wait_for
        with: { event: { kind: STATE, code: power.dock, attrs.value: DOCKED } }
        timeout: 30s
    on_failure:
      - escalate: operator
```

`nav2.*` actions need the Nav2 actuator: add `jarvis_nav2::Nav2Actuator` to `actuators` of
`/jarvis/recovery` in your `jarvis.yaml` (lint checks against it by default, the recovery node
loads only what you list).

### 7. Diagnostics to recovery

The diagnostics bridge turns every non-OK `diagnostic_msgs` status into a fault
`diag.<status name>`, lowercased with `/`, `:` and `.` turned into dots (`motors/left` →
`diag.motors.left`), on the component of its `hardware_id`. Its key-values come along as
attributes, next to `diag.name`, `diag.hardware_id` and `diag.level`.

```yaml
schema: jarvis.rules/v1
rules:
  - id: motor-driver-reset
    description: A motor driver reports an error; reset that driver.
    partition_by: [source.component]
    when:
      event: { kind: FAULT_STATE, code: "diag.motors.*", attrs.state: ACTIVE, severity: ">=ERROR" }
    guard: { faults_inactive: ["safety.*"] }
    then:
      - recover:
          plan: reset-motor-driver
          args: { driver: "{{ event.source.component }}" }
    policy: { cooldown: 1m, max_fires: { count: 3, per: 1h } }
```

```yaml
schema: jarvis.plans/v1
plans:
  - id: reset-motor-driver
    description: Reset a motor driver through its Trigger service, then wait for its fault to clear.
    params:
      driver: { type: string, required: true, one_of: [motor_left, motor_right] }
    locks: ["motor:{{ params.driver }}"]
    preconditions: { faults_inactive: ["safety.*"] }
    max_attempts: { count: 3, per: 1h }
    timeout: 1m
    steps:
      - id: reset
        action: service.trigger
        with: { service: "/{{ params.driver }}/reset" }
        timeout: 5s
        retry: { attempts: 1, backoff: 2s }
      - id: verify
        action: wait_for
        with: { fault: { code: "diag.motors.*", component: "{{ params.driver }}" }, state: CLEARED }
        timeout: 20s
```

`one_of` keeps a rule (or a model) from asking for a driver that doesn't exist: the request is
rejected at the plan gate.

### 8. Shadow mode first

`dry_run: true` makes a rule decide without acting: its matches are recorded (`dry_run: true` on
the `RULE_MATCH`), counted as "shadow" fires in the dashboard's Rules view, and nothing runs.
Leave a new rule there for a few days of real operation, look at what it would have done, then
switch it live (Rules view, or `ros2 jarvis rules dry-run <id> --off`).

```yaml
schema: jarvis.rules/v1
rules:
  - id: nav2-server-failed
    description: A Nav2 server's lifecycle transition failed; reset the Nav2 stack.
    dry_run: true
    partition_by: [source.component]
    when:
      event:
        kind: FAULT_STATE
        code: ros.lifecycle.failed
        attrs.state: ACTIVE
        source.component: { in: [controller_server, planner_server, bt_navigator, behavior_server] }
    then:
      - recover: { plan: nav2-reset }
    policy: { cooldown: 60s, max_fires: { count: 2, per: 1h } }
```

Shadow fires count against `cooldown` and `max_fires` like live ones, so what you see in shadow
mode is what the rule will do live. To rehearse a whole deployment instead of one rule, start
Jarvis with `recovery_mode:=DRY_RUN`: every gate runs, no step moves anything.

### 9. Page the right people

`notify` emits `jarvis.notify` on a channel; the gateway delivers it to every webhook subscribed
to that channel (or to `*`), as Slack messages or JSON (`webhooks:` in `gateway.yaml`).

```yaml
schema: jarvis.rules/v1
rules:
  - id: estop-pressed
    description: Tell the safety channel the moment an e-stop engages.
    when:
      event: { kind: FAULT_STATE, code: safety.estop.engaged, attrs.state: { in: [ACTIVE, LATCHED] } }
    then:
      - notify: { channel: safety, message: "{{ robot }}: e-stop engaged ({{ event.message }})" }
    policy: { cooldown: 1m }
```

```yaml
# gateway.yaml
webhooks:
  - { name: safety-team, url: https://hooks.slack.com/services/..., channels: [safety], format: slack }
  - { name: on-call, url: https://pager.example/hook, channels: [operators, safety] }
```

## Plans

A plan is steps behind gates: the mode, who may ask (`allowed_requesters`), its
`preconditions`, a rate limit (`max_attempts`), `locks`, and `requires_approval`. A step is an
action with arguments, a timeout and retries; the last steps should check that the fix worked
(`wait_for` or `check`: lint warns about a plan that never verifies its effect). When a plan
fails, its `on_failure` ladder runs: other plans, then `escalate`.

### 10. An escalation ladder for Nav2

Reset Nav2; if that fails, stop the robot; if anything fails, a human is paged once, however deep
the ladder went.

```yaml
schema: jarvis.plans/v1
plans:
  - id: nav2-reset
    description: Reset and restart the Nav2 stack through its lifecycle manager.
    locks: [nav2]
    agent_policy: supervised
    preconditions: { faults_inactive: ["safety.*"] }
    max_attempts: { count: 2, per: 1h }
    timeout: 2m
    steps:
      - { id: reset, action: nav2.manage_lifecycle, with: { manager: /lifecycle_manager_navigation, command: RESET }, timeout: 20s }
      - { id: startup, action: nav2.manage_lifecycle, with: { manager: /lifecycle_manager_navigation, command: STARTUP }, timeout: 30s }
      - id: verify
        action: wait_for
        with: { fault: { code: "ros.lifecycle.*" }, state: CLEARED }
        timeout: 30s
    on_failure:
      - run: { plan: safe-stop }
      - escalate: operator

  - id: safe-stop
    description: Cancel every navigation goal and check that none is executing.
    agent_policy: supervised
    timeout: 30s
    steps:
      - { id: cancel, action: nav2.cancel_navigation, timeout: 5s }
      - id: stopped
        action: check
        with:
          state:
            ros.action.navigate_to_pose.status: { not: EXECUTING }
            ros.action.navigate_through_poses.status: { not: EXECUTING }
        timeout: 2s
        retry: { attempts: 5, backoff: 1s }
    on_failure:
      - escalate: operator

  - id: relocalize
    description: Re-initialise global localization and wait for it to settle.
    agent_policy: supervised
    locks: [nav2]
    timeout: 1m
    steps:
      - { id: reinit, action: service.empty, with: { service: /reinitialize_global_localization }, timeout: 5s }
      - id: verify
        action: wait_for
        with: { fault: { code: nav.localization.lost }, state: CLEARED }
        timeout: 30s
    on_failure:
      - run: { plan: safe-stop }
      - escalate: operator
```

- A ladder entry that succeeds stops the ladder (recovered); one that is aborted stops it too (a
  human or the kill switch stepped in). `escalate` happens once per chain.
- `run` entries and `run` steps start the plan as requester `PLAN`, which a plan accepts unless
  its `allowed_requesters` leaves it out.
- `check` with `retry` polls. The action status bridge keeps `ros.action.<action>.status`
  current, and a `not` test also holds for a state that was never reported.

### 11. A plan that waits for a human

`requires_approval` lists the requesters whose runs wait for an operator's approval (5 minutes by
default, then `EXPIRED`). Operators running it themselves don't wait; a rule asking does.

```yaml
schema: jarvis.plans/v1
plans:
  - id: power-cycle-lidar
    description: Cut the lidar's power for 3 s through its relay, then wait for scans.
    allowed_requesters: [RULE, OPERATOR, AGENT]
    requires_approval: [RULE, AGENT]
    preconditions: { faults_inactive: ["safety.*"] }
    max_attempts: { count: 2, per: 1h }
    timeout: 1m
    steps:
      - { id: off, action: service.set_bool, with: { service: /power/lidar, data: false }, timeout: 5s }
      - { id: wait, action: delay, with: { duration: 3s } }
      - { id: on, action: service.set_bool, with: { service: /power/lidar, data: true }, timeout: 5s }
      - id: scans
        action: wait_for
        with: { fault: { code: ros.topic.stale, component: lidar }, state: CLEARED }
        timeout: 30s
    on_failure:
      - escalate: operator
```

The approval request shows in the dashboard (Overview, Recovery → Pending approval, the bell)
and through `ros2 jarvis executions`; `ros2 jarvis approve <id>` or the card's buttons decide.
Every gate is checked again after the approval: a precondition that went false meanwhile still
rejects the run.

### 12. One at a time: locks and queues

Two plans holding the same lock never run together. `on_conflict: queue` waits for the lock
(`queue_ttl`, then `EXPIRED`) instead of rejecting; a sub-plan shares its parent's locks.

```yaml
schema: jarvis.plans/v1
plans:
  - id: clear-costmaps
    description: Clear both costmaps, after any Nav2 recovery in progress.
    locks: [nav2]
    on_conflict: queue
    queue_ttl: 1m
    agent_policy: autonomous
    timeout: 1m
    steps:
      - { id: clear, action: nav2.clear_costmaps, with: { which: both }, timeout: 5s }
      - id: settled
        action: check
        with: { faults_inactive: [ros.action.compute_path_to_pose.aborted] }
        timeout: 2s
        retry: { attempts: 5, backoff: 2s }
```

Locks are templates: `"node:{{ params.node }}"` locks one node, so restarts of different nodes
run side by side.

### 13. What the agent may do

The agent asks for recoveries as requester `AGENT`, through the same gates as everyone. What it
gets is the lowest of three settings:

| Where | Setting | Values |
|-------|---------|--------|
| the plan | `agent_policy` (default `advise`) | `never`, `advise` (recommend only), `supervised` (run after an operator approves), `autonomous` |
| the robot | `agent_autonomy` of `/jarvis/recovery` (default `supervised`) and `min_confidence` (0.8) | same |
| the agent | `autonomy.level` in `agent.yaml` (default `advise`) and `min_confidence` (0.7) | `observe`, `advise`, `supervised`, `autonomous` |

An `autonomous` request below the robot's `min_confidence` waits for approval instead. Keep
`autonomous` for plans that can't make things worse (clear costmaps, a snapshot); `allowed_requesters`
without `AGENT` keeps the agent out of a plan entirely.

## Gotchas

- **Quote what must stay text.** YAML reads `attrs.value: true` as a boolean and `attrs.id: 007`
  as a number; numbers compare as numbers, everything else as text. `"007"` is the string.
- **`*` crosses dots.** `ros.topic.*` matches `ros.topic.stale` and `ros.topic.a.b`. Globs are
  case-sensitive, like codes.
- **`regex` searches.** `{ regex: "lidar" }` matches `front_lidar_2`; anchor it (`^lidar$`) to
  match the whole value. The syntax is ECMAScript.
- **No template filters.** `{{ event.attrs.node }}` is all there is: no `| upper`, no
  conditions. The variables: `event.<field>`, `steps.<id>.<field>` (sequences), `count`,
  `partition.<field>` (fields listed in `partition_by`), `rule.id` and `robot`. Lint rejects any
  other. A template that renders empty at fire time skips that action and emits
  `jarvis.rule.action_failed`.
- **`severity` is a floor test only with an operator**: `severity: ERROR` is exactly ERROR,
  `severity: ">=ERROR"` is ERROR or CRITICAL.
- **A rule ignores the events it caused**, so a rule that emits what it matches does not loop.
- **Shadow fires count.** A rule in `dry_run` uses up its `cooldown` and `max_fires` and can be
  suppressed, as it would live.
- **Lint checks the plans, not the robot.** It doesn't know whether `/lidar_driver` exists, and it
  doesn't check an actuator's `with` arguments: the recovery node checks those when it loads the
  plans (and refuses a plan it can't run). `lint --actuators ...` checks against the actuators you
  actually load.
- **Lint stops at the first parse error of a directory**: fix it and run it again.
- **One directory each.** `--rules` and `--plans` take one directory; ids must be unique across
  its files.
