# Jarvis on a real robot

From a laptop demo to a robot computer that boots into Jarvis and has it ready before anyone
drives off: the hardware, Docker or a source install, starting at boot, the disk, the clock,
the network, security, and `ros2 jarvis doctor`, the check before you hand the robot over.

1. [The computer](#1-the-computer)
2. [Install it: Docker or from source](#2-install-it-docker-or-from-source)
3. [Start it at boot (systemd)](#3-start-it-at-boot-systemd)
4. [The data directory](#4-the-data-directory)
5. [The clock](#5-the-clock)
6. [The network](#6-the-network)
7. [Security](#7-security)
8. [Check it: `ros2 jarvis doctor`](#8-check-it-ros2-jarvis-doctor)

What Jarvis watches on the robot, and how your nodes report to it: the
[integration guide](integration.md). This guide is about the machine it runs on.

## 1. The computer

Jarvis runs on the robot's own computer, next to its nodes: ARM64 (Jetson Orin, Raspberry Pi 5,
RK3588 boards) or x86_64 (industrial PCs, NUCs). CI builds and tests the whole workspace on both,
natively (ROS 2 Jazzy on an ARM runner, not under emulation), and each release publishes its
Docker images for both.

| | |
|---|---|
| OS | Ubuntu 24.04 with ROS 2 Jazzy, or Ubuntu 26.04 with Lyrical Luth |
| Memory | the core: 80–93 MB, plus its buffers (the black box: `blackbox_cache_mb`, 256 MB by default); the lake, the gateway and the agent are Python processes on top |
| CPU | a fifth of one core at 500 events/s with the recorder and the black box, 4 % idle ([requirements §5](../design/01-requirements.md)) |
| Disk | an SSD or NVMe with room for the recordings ([§4](#4-the-data-directory)); not the SD card |

## 2. Install it: Docker or from source

**Docker** is the shortest way on a robot that runs containers already. The image has
everything built, the dashboard included; the same tag pulls the right architecture:

```bash
docker run -d --name jarvis --restart unless-stopped \
  --network host --ipc host \
  --stop-signal SIGINT --stop-timeout 30 \
  -v /var/lib/jarvis:/var/lib/jarvis -v /etc/jarvis:/etc/jarvis:ro \
  -e ROS_DOMAIN_ID=0 \
  ghcr.io/guilyx/jarvis:<version> \
  ros2 launch jarvis_bringup jarvis.launch.py robot_id:=amr-07 \
    rules:=/etc/jarvis/rules plans:=/etc/jarvis/plans
```

- `--network host`: DDS discovery finds the robot's nodes, as if Jarvis ran on the host.
- `--ipc host`: shared memory with the robot's nodes (Fast DDS uses it on the same machine).
- `--stop-signal SIGINT`: Jarvis stops like Ctrl-C, and the recorder finalizes its files.
- `/var/lib/jarvis`: the data directory, kept across upgrades ([§4](#4-the-data-directory)).

The images are published with each release ([RELEASING.md](../../RELEASING.md)). For your own
build, on the robot or for it from another machine:

```bash
docker build -f docker/Dockerfile --target runtime -t jarvis .                  # this machine's
docker buildx build -f docker/Dockerfile --target runtime --platform linux/arm64 \
  -t jarvis:arm64 --load .                                                       # for an ARM robot
```

The second line runs under emulation when the machine isn't ARM: it works, slowly (an hour or
more). Building on the robot, or on any ARM64 machine, is faster.

**From source**, in the robot's workspace: [integration guide §1](integration.md#1-install-it-and-start-it).
Then start it with systemd, below.

## 3. Start it at boot (systemd)

`jarvis_bringup` ships a unit and its settings. Jarvis runs as its own user, starts once the
network is up and the clock synchronized, stops cleanly, and restarts when it dies:

```bash
share=$(ros2 pkg prefix jarvis_bringup)/share/jarvis_bringup/systemd
sudo useradd --system --home-dir /var/lib/jarvis --shell /usr/sbin/nologin jarvis
sudo install -D -m 644 "$share/jarvis.env" /etc/jarvis/jarvis.env
sudoedit /etc/jarvis/jarvis.env              # the workspace, the robot's name, the domain, ...
sudo install -m 644 "$share/jarvis.service" /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now jarvis
journalctl -u jarvis -f                      # what it says
```

What goes in [`jarvis.env`](../../ros/jarvis_bringup/systemd/jarvis.env):

| Setting | |
|---------|---|
| `ROS_SETUP` | the workspace's `setup.bash` (it sources ROS 2's) |
| `JARVIS_ROBOT_ID` | the robot's name, on every event, in the dashboard and the fleet view |
| `JARVIS_DATA` | the data directory, `/var/lib/jarvis` by default |
| `ROS_DOMAIN_ID`, `RMW_IMPLEMENTATION`, ... | the same as the robot's other nodes |
| `JARVIS_LAUNCH_ARGS` | any other launch argument: the robot's rules and plans, the gateway on the robot's network, ... |

After a crash, systemd starts it again within 5 s: Jarvis restores its open faults, incidents
and pending approvals, and recovers the recording the crash cut short. A stop (an upgrade, a
reboot) sends SIGINT and waits 30 s for the recorder to close its file.

Recoveries that restart [containers or units](containers.md) need rights the `jarvis` user
doesn't have: the Docker socket, or a polkit rule for just the units Jarvis may restart. The
containers guide has both, and why to keep them short.

## 4. The data directory

Everything Jarvis keeps is under one directory (`data_dir`, `$JARVIS_DATA`):

| | |
|---|---|
| `bags/` | the continuous recording of Jarvis's topics, one MCAP file every 5 minutes |
| `blackbox/` | what happened just before a fault: the black box's snapshots |
| `lake/` | the closed recordings as Parquet: history, analytics, backtests |
| `state/` | open faults, rule history, waiting approvals: what survives a restart |
| `config/` | with `config_from`: the rules and plans from the fleet, the revision that runs, the ones before |

Put it on the robot's biggest disk, an SSD rather than an SD card (the recorder writes all
day). The lake keeps `lake_min_free_gib` free (1 GiB) by deleting the oldest data first, and
raises `jarvis.storage.low` when it has to; the doctor warns below 5 GiB free, before that
happens. A day of recordings is what your robot publishes on `/jarvis/**` and
`record_extra_topics` in a day: measure a shift, then size the disk.

A disk can fail, or be swapped with the computer. To keep the history beyond the robot, the
lake uploads it to object storage or a share as it goes (`lake_upload_to`, in
`JARVIS_LAUNCH_ARGS`; credentials in `jarvis.env`): [fleet guide §4](fleet.md#4-the-fleets-history).
Its rules and plans can come from there too, so a fleet changes a rule once
(`config_from`): [fleet guide §5](fleet.md#5-rules-and-plans-from-the-fleet).

## 5. The clock

Every event has its time from the robot's clock: the timeline, incidents, rule windows, and the
fleet view, which interleaves robots by time. A robot computer without a battery-backed clock
(a Raspberry Pi) boots in the past until it reaches a time server.

- Install a time sync: `chrony` (or `systemd-timesyncd`), pointed at your network's time source
  when the robot has no internet.
- The unit starts after `time-sync.target`. For it to wait for the sync, not just for the
  service: `systemctl enable chrony-wait.service` (chrony) or
  `systemd-time-wait-sync.service` (timesyncd).
- The doctor says whether the clock is synchronized.

## 6. The network

- **The same domain and middleware as the robot**: `ROS_DOMAIN_ID` and `RMW_IMPLEMENTATION` in
  `jarvis.env` (or `-e` with Docker), as the robot's own nodes have them. Jarvis talks to nothing
  it doesn't see on the graph.
- **Discovery**: if the robot's nodes keep discovery to the machine
  (`ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`), set the same for Jarvis. Several computers on
  one robot: `SUBNET`, or `ROS_STATIC_PEERS` with their addresses.
- **The dashboard from the robot's network**: the gateway listens on `127.0.0.1` by default.
  `gateway_host:=0.0.0.0` (in `JARVIS_LAUNCH_ARGS`) opens it to the network, with tokens and
  TLS first ([security guide §2](security.md)). Several robots: the [fleet view](fleet.md).

## 7. Security

On a robot others can reach, close it up before handing it over: DDS security (SROS2, an
enclave per Jarvis process), the gateway behind TLS with tokens or single sign-on, and what
leaves the robot when the agent asks a model. All in the [security guide](security.md); the
keystore's variables go in `jarvis.env` (`ROS_SECURITY_*`, commented there).

## 8. Check it: `ros2 jarvis doctor`

Before handing a robot over, after an upgrade, or when something looks off:

```console
$ ros2 jarvis doctor
Jarvis doctor · amr-07 · Linux aarch64 · ROS 2 jazzy · rmw_fastrtps_cpp · domain 0

 ✓ event hub      active
 ✓ fault manager  active
 ✓ rule engine    active
 ✓ recovery       active
 ! recovery mode  DRY_RUN: Jarvis decides, nothing moves
                  → ros2 jarvis mode ENABLED --reason "..." once you trust it
 ✓ plans          9 loaded
 ✓ rules          5 loaded (1 in shadow mode)
 ✓ faults         none open
 ✓ data           /var/lib/jarvis: 212.4 GiB free of 233 GiB
 ✗ recordings     the recorder is not running
                  → see jarvis.launch.py's log (/jarvis/recorder)
 ✓ clock          synchronized (NTP)
 ✓ gateway        http://127.0.0.1:8080: ok

1 problem(s), 1 warning(s).
```

Each line that isn't ✓ says what to do. `✗` is a problem, `!` a warning, `·` something the
doctor couldn't tell from where it ran. The exit code is 1 when there is a problem (warnings
don't count), so a provisioning script can stop on it; `--json` gives the same report to read.

| Check | ✓ when | |
|-------|--------|---|
| event hub, fault manager, rule engine, recovery | the node is active | a node that is missing or not active |
| recovery mode | `ENABLED` | `DRY_RUN` and `DISABLED` are warnings: right while you rehearse, not when handing over. Components under maintenance are listed |
| plans, rules | some are loaded | none: nothing would recover, or nothing happens by itself. Rules in shadow mode and switched off are counted |
| faults | none latched | `jarvis.storage.low` is a problem; a latched fault waits for a person |
| data | 5 GiB free or more | under 5 GiB a warning, under 1 GiB a problem (the lake is deleting recordings) |
| recordings | the recorder runs | how many recordings are kept |
| fleet config | with `config_from`: the robot runs what the fleet gives | a refused revision, an unreachable or empty source, `config_sync` not running: warnings, the robot runs the rules and plans it had |
| clock | synchronized | `timedatectl` or `chronyc`, whichever the robot has |
| gateway | `/api/v1/health` says ok | `--no-gateway` on a robot without one; `--gateway URL` for another address |

Run it on the robot: the data directory and the clock are the machine's. Under SROS2, run it
as an operator: `ROS_SECURITY_ENCLAVE_OVERRIDE=/jarvis/operator ros2 jarvis doctor`. In
Docker: `docker exec jarvis ros2 jarvis doctor`.
