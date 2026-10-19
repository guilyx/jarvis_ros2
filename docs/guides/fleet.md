# Watching a fleet

Every robot keeps its own Jarvis and gateway: its faults, rules, recoveries and recordings stay on
the robot, and work without a network. To see several robots at once, one gateway lists the others,
and its dashboard's **Fleet** view shows them side by side, the ones that need someone first; a
click opens a robot's own dashboard. Their history, uploaded to object storage, answers questions
across the fleet: which fault costs the most, on which robots, since when. And their rules and
plans can come from that storage too: change a rule once, every robot takes it.

1. [List the robots](#1-list-the-robots)
2. [What the fleet view shows](#2-what-the-fleet-view-shows)
3. [Where to run it](#3-where-to-run-it)
4. [The fleet's history](#4-the-fleets-history)
5. [Rules and plans from the fleet](#5-rules-and-plans-from-the-fleet)

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
$ ros2 run jarvis_lake jarvis-lake fleet s3://acme-robots/jarvis
robot   events  first_event                       last_event                        faults  incidents  recoveries
amr-07  48211   2026-10-02 06:01:13.204118+00:00  2026-10-19 07:40:51.880412+00:00  312     41         37
amr-08  51007   2026-10-02 06:00:58.911730+00:00  2026-10-19 07:41:02.113064+00:00  198     22         20

$ ros2 run jarvis_lake jarvis-lake fleet s3://acme-robots/jarvis \
    "SELECT code, count(*) AS episodes, count(DISTINCT robot) AS robots,
            median(epoch(cleared_at - confirmed_at)) AS median_s
     FROM fault_episodes WHERE confirmed_at IS NOT NULL GROUP BY code ORDER BY episodes DESC LIMIT 5"
```

`--format csv` or `json` for a spreadsheet or a script. Any tool that reads Parquet reads it too:
DuckDB, Spark, Athena, a notebook with pandas.

### In the dashboard

Tell the gateway you look at the fleet from where the robots upload, with credentials that may
read the bucket (in its environment), and its Fleet page shows the history under the robots' cards:

```yaml
fleet:
  robots:
    - { name: amr-08, url: http://amr-08.local:8080 }
  history: s3://acme-robots/jarvis
```

Over 24 hours, 7 or 30 days:

| | |
|---|---|
| Per robot, most faults first | Confirmed faults, MTTR, incidents and how many escalated, the share recovered without a person, the share of recoveries that worked, how far its history goes (what is not uploaded yet is not there) |
| Confirmed faults per robot | A stacked bar per day (per hour over 24 h): which robot, when |
| What costs the fleet most | The fault codes with the most episodes, the robots they hit, their MTTR |

The gateway reads the bucket once a minute per window, off its event loop
(`GET /api/v1/fleet/history?hours=168`, for any viewer). Without `fleet.history`, the page says how
to set it up.

To try it without a week of robots, write a made-up week for demo robots where they would upload,
then point the gateway there (never at a real fleet's storage: it is made up):

```bash
ros2 run jarvis_lake jarvis-lake demo-history file:///tmp/fleet --robots amr-7,amr-8,amr-9 --days 7
```

## 5. Rules and plans from the fleet

Ten robots, one rule to change: keep the rules and plans in one place, and each robot takes
them from there. The robot checks what it gets before it loads it, and keeps what it runs when
it can't reach the place or doesn't like what it finds.

### Set it up

Put the files in the fleet's storage, shared or per robot:

```
s3://acme-robots/config/rules/lidar.yaml              every robot's
s3://acme-robots/config/plans/dock.yaml
s3://acme-robots/config/robots/amr-07/rules/lidar.yaml   amr-07's lidar is different
```

Then start each robot with:

```bash
ros2 launch jarvis_bringup jarvis.launch.py robot_id:=amr-07 \
    config_from:='s3://acme-robots/config'    # or file:///mnt/fleet/config, an S3 API with ?endpoint_override=...
```

(On a robot started by systemd: in `JARVIS_LAUNCH_ARGS`, credentials in `jarvis.env`, as for
the history.) The URI is read as the history's target is (§4), with the same credentials;
the robot only reads, so its key needs `s3:GetObject` and `s3:ListBucket` on the prefix, no
more. Each robot looks every `config_period_s` (60 s).

The files are layered by name: the robot's own (its `rules` and `plans` launch arguments) at
the bottom, the shared ones over them, the robot's directory on top. A file replaces the one of
the same name below it, the others add to it: ship `plans/dock.yaml` and the robot keeps its
generic plans too; ship a `rules/core.yaml` with `rules: []` and the robot's `core.yaml` is gone.
Files that are not YAML (a README) are skipped.

### What the robot does with a new revision

A revision is the set of files, hashed. When the source has a new one:

1. **It settles.** The robot reads it again 5 s later, and goes on only if it is the same: a
   rule and its new plan half-way through an upload are not a revision.
2. **`jarvis lint` passes it**: the rules, the plans and the fault catalog fit together (a rule
   asking for a plan nobody has is refused here).
3. **The recovery orchestrator and the rule engine check it** with what only they know (the
   actuators the robot has, their allowlists), without loading it yet.
4. **They load it**, plans first. If anything fails, the robot goes back to the revision before
   and loads that again.

A refused revision raises `jarvis.config.rejected` (WARN,
[runbook](../../ros/jarvis_bringup/config/runbooks/jarvis.config.rejected.md)) with the first
error, and is not tried again until the source changes. Fix the files, or put the old ones back:
the fault clears once the robot runs what the source has. Each revision loaded is on the record
(`jarvis.config.applied`, with the revision before), so the history shows when a rule changed on
which robot.

The robot keeps its versions under `<data_dir>/config` (`active` is what runs, `versions/` the
last five, refused ones too, to look at or go back to by hand). It boots on the last revision
it accepted, offline too. A source that is unreachable, or empty (a wrong prefix, a wiped one),
changes nothing. The dashboard's System page shows where the rules and plans come from, the
revision that runs, the last look and what was refused, with every error.

Lint before you upload, with the same check the robot makes:

```bash
ros2 jarvis lint --rules config/rules --plans config/plans --catalog faults.yaml
```
