# Watching a fleet

Every robot keeps its own Jarvis and gateway: its faults, rules, recoveries and recordings stay on
the robot, and work without a network. To see several robots at once, one gateway lists the others,
and its dashboard's **Fleet** view shows them side by side, the ones that need someone first; a
click opens a robot's own dashboard. Their history, uploaded to object storage, answers questions
across the fleet: which fault costs the most, on which robots, since when.

1. [List the robots](#1-list-the-robots)
2. [What the fleet view shows](#2-what-the-fleet-view-shows)
3. [Where to run it](#3-where-to-run-it)
4. [The fleet's history](#4-the-fleets-history)

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

## 4. The fleet's history

The fleet view shows now. The history of what happened (every fault episode, incident and
recovery) is in each robot's lake, and goes with its disk: retention deletes it after months, a
disk swap after a day. Upload it, and the whole fleet's history is one place you can query.

### Upload it

Each robot's lake uploads its Parquet, and if asked the black box's snapshots, as they are
complete, to object storage or a mounted share:

```bash
ros2 launch jarvis_bringup jarvis.launch.py robot_id:=amr-07 \
  lake_upload_to:=s3://acme-robots/jarvis                  # AWS S3
# lake_upload_to:="s3://jarvis/fleet?endpoint_override=minio.lan:9000&scheme=http"   MinIO, Ceph, ...
# lake_upload_to:=file:///mnt/fleet                        a mounted share (NFS, SMB)
# lake_upload_blackbox:=true                               the black box's snapshots too
```

```text
<target>/robots/<robot_id>/events/date=YYYY-MM-DD/<bag>_<n>.parquet
<target>/robots/<robot_id>/blackbox/<bag>/<bag>_<n>.mcap
```

Credentials come from where the AWS SDK looks: `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
(in `/etc/jarvis/jarvis.env` with [systemd](robot.md#3-start-it-at-boot-systemd), `-e` with
Docker), the AWS config file, an instance role. Never in the URI: it shows in the process list.
Give each robot a key that may only write under its own prefix:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:PutObject"],
    "Resource": "arn:aws:s3:::acme-robots/jarvis/robots/amr-07/*"
  }]
}
```

The events are the robot's: fault messages, component names, operator names in the audit trail.
The bucket is yours to protect like the robots' disks: encryption at rest, no public access.

### How it behaves

- **A file goes up once it is complete**, and again if it changes (a split converted again after
  a crash). The ledger (`lake/uploads.json`) remembers what went where, across restarts.
- **Offline is normal.** A robot out of Wi-Fi keeps its history, and tries again by itself: after
  30 s, then doubling up to every 10 min, oldest files first, a minute of uploading per pass at
  most so conversion never waits.
- **Retention waits for the upload.** Days of Parquet and snapshots not uploaded yet stay, past
  their age and budget. Disk pressure does not wait: the robot comes first, and what had to go
  before it was uploaded is counted (`lost`).
- **Behind is a fault.** When a file has waited longer than `lake_upload_max_lag_hours` (24 h),
  the lake raises `jarvis.upload.behind` (WARN, [runbook](../../ros/jarvis_bringup/config/runbooks/jarvis.upload.behind.md)),
  cleared once it caught up. The dashboard's System page shows where it uploads, what waits and
  the last error.
- **Push now**: `ros2 run jarvis_lake jarvis-lake --robot amr-07 --upload-to s3://... upload`, e.g.
  while docked on a good network (`--dry-run` lists what would go).

### Query it

`jarvis-lake fleet` reads the uploaded Parquet where it is, with the lake's own views, each with a
`robot` column. It runs wherever Jarvis is installed (`ros2 run jarvis_lake jarvis-lake fleet`, or
the Docker image), with credentials that may read the bucket:

```console
$ jarvis-lake fleet s3://acme-robots/jarvis
robot   events  first_event                 last_event                  faults  incidents  recoveries
amr-07  48211   2026-10-02 06:01:13.204+00  2026-10-19 07:40:51.880+00  312     41         37
amr-08  51007   2026-10-02 06:00:58.911+00  2026-10-19 07:41:02.113+00  198     22         20

$ jarvis-lake fleet s3://acme-robots/jarvis \
    "SELECT code, count(*) AS episodes, count(DISTINCT robot) AS robots,
            median(epoch(cleared_at - confirmed_at)) AS median_s
     FROM fault_episodes WHERE confirmed_at IS NOT NULL GROUP BY code ORDER BY episodes DESC LIMIT 5"
```

`--format csv` or `json` for a spreadsheet or a script. Any tool that reads Parquet reads it too:
DuckDB, Spark, Athena, a notebook with pandas.
