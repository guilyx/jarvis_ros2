# Watching a fleet

Every robot keeps its own Jarvis and gateway: its faults, rules, recoveries and recordings stay on
the robot, and work without a network. To see several robots at once, one gateway lists the others,
and its dashboard's **Fleet** view shows them side by side, the ones that need someone first; a
click opens a robot's own dashboard.

1. [List the robots](#1-list-the-robots)
2. [What the fleet view shows](#2-what-the-fleet-view-shows)
3. [Where to run it](#3-where-to-run-it)

## 1. List the robots

In the `gateway.yaml` of the gateway you will look at:

```yaml
schema: jarvis.gateway/v1
fleet:
  - { name: amr-2, url: http://amr-2.local:8080 }
  - { name: amr-3, url: https://amr-3.local:8443, token_env: JARVIS_AMR3_TOKEN }
```

- `url`: the robot's gateway, as a browser on your network reaches it (the dashboard's links go
  there). Its gateway must listen on that address (`gateway_host:=0.0.0.0` or the robot's LAN
  address), not only on 127.0.0.1.
- `token_env`: for a robot that wants a token, the environment variable that holds one. A viewer's
  token is enough: the fleet view only reads.

```bash
export JARVIS_AMR3_TOKEN=...      # a viewer token of amr-3's gateway
ros2 launch jarvis_bringup jarvis.launch.py gateway_config:=/etc/jarvis/gateway.yaml
```

The same robots are in `GET /api/v1/fleet`, for scripts and other tools.

## 2. What the fleet view shows

One card per robot, this one included, worst first: offline, then by their worst open fault.

| On a card | From the robot's gateway |
|-----------|--------------------------|
| Online, and how long it took to answer | Its health; offline with why (unreachable, no answer, refused the token) |
| Open faults by severity, incidents | Its open faults (muted symptoms left out) |
| The worst fault: code, component, message | The most severe, a latched one first |
| Recovery mode | `ENABLED`, `DRY_RUN` or `DISABLED`: is Jarvis allowed to act there |

The gateway asks every robot when the view refreshes (every 5 s), all at once, and gives each 3 s
(`fleet: {robots: [...], timeout_s: 3}` to change it). Nothing is copied or kept: close the view and
the robots aren't asked anymore.

## 3. Where to run it

Any gateway can list the others: one robot's, or one on an operations machine (`ros2 run
jarvis_gateway jarvis-gateway --config gateway.yaml` next to a Jarvis, or the Docker image).
Whoever can see the fleet view sees every robot's open faults: give that gateway the same tokens,
TLS and single sign-on as a robot's ([security guide](security.md)).

The fleet view reads; it does not act. To clear a fault or run a recovery, open the robot's
dashboard: commands go to that robot's gateway, under your name in its audit trail.
