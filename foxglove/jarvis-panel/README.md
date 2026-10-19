# Jarvis panel for Foxglove and Lichtblick

Jarvis's open faults over its event log, next to your robot's own data: live through
[foxglove_bridge](https://docs.foxglove.dev/docs/connecting-to-data/ros-foxglove-bridge), or from a
Jarvis recording (`<data_dir>/bags/jarvis_*/*.mcap`), where the log runs up to the playhead and a
click on an event takes the playback there.

- **Open faults** (`/jarvis/faults`): worst first, latched ones marked, their component and how long
  they have been open; muted symptoms left out.
- **Events** (`/jarvis/event_batches`, the stream as Jarvis records it): faults raised and cleared,
  rule matches, recovery steps, insights, operator actions; from a minimum severity.

The guide: [docs/guides/foxglove.md](../../docs/guides/foxglove.md).

## Install

Download `guilyx.jarvis-panel-<version>.foxe` (attached to each release; the `jarvis-panel`
artifact of a CI run), then drag it onto the Foxglove or Lichtblick window, or install it from
_Settings → Extensions_. Add the **Jarvis** panel to a layout.

## Use

Live: run foxglove_bridge next to Jarvis (the demo's `foxglove:=true`, which the Docker demo does)
and open `ws://<robot>:8765`. A recording: open its MCAP files. The panel's settings change the
topics (a namespaced Jarvis, a remapped stream), the minimum severity of the events and how many
are shown.

## Build

```bash
npm ci
npm run lint && npm run typecheck && npm test
npm run package          # guilyx.jarvis-panel-<version>.foxe
npm run local-install    # straight into the Foxglove desktop app on this machine
```

`src/model.ts` turns Jarvis's messages into rows (tested on its own, `src/model.test.ts`);
`src/JarvisPanel.tsx` is the panel.
