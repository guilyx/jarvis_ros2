# Drivers in containers and systemd units

`lifecycle.restart` brings a managed node back. Most drivers aren't managed nodes: a vendor's lidar
driver in a container, a camera daemon under systemd, a CAN bridge started at boot. When one of
them hangs, the fix is to restart its process. Jarvis restarts a container (Docker, or Podman's
compatible socket) or a systemd unit, waits until it is up again, and checks that its faults clear.

1. [Allow what Jarvis may restart](#1-allow-what-jarvis-may-restart)
2. [A rule that restarts a container](#2-a-rule-that-restarts-a-container)
3. [systemd units](#3-systemd-units)
4. [Try it: the container demo](#4-try-it-the-container-demo)
5. [Security](#5-security)

## 1. Allow what Jarvis may restart

Nothing is restartable until the recovery node allows it. In your `jarvis.yaml`, or a small file
layered over the shipped one (`params_overlay:=`):

```yaml
/jarvis/recovery:
  ros__parameters:
    containers:
      allowed: ["lidar-driver", "camera-*"]   # globs of container names
      socket: /var/run/docker.sock            # Podman: /run/podman/podman.sock
    systemd:
      allowed_units: ["can-bridge.service"]   # globs of unit names
```

```bash
ros2 launch jarvis_bringup jarvis.launch.py params_overlay:=/etc/jarvis/restartable.yaml
```

A plan that names a container or unit outside these lists fails to load, and so does a recovery
asked with one through a template: the step fails before anything is restarted.

## 2. A rule that restarts a container

The shipped plan `restart-container` restarts a container, then waits for the fault to clear:

```yaml
schema: jarvis.rules/v1
rules:
  - id: lidar-driver-restart
    description: The lidar went quiet. Restart the driver's container and check that scans flow again.
    when:
      event: { kind: FAULT_STATE, code: sensor.scan.stale, attrs.state: ACTIVE }
    guard: { faults_inactive: [safety.estop.engaged] }
    then:
      - recover:
          plan: restart-container
          args: { container: lidar-driver, component: "{{ event.source.component }}", fault: sensor.scan.stale }
    policy: { cooldown: 30s, max_fires: { count: 3, per: 10m } }
```

`container.restart` (the plan's first step):

- **Thaws a paused container first:** a frozen container can't be restarted otherwise.
- **Restarts it** with `stop_timeout_s` (5) for the process to stop before it is killed.
- **Waits until the container runs.** If it has a `HEALTHCHECK`, it waits until the container is
  healthy. The step's timeout bounds the wait.
- **Its message says what it found:** `container lidar-driver restarted: running, healthy (pid 4321)`.

Write your own plan around it for more: a ladder that restarts the ROS relay first and the driver's
container if that wasn't enough ([cookbook §10](rules-cookbook.md#10-an-escalation-ladder-for-nav2)),
or a `wait_for` on a topic's state.

## 3. systemd units

`restart-systemd-unit` (action `systemd.restart`) runs `systemctl restart`, then waits until
`systemctl is-active` says `active`. If the unit fails, the step fails at once with the reason.
`user: "true"` restarts a unit of the user's service manager (`systemctl --user`).

```yaml
then:
  - recover:
      plan: restart-systemd-unit
      args: { unit: can-bridge.service, component: can, fault: "can.bus.*" }
```

The recovery node needs the right to restart the unit. Give it with a polkit rule for just
those units, rather than running Jarvis as root:

```js
// /etc/polkit-1/rules.d/50-jarvis.rules
polkit.addRule(function (action, subject) {
  if (action.id == "org.freedesktop.systemd1.manage-units" && subject.user == "jarvis" &&
      ["can-bridge.service"].indexOf(action.lookup("unit")) >= 0 &&
      action.lookup("verb") == "restart") {
    return polkit.Result.YES;
  }
});
```

## 4. Try it: the container demo

The demo's lidar driver, in a container: Jarvis restarts the container when the driver hangs.

```bash
# The driver (the demo's fake_lidar) in a container, with Jarvis's install mounted into it
docker run -d --name lidar-driver --network host --ipc host --entrypoint bash \
  -v /opt/ros/jazzy:/opt/ros/jazzy:ro -v $PWD/install:/ws/install:ro ros:jazzy-ros-core \
  -c 'source /opt/ros/jazzy/setup.bash; source /ws/install/setup.bash; exec ros2 run jarvis_demo fake_lidar'

ros2 launch jarvis_demo containers.launch.py     # the demo, without its own lidar
ros2 param set /fake_lidar inject hang           # the driver hangs: only a restart brings it back
```

The relay reports `sensor.scan.stale`, the rule restarts `lidar-driver`, and scans flow again
from the restarted driver. The dashboard shows each step: `container lidar-driver restarted:
running (pid …)`, then `fault sensor.scan.stale is CLEARED`.

`--network host --ipc host`: the driver and Jarvis on one DDS domain, shared memory included.

## 5. Security

- **The Docker socket is root.** Anything that can reach it can start a privileged container. Keep
  `containers.allowed` short, and consider a socket proxy that lets through only what Jarvis uses
  (`GET /containers/{name}/json`, `POST /containers/{name}/restart` and `/unpause`). Then set
  `containers.socket` to the proxy's socket.
- **Who may ask.** Both plans accept rules, operators and the agent. Agent requests need an
  operator's approval by default (`agent_policy: supervised`). Approvals and the kill switch
  apply as to any plan ([operator guide §5](operator.md#5-recoveries-and-approvals)).
- **Every restart is in the audit trail**, with who asked and why.
