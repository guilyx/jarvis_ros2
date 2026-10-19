# Behavior trees

Many teams already write their recoveries as behavior trees (BehaviorTree.CPP, as Nav2 does).
`jarvis_bt` runs them as a recovery step, `bt.run`. A tree then gets what every Jarvis plan has:
the gates (mode, requester, preconditions, rate limits), locks, approvals, timeouts, escalation and
the audit trail. And the dashboard says which branch of the tree failed, and why.

`jarvis_bt` also gives trees nodes that call any service or action, and nodes that read Jarvis's
open faults. A robot's own trees can load them too.

1. [Run a tree from a plan](#1-run-a-tree-from-a-plan)
2. [The Jarvis nodes](#2-the-jarvis-nodes)
3. [Your trees, your nodes, Nav2's nodes](#3-your-trees-your-nodes-nav2s-nodes)
4. [What the step says](#4-what-the-step-says)
5. [Try it: the behavior-tree demo](#5-try-it-the-behavior-tree-demo)
6. [Jarvis's nodes in your robot's own trees](#6-jarvis-nodes-in-your-robots-own-trees)

## 1. Run a tree from a plan

Load the actuator and tell it where your trees are. `bt.run` only runs trees from that directory.
The actuators list replaces the shipped one, so keep the built-in actuators in it:

```yaml
/jarvis/recovery:
  ros__parameters:
    actuators:
      - jarvis_ros::LifecycleActuator
      - jarvis_ros::ServiceActuator
      - jarvis_ros::GenericActuator
      - jarvis_ros::ParamActuator
      - jarvis_ros::BagActuator
      - jarvis_ros::ProcessActuator
      - jarvis_bt::TreeActuator         # bt.run
    bt:
      trees_dir: /etc/jarvis/trees      # nothing outside it runs
      plugins: []                       # BT plugin libraries your trees use (section 3)
      tick_hz: 20.0
```

A plan step runs a tree. Its `blackboard` entries are what the tree reads as `{name}`; they are
rendered like any step argument, so they can come from the plan's parameters:

```yaml
- id: restart-with-tree
  params:
    node: { type: string, required: true, pattern: "^/" }
    component: { type: string, required: true }
  locks: ["node:{{ params.node }}"]
  timeout: 90s
  steps:
    - id: tree
      action: bt.run
      with:
        tree: restart_lifecycle_node.xml      # a file in bt.trees_dir
        id: RestartLifecycleNode              # optional: the file's main tree by default
        blackboard:
          change_state: "{{ params.node }}/change_state"
          fault: sensor.scan.stale
          component: "{{ params.component }}"
          wait: 4
      timeout: 60s                            # halts the tree
    - id: verify                              # the plan checks, whatever the tree did
      action: wait_for
      with: { fault: { code: sensor.scan.stale, component: "{{ params.component }}" }, state: CLEARED }
      timeout: 15s
  on_failure:
    - escalate: operator
```

The recovery node checks every `bt.run` step when it loads the plans. A tree file that isn't in
`bt.trees_dir`, an XML error, a node type no library provides, or an unknown `id` fails the load,
before anything runs. A tree named through a template is checked when it runs. The tree is ticked
`bt.tick_hz` times a second. `SUCCESS` succeeds the step, and `FAILURE` fails it, so the plan's
`retry` and `on_failure` apply as for any step.

`jarvis_bt` ships `restart_lifecycle_node.xml` (in its `share/jarvis_bt/trees`). It restarts a
lifecycle node gently first (deactivate, activate). If the fault is still open after `wait`
seconds, it restarts it all the way (cleanup, configure, activate).

## 2. The Jarvis nodes

| Node | Ports | Does |
|------|-------|------|
| `JarvisCallService` | `service`, `type`, `request`, `expect`, `server_timeout` (5 s), `timeout` (30 s); out: `response` | Calls any service of the type named (`pkg/srv/Name`). SUCCESS when the response holds `expect` (default `success: true`, when the response has a `success`) |
| `JarvisSendGoal` | `action`, `type`, `goal`, `expect`, `server_timeout` (5 s), `timeout` (0: none); out: `result` | Sends a goal to any action (`pkg/action/Name`). SUCCESS when it succeeds and its result holds `expect`. Halting the node cancels the goal |
| `JarvisFaultActive` | `code` (globs), `component`, `topic`, `snapshot_timeout` (2 s) | SUCCESS if Jarvis has such a fault open (ACTIVE or LATCHED), else FAILURE |
| `JarvisWaitFaultCleared` | `code` (globs), `component`, `topic`, `timeout` (10 s) | RUNNING until no such fault is open, then SUCCESS. FAILURE after `timeout` |

`request`, `goal` and `expect` are YAML (or JSON) in the XML: `request="{transition: {label:
activate}}"`. BehaviorTree.CPP reads `{name}` as a blackboard entry. The Jarvis nodes do too when
that entry is set, and otherwise take the braces as YAML, so both forms work:

```xml
<JarvisSendGoal action="/dock_robot" type="nav2_msgs/action/DockRobot" goal="{dock_id: home}"/>
<JarvisSendGoal action="/dock_robot" type="nav2_msgs/action/DockRobot" goal="{dock_goal}"/>
```

The types are loaded by name at run time, as for the `service.call` and `action.goal` steps, so
there is no code to compile for a new service.

## 3. Your trees, your nodes, Nav2's nodes

A tree may use:
- BehaviorTree.CPP's own nodes (Sequence, Fallback, RetryUntilSuccessful, Timeout, Sleep, …);
- the Jarvis nodes;
- the nodes of any BehaviorTree.CPP plugin library listed in `bt.plugins`, by name
  (`nav2_clear_costmap_service_bt_node`) or by path.

Like Nav2's BT navigator, `bt.run` puts these on the tree's blackboard:
- `node`: an `rclcpp::Node` of the tree's own, named `<recovery node>_bt`;
- `server_timeout`, `bt_loop_duration` and `wait_for_service_timeout`.

So plugin nodes written to Nav2's conventions, which read these entries, find them.

## 4. What the step says

Give the branches that matter a `name` in the XML (`<Sequence name="Reactivate">`). The step's
message says what each named node did, in order, and why the last failure failed:

```
RestartLifecycleNode succeeded in 4.5 s: Reactivate FAILURE (sensor.scan.stale (lidar): ACTIVE,
still, after 4 s), Reconfigure SUCCESS
```

That message is in the dashboard (the execution's steps), on the event stream (the step's
`RECOVERY` event) and in `ros2 jarvis executions --steps`. The recovery node also logs each named
node as it finishes (`bt RestartLifecycleNode: Reactivate FAILURE: ...`).

## 5. Try it: the behavior-tree demo

```bash
ros2 launch jarvis_demo trees.launch.py
ros2 run jarvis_demo inject scan-wedge        # the relay hangs; only a cleanup cures it
```

The rule `lidar-stale-tree` asks for `restart-with-tree`, which runs the shipped tree on the relay.
Reactivating it does not cure a wedge, so the tree's `Reactivate` branch fails: the fault is still
open after 4 s. The `Reconfigure` branch cleans the relay up, configures it and activates it, and
the fault clears. The demo's launch test (`test_trees.launch.py`) checks exactly that.

## 6. Jarvis's nodes in your robot's own trees

`jarvis_bt_nodes` is a BehaviorTree.CPP plugin library with the four nodes. For example, Nav2's
`bt_navigator` can load it:

```yaml
bt_navigator:
  ros__parameters:
    plugin_lib_names: [jarvis_bt_nodes]
```

A navigation tree can then check Jarvis before it acts. For example, don't start docking while
the charger reports a fault:

```xml
<Inverter><JarvisFaultActive code="power.charger.*"/></Inverter>
```

The nodes take their ROS node from the blackboard entry `node` (Nav2 sets it) and spin a callback
group of their own while they wait, whoever ticks the tree.

In C++, include `jarvis_bt/nodes.hpp` before any BehaviorTree.CPP header.
BehaviorTree.CPP bundles its own copy of nlohmann/json under the same include guard as the system
one, so whichever comes first wins. `jarvis_bt` builds with the system copy, so `nodes.hpp`
includes it first.
