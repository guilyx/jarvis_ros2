import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { streamUrl } from "@/api/client";
import type { JarvisEvent } from "@/api/types";
import { LiveStream, type LiveState } from "./stream";

const LiveContext = createContext<LiveStream | null>(null);

/** Which cached views an event makes stale (refetched at most every 2 s each). */
function staleKeys(e: JarvisEvent): string[][] {
  switch (e.kind) {
    case "FAULT_STATE":
      return [["fault"], ["faults", "all"], ["stats"], ["incidents"], ["incident"]];
    case "RECOVERY":
      return [["recoveries"], ["recovery"], ["stats"], ["incident"]];
    case "RULE_MATCH":
      return [["rules"], ["rule"], ["incident"]];
    case "OPERATOR":
      return [["audit"], ["rules"], ["rule"], ["plans"]];
    case "INSIGHT":
      return [["insights"], ["incident"]];
    case "SYSTEM":
      return e.code.startsWith("jarvis.incident.") ? [["incidents"], ["incident"], ["stats"]] : [["system"]];
    default:
      return [];
  }
}

export function LiveProvider({ stream, children }: { stream?: LiveStream; children: ReactNode }) {
  const live = useMemo(() => stream ?? new LiveStream(streamUrl), [stream]);
  const qc = useQueryClient();

  useEffect(() => {
    live.start();
    return () => live.stop();
  }, [live]);

  useEffect(() => {
    const pending = new Map<string, string[]>();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
      timer = null;
      pending.forEach((queryKey) => void qc.invalidateQueries({ queryKey }));
      pending.clear();
    };
    const off = live.onEvent((e) => {
      for (const key of staleKeys(e)) pending.set(key.join("/"), key);
      if (pending.size && !timer) timer = setTimeout(flush, 2000);
    });
    return () => {
      off();
      if (timer) clearTimeout(timer);
    };
  }, [live, qc]);

  return <LiveContext.Provider value={live}>{children}</LiveContext.Provider>;
}

export function useLiveStream(): LiveStream {
  const live = useContext(LiveContext);
  if (!live) throw new Error("useLive outside LiveProvider");
  return live;
}

export function useLive<T>(select: (s: LiveState) => T): T {
  const live = useLiveStream();
  return useSyncExternalStore(live.subscribe, () => select(live.snapshot));
}
