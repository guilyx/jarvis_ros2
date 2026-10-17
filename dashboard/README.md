# Jarvis dashboard

The web console of one robot's Jarvis: live events, faults, incident timelines, recovery (with
approvals and the kill switch), rules, analytics and Jarvis' own health. A static bundle that the
gateway (`jarvis_gateway`) serves: no Node.js on the robot.

React 19 + TypeScript (strict) + Vite · TanStack Query, Router, Table and Virtual · Tailwind CSS +
Radix primitives · Apache ECharts · a typed client generated from the gateway's OpenAPI document.

## Develop

```bash
npm ci
jarvis-gateway --fake --scenario --anonymous-role admin   # an in-memory Jarvis on :8080 (no ROS)
npm run dev                                               # http://localhost:5173, the API proxied to :8080
```

`--scenario` makes things happen: the e-stop is latched, a lidar goes quiet and a rule restarts its
relay, a camera drops off the graph and comes back. Against a real robot, point the proxy at its
gateway: `JARVIS_GATEWAY=http://robot:8080 npm run dev`.

| Command                                          | What it does                                                                  |
| ------------------------------------------------ | ----------------------------------------------------------------------------- |
| `npm run build`                                  | Type-check and build `dist/` (what the gateway serves)                        |
| `npm test`                                       | Unit tests (Vitest): live stream resume, filters, timeline, views             |
| `npm run e2e`                                    | Playwright against the gateway over the fake (it starts it, serving `dist/`)  |
| `E2E_BASE_URL=http://localhost:8080 npm run e2e` | The same against a running Jarvis (CI: the Docker demo)                       |
| `npm run lint`                                   | ESLint and Prettier                                                           |
| `npm run gen:api`                                | Regenerate `src/api/schema.d.ts` from the gateway (CI fails when it is stale) |

## Serve it

The gateway serves the bundle from `--dashboard DIR`, else `$JARVIS_DASHBOARD`, else
`share/jarvis_gateway/dashboard` (where the Docker image puts it). From source:

```bash
(cd dashboard && npm ci && npm run build)
JARVIS_DASHBOARD=$PWD/dashboard/dist ros2 launch jarvis_bringup jarvis.launch.py
```

## How it works

- **One live connection** (`src/live`): a WebSocket that subscribes from the last `seq` it saw, so a
  reconnect replays exactly what was missed. It holds the fault and execution snapshots; views
  read them directly, and events it receives refetch the views they change.
- **Filters live in the URL**: every view is shareable as it is.
- **Commands** go through the gateway with your token (Identity, top right); dangerous ones confirm
  and take a note, recorded with your name in the audit trail. What your role cannot do is hidden.
