// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// The Jarvis panel: the open faults (/jarvis/faults) over the event log (/jarvis/event_batches),
// live through foxglove_bridge or from a Jarvis recording. In a recording, the log is every event
// up to the playhead, and a click on one seeks there.

import {
  MessageEvent,
  PanelExtensionContext,
  SettingsTreeAction,
  SubscribeMessageRangeArgs,
  Time,
} from "@foxglove/extension";
import { ReactElement, useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

import {
  EventBatchMsg,
  EventLog,
  FaultArrayMsg,
  History,
  ago,
  clock,
  counts,
  openFaults,
  robotOf,
} from "./model";

type Config = { faultsTopic: string; eventsTopic: string; minSeverity: string; maxEvents: number };

const DEFAULTS: Config = {
  faultsTopic: "/jarvis/faults",
  eventsTopic: "/jarvis/event_batches",
  minSeverity: "INFO",
  maxEvents: 500,
};
const LEVELS = ["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"];
const COLORS: Record<string, string> = {
  CRITICAL: "#ef4444",
  ERROR: "#f97316",
  WARN: "#eab308",
  INFO: "#38bdf8",
  DEBUG: "#94a3b8",
};

type Range = (args: SubscribeMessageRangeArgs) => () => void;

/** Reading a whole topic of a recording: Foxglove's name for it, or Lichtblick's. Live, there is
 * nothing to read (the iterator never comes) and the log is what arrives. */
function rangeOf(context: PanelExtensionContext): Range | undefined {
  const c = context as PanelExtensionContext & { unstable_subscribeMessageRange?: Range };
  return (c.subscribeMessageRange ?? c.unstable_subscribeMessageRange)?.bind(context);
}

function Badge({ severity, n }: { severity: string; n?: number }): ReactElement {
  const color = COLORS[severity] ?? "#94a3b8";
  return (
    <span
      style={{
        border: `1px solid ${color}`,
        color,
        borderRadius: 4,
        padding: "0 5px",
        fontSize: 11,
        fontWeight: 600,
        whiteSpace: "nowrap",
      }}
    >
      {n == undefined ? severity : `${n} ${severity}`}
    </span>
  );
}

function JarvisPanel({ context }: { context: PanelExtensionContext }): ReactElement {
  const [config, setConfig] = useState<Config>(() => ({
    ...DEFAULTS,
    ...(context.initialState as Partial<Config>),
  }));
  const log = useMemo(() => new EventLog(config.maxEvents, config.minSeverity), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [faults, setFaults] = useState<FaultArrayMsg | undefined>();
  const [history, setHistory] = useState<History | undefined>();
  const [version, setVersion] = useState(0); // bumped when the log changes
  const [now, setNow] = useState<Time | undefined>();
  const [dark, setDark] = useState(true);
  const [renderDone, setRenderDone] = useState<(() => void) | undefined>();

  const onSettings = useCallback((action: SettingsTreeAction) => {
    if (action.action !== "update") {
      return;
    }
    const key = action.payload.path[1] as keyof Config;
    setConfig((c) => ({ ...c, [key]: action.payload.value as never }));
  }, []);

  useEffect(() => {
    context.saveState(config);
    context.setDefaultPanelTitle("Jarvis");
    log.configure(config.maxEvents, config.minSeverity);
    setVersion((v) => v + 1);
    context.subscribe([{ topic: config.faultsTopic }, { topic: config.eventsTopic }]);
    context.updatePanelSettingsEditor({
      actionHandler: onSettings,
      nodes: {
        general: {
          label: "Jarvis",
          fields: {
            faultsTopic: { label: "Faults topic", input: "string", value: config.faultsTopic },
            eventsTopic: { label: "Events topic", input: "string", value: config.eventsTopic },
            minSeverity: {
              label: "Events from",
              input: "select",
              value: config.minSeverity,
              options: LEVELS.map((l) => ({ label: l, value: l })),
            },
            maxEvents: { label: "Events kept", input: "number", value: config.maxEvents, min: 10 },
          },
        },
      },
    });
  }, [config, context, log, onSettings]);

  useLayoutEffect(() => {
    context.onRender = (state, done) => {
      if (state.didSeek === true) {
        log.clear();
        setVersion((v) => v + 1);
      }
      let changed = false;
      for (const m of (state.currentFrame ?? []) as MessageEvent[]) {
        if (m.topic === config.faultsTopic) {
          setFaults(m.message as FaultArrayMsg);
        } else if (m.topic === config.eventsTopic) {
          changed = log.add(m.message as EventBatchMsg, m.receiveTime) || changed;
        }
      }
      if (changed) {
        setVersion((v) => v + 1);
      }
      if (state.currentTime) {
        setNow(state.currentTime);
      }
      if (state.colorScheme) {
        setDark(state.colorScheme === "dark");
      }
      setRenderDone(() => done);
    };
    context.watch("currentFrame");
    context.watch("didSeek");
    context.watch("currentTime");
    context.watch("colorScheme");
  }, [context, config.faultsTopic, config.eventsTopic, log]);

  useEffect(() => {
    const range = rangeOf(context);
    setHistory(undefined);
    return range?.({
      topic: config.eventsTopic,
      onNewRangeIterator: async (batches) => {
        const all = new History();
        for await (const batch of batches) {
          for (const m of batch) {
            all.add(m.message as EventBatchMsg, m.receiveTime);
          }
          setHistory(all);
          setVersion((v) => v + 1);
        }
      },
    });
  }, [context, config.eventsTopic]);

  useEffect(() => {
    renderDone?.();
  }, [renderDone]);

  const recorded = history != undefined && history.size > 0;
  const rows = useMemo(() => openFaults(faults), [faults]);
  const shown = useMemo(
    () => (recorded ? history.until(now, config.minSeverity, config.maxEvents) : [...log.all]),
    [recorded, history, now, config, log, version], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const events = useMemo(() => [...shown].reverse(), [shown]);
  const robot = robotOf(faults, shown);
  const per = counts(rows);
  const seek = context.seekPlayback?.bind(context);
  const fg = dark ? "#e2e8f0" : "#0f172a";
  const muted = dark ? "#94a3b8" : "#64748b";
  const line = dark ? "#1e293b" : "#e2e8f0";
  const cell = {
    padding: "3px 6px",
    borderBottom: `1px solid ${line}`,
    verticalAlign: "top",
  } as const;

  return (
    <div
      data-testid="jarvis-panel"
      style={{
        padding: 8,
        color: fg,
        fontSize: 12,
        height: "100%",
        overflow: "auto",
        boxSizing: "border-box",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <strong style={{ fontSize: 13 }}>Jarvis{robot ? ` · ${robot}` : ""}</strong>
        {rows.length === 0 ? (
          <span style={{ color: "#10b981" }}>
            {faults ? "no open faults" : "waiting for /jarvis/faults"}
          </span>
        ) : (
          ["CRITICAL", "ERROR", "WARN"].map((s) =>
            (per[s] ?? 0) > 0 ? <Badge key={s} severity={s} n={per[s]} /> : undefined,
          )
        )}
      </div>

      {rows.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 10 }}>
          <thead>
            <tr style={{ color: muted, textAlign: "left" }}>
              <th style={cell}>open fault</th>
              <th style={cell}>component</th>
              <th style={cell}>state</th>
              <th style={cell}>since</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => (
              <tr key={f.id} data-testid="jarvis-fault" title={f.message}>
                <td style={cell}>
                  <Badge severity={f.severity} /> <code>{f.code}</code>
                  {f.message ? <div style={{ color: muted }}>{f.message}</div> : undefined}
                </td>
                <td style={cell}>{f.component}</td>
                <td style={{ ...cell, color: f.state === "LATCHED" ? COLORS.ERROR : fg }}>
                  {f.state}
                  {f.acknowledged ? " (ack)" : ""}
                </td>
                <td style={{ ...cell, color: muted, whiteSpace: "nowrap" }}>{ago(f.since, now)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div style={{ color: muted, margin: "4px 0" }}>
        Events from {config.minSeverity}
        {recorded ? ", up to the playhead" : ""}
        {seek && recorded ? " · click one to go there" : ""}
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <tbody>
          {events.map((e) => (
            <tr
              key={e.id}
              data-testid="jarvis-event"
              onClick={
                seek && recorded && e.at
                  ? () => {
                      seek(e.at as Time);
                    }
                  : undefined
              }
              style={{ cursor: seek && recorded ? "pointer" : "default" }}
            >
              <td style={{ ...cell, color: muted, whiteSpace: "nowrap", fontFamily: "monospace" }}>
                {clock(e.stamp)}
              </td>
              <td style={cell}>
                <Badge severity={e.severity} />
              </td>
              <td style={{ ...cell, whiteSpace: "nowrap" }}>
                <code>{e.code}</code>
              </td>
              <td style={{ ...cell, color: muted }}>{e.component}</td>
              <td style={cell}>{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {events.length === 0 && (
        <div style={{ color: muted, padding: 6 }}>No events yet on {config.eventsTopic}.</div>
      )}
    </div>
  );
}

export function initJarvisPanel(context: PanelExtensionContext): () => void {
  const root = createRoot(context.panelElement);
  root.render(<JarvisPanel context={context} />);
  return () => {
    root.unmount();
  };
}
