# Devices that don't run ROS (MQTT)

A robot is more than its ROS nodes: a battery's BMS, a charger, a PLC behind a gateway, a
microcontroller on the lift. With `mqtt:=true`, they report to Jarvis over MQTT: small JSON
messages, from anything with an MQTT client. Their faults go through the same catalog, rules,
recovery and agent as everything else. Jarvis notices when one drops off the network, and HMIs and
PLCs read Jarvis's open faults back from MQTT.

1. [Start it](#1-start-it)
2. [Report from a device](#2-report-from-a-device)
3. [Liveness: the last will](#3-liveness-the-last-will)
4. [Jarvis's open faults, for HMIs and PLCs](#4-jarviss-open-faults-for-hmis-and-plcs)
5. [Security](#5-security)

## 1. Start it

Any MQTT broker works (mosquitto, EMQX, HiveMQ). In `jarvis.yaml`:

```yaml
/jarvis/bridges/mqtt:
  ros__parameters:
    host: localhost
    port: 1883
    allow: ["power.*", "charger.*"]   # codes devices may report
    devices: [bms, charger]           # expected: offline if they don't show up
```

```bash
# rosdep (step 1 of the integration guide) installs paho-mqtt
ros2 launch jarvis_bringup jarvis.launch.py mqtt:=true params_file:=/etc/jarvis/jarvis.yaml
```

## 2. Report from a device

A device reports under its own name: the name is the component of everything it sends.

```
jarvis/devices/<device>/report
```

One JSON object, or a list of them:

| Message | Becomes |
|---------|---------|
| `{"fault": "power.bms.cell_overtemp", "severity": "ERROR", "message": "cell 3 at 61 °C", "attrs": {"cell": 3}}` | A fault raised (severity defaults to `ERROR`) |
| `{"clear": "power.bms.cell_overtemp", "message": "cooled down"}` | The fault cleared |
| `{"state": "power.bms.soc", "value": 54}` | A state (latest value wins), e.g. for rule guards |
| `{"log": "power.bms.balancing", "severity": "INFO", "message": "balancing cells"}` | A log event |

```bash
mosquitto_pub -t jarvis/devices/bms/report \
  -m '{"fault": "power.bms.cell_overtemp", "message": "cell 3 at 61 °C", "attrs": {"cell": 3}}'
```

- **Codes** are dotted lowercase, like every Jarvis code. Catalog them (severity floor, debounce,
  latch, runbook) like any other.
- **`attrs`** maps names to text, numbers or booleans. The device's name is added as `device`.
- **Repeats are fine:** a fault reported every second is one fault. A device that restarts
  can report it again; Jarvis keeps counting the same fault.
- **Refused reports.** Jarvis refuses a report that doesn't fit and logs `mqtt.report.rejected`
  with the reason. Examples: an unknown key, a code that isn't dotted lowercase, a code outside
  `allow`, a `jarvis.*` or `mqtt.*` code, or more than 64 KiB.

## 3. Liveness: the last will

A device publishes its status on `jarvis/devices/<device>/status`. On connecting it sends `online`
(retained), and it sets `offline` (retained) as its MQTT last will. If it drops off the network
without saying goodbye (power lost, crashed, cable out), the broker publishes its will, and Jarvis
raises `mqtt.device.offline` for it. The fault clears when the device says `online` again.

```python
client.will_set("jarvis/devices/bms/status", "offline", qos=1, retain=True)
client.connect("robot.local", 1883, keepalive=10)  # gone after 15 s of silence
client.publish("jarvis/devices/bms/status", "online", qos=1, retain=True)
```

- Devices listed in `devices` are expected. If one hasn't said `online` within `startup_grace_s`
  (10 s), it is offline.
- If the bridge loses the broker for more than 5 s, it raises `mqtt.broker.unreachable`. While
  that fault is open, nothing the devices send is heard.

## 4. Jarvis's open faults, for HMIs and PLCs

The bridge publishes Jarvis's open faults, retained, on `jarvis/robots/<robot_id>/faults`. It does
so whenever they change, so a screen that connects later gets them at once:

```json
{"robot": "amr-7", "faults": [
  {"code": "power.bms.cell_overtemp", "severity": "ERROR", "state": "ACTIVE", "component": "bms",
   "message": "cell 3 at 61 °C", "muted": false, "incident_id": "…"}]}
```

`publish_faults: false` turns it off. To act on a fault, use the dashboard or the API: what comes
back over MQTT is read-only.

## 5. Security

MQTT is outside DDS, so SROS2 doesn't cover it. Secure the broker itself:

- **TLS** (`tls: true`, `ca_file`) and a user for Jarvis (`username`, `password_env`: the variable
  that holds the password, never the file).
- **ACLs:** each device may write only `jarvis/devices/<its name>/#`, so it can't report as another
  device. Jarvis needs to read `jarvis/devices/#` and write `jarvis/robots/<robot_id>/faults`.
- **`allow`** keeps devices to their codes. A fault can trigger recoveries, so a charger shouldn't
  be able to raise `safety.*`. `jarvis.*` and `mqtt.*` are always refused.

| Setting | Default | |
|---------|---------|---|
| `host`, `port` | `localhost`, `1883` | The broker |
| `tls`, `ca_file` | `false`, `""` | TLS, and the broker's CA |
| `username`, `password_env` | `""` | Credentials; the password comes from that environment variable |
| `client_id` | `jarvis-<robot_id>` | |
| `prefix` | `jarvis` | Topics under it. On a site-wide broker, use one prefix per robot (`site/amr-7`) |
| `allow` | `["*"]` | Codes devices may report (globs) |
| `devices` | none | Expected devices |
| `startup_grace_s` | `10.0` | How long an expected device has to show up |
| `publish_faults` | `true` | Jarvis's open faults, retained, on `<prefix>/robots/<robot_id>/faults` |
