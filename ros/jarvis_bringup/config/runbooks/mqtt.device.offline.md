# mqtt.device.offline: a device that reports over MQTT is gone

**Severity** ERROR · **Raised by** the MQTT bridge (`/jarvis/bridges/mqtt`) · **Clears** when the device says it is online

## What it means

A device that doesn't run ROS (a BMS, a charger, a PLC gateway) reports to Jarvis over MQTT. Its
connection carries a last will: when it drops off the network without saying goodbye (power lost,
crashed, cable out), the broker publishes `offline` on its status topic, and Jarvis raises this
for the device (the component). A device listed in `devices` that never says `online` within
`startup_grace_s` is offline too. The attribute `device` names it.

Jarvis no longer hears it: its own faults can't be raised, nor cleared.

## Check

1. Is it powered and on the network? Its link LEDs, `ping`.
2. What the broker sees: `mosquitto_sub -h <broker> -t 'jarvis/devices/<device>/#' -v`. A retained
   `offline` with nothing after it: it is down. Reports but no `online`: it doesn't set its status.
3. The broker's log: refused credentials, an ACL that denies its topics.
4. `mqtt.broker.unreachable` open at the same time: the broker, not the device.

## Fix

- Power-cycle or reconnect the device; once its client is back it publishes `online` and this clears.
- A device that doesn't publish `online` on connect: make its firmware do it (retained), with
  `offline` as its last will (docs/guides/mqtt.md).

## Escalate when

- The device is safety- or power-relevant (BMS, charger, e-stop gateway) and doesn't come back:
  take the robot out of service until it does.
