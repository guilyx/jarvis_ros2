# ros.topic.slow: a watched topic publishes too slowly

**Severity** WARN · **Raised by** the topic watchdog · **Clears** when the rate is back above its minimum

## What it means

A topic publishes below its `min_rate_hz` (measured over `rate_window_s`). Its consumers get
fewer updates than they are tuned for: costmaps lag, controllers react late. The attributes give
`rate_hz` (measured) and `min_rate_hz`.

## Check

1. `ros2 topic hz <topic>` and `ros2 topic bw <topic>`: how far below, and how big the messages.
2. Is the machine overloaded? `top` or `htop` on the robot: CPU, memory, a process spinning.
3. Does it cross a network (a sensor on another computer, Wi-Fi)? Drops and limited bandwidth
   slow large messages first.
4. The driver's own settings (rate, resolution), and its diagnostics.

## Fix

- Relieve the machine (stop what does not need to run), or lower what the topic carries.
- Fix the link the topic crosses.
- Restart the driver if it degraded on its own.

## Escalate when

- It stays slow under normal load: the hardware or the configuration needs a change.
