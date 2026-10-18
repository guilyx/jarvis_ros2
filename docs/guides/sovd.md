# Jarvis for SOVD clients

The gateway serves Jarvis's faults through a SOVD-style API (Service-Oriented Vehicle Diagnostics,
ISO 17978, the paths and fields [ros2_medkit](https://github.com/selfpatch/ros2_medkit)'s gateway
serves). So a SOVD client, such as the ros2_medkit web UI or its Foxglove panel, lists the robot's
faults, follows them live and clears them. Nothing to install: it is part of the gateway, at
`/sovd`.

1. [Connect a client](#1-connect-a-client)
2. [What it serves](#2-what-it-serves)
3. [How Jarvis maps to SOVD](#3-how-jarvis-maps-to-sovd)
4. [Who may do what](#4-who-may-do-what)

## 1. Connect a client

Give the client the gateway's address followed by `/sovd`: `http://robot.local:8080/sovd`. SOVD
clients add `/api/v1` themselves.

A client that runs in a browser and is served from another address (the ros2_medkit web UI on its
own port, say) needs the gateway to allow its origin, in `gateway.yaml`:

```yaml
schema: jarvis.gateway/v1
cors_origins: [http://localhost:3000]
```

To try it with the demo (`./demo up`, whose gateway allows that origin and lets anyone clear):
run the ros2_medkit web UI on port 3000 (`docker run -p 3000:80
ghcr.io/selfpatch/ros2_medkit_web_ui`), open http://localhost:3000 and connect to
`http://localhost:8080/sovd`.

Check it from the command line:

```bash
curl -s localhost:8080/sovd/version-info                # {"sovd_info": [{"version": "1.0.0", ...}]}
curl -s -H "Authorization: Bearer $TOKEN" localhost:8080/sovd/api/v1/faults
```

## 2. What it serves

| Path (under `/sovd/api/v1`) | What |
|------|------|
| `GET /`, `/health`, `/version-info` (and `GET /sovd/version-info`) | Discovery |
| `GET /areas`, `/functions` | Empty: Jarvis has no areas or functions |
| `GET /components[/{id}]`, `/components/{id}/hosts` | The robot, and the apps on it |
| `GET /apps[/{id}]` | The components that have faults |
| `GET /faults?status=pending,confirmed,cleared,all&include_muted=true` | Every fault (open ones by default) |
| `GET /faults/stream` | Server-sent events: `fault_confirmed`, `fault_updated`, `fault_cleared` |
| `GET /{components,apps}/{id}/faults[/{code}]` | An entity's faults; one in detail, with its status bits and freeze frame |
| `DELETE /{components,apps}/{id}/faults[/{code}]` | Clears them (operator) |
| `GET /{components,apps}/{id}/{data,operations,configurations}` | Empty: Jarvis serves faults only |

Errors are SOVD's: `{"error_code": "resource-not-found", "message": ..., "parameters": {...}}`.

## 3. How Jarvis maps to SOVD

| Jarvis | SOVD |
|--------|------|
| The robot (`robot_id`) | A component |
| A fault's `source.component` (`lidar_front`) | An app on it, its id in lowercase letters, digits and dashes (`lidar-front`) |
| A fault's code (`sensor.scan.stale`) | The fault code, as it is |
| Severity DEBUG, INFO / WARN / ERROR / CRITICAL | 0 / 1 / 2 / 3 |
| State PENDING / ACTIVE, LATCHED / CLEARED | `PREFAILED` / `CONFIRMED` / `CLEARED`; in detail, the status bits (`aggregatedStatus` passive / active / cleared, `testFailed`, `confirmedDTC`, `pendingDTC`) |
| Message | `description` (list), `fault_name` (detail) |
| Freeze frame | A `freeze_frame` snapshot |
| Fault id, incident, root cause, runbook | Under `x-jarvis` |

Muted symptoms are left out unless `include_muted=true`. Cleared faults come from the history (the
lake). Jarvis does not tell a fault that healed from one an operator cleared: `status=healed` and
`status=cleared` return the same.

## 4. Who may do what

The gateway's tokens and roles apply, as for its own API ([security guide](security.md)): reading
needs a viewer, clearing an operator. A clear goes through Jarvis like one from the dashboard:
recorded in the audit trail under the caller's name, the root cause's symptoms with it.

SOVD clients that send no token, such as the ros2_medkit web UI, get the gateway's anonymous role
(`anonymous` in `gateway.yaml`, a viewer by default): they can read, not clear. To let people clear
from such a client, put it behind your single sign-on proxy (`proxy_auth`), so each clear is
recorded under the person's name. Raising the anonymous role to operator lets anyone on the network
clear faults: only on a bench.
