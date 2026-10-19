# mqtt.broker.unreachable: Jarvis lost the MQTT broker

**Severity** ERROR · **Raised by** the MQTT bridge (`/jarvis/bridges/mqtt`) · **Clears** when it reconnects

## What it means

The MQTT bridge lost its connection to the broker for more than 5 s (it retries every few seconds).
Until it is back, nothing the devices behind it report reaches Jarvis, and a device that drops off
in the meantime isn't noticed. The attribute `broker` gives the address it tries.

## Check

1. Is the broker running? `systemctl status mosquitto` (or its container), on the machine in `host`.
2. Reachable from the robot? `mosquitto_sub -h <host> -p <port> -t '$SYS/broker/uptime' -C 1`.
3. Credentials and TLS: the bridge's log says why the broker refused it (`username`,
   `password_env`, `ca_file` in `/jarvis/bridges/mqtt`).

## Fix

- Start or restart the broker; the bridge reconnects by itself and this clears.
- Wrong address or credentials: fix `/jarvis/bridges/mqtt` in jarvis.yaml and restart Jarvis.

## Escalate when

- It stays down while devices that matter (BMS, charger) report through it.
