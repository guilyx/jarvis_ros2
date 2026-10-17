import { getToken } from "./client";
import type { Insight } from "./types";

/** What POST /api/v1/agent/ask streams: progress as the agent works, then the insight or an error. */
export type AskEvent =
  | { kind: "progress"; stage: string; detail: string }
  | { kind: "insight"; insight: Insight }
  | { kind: "error"; message: string };

/** One server-sent event block ("event: …\ndata: …") as an AskEvent, or null if it is not one. */
export function parseEvent(block: string): AskEvent | null {
  let kind = "";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) kind = line.slice(7).trim();
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!kind || !data) return null;
  try {
    const d = JSON.parse(data);
    if (kind === "progress") return { kind, stage: String(d.stage ?? ""), detail: String(d.detail ?? "") };
    if (kind === "insight") return { kind, insight: d as Insight };
    if (kind === "error") return { kind, message: String(d.message ?? "the agent could not answer") };
  } catch {
    return null;
  }
  return null;
}

/** Asks the agent and calls onEvent for each event as it arrives (EventSource cannot POST). */
export async function askAgent(
  body: { incident_id?: string; question?: string },
  onEvent: (e: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json", Accept: "text/event-stream" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const url = new URL("/api/v1/agent/ask", globalThis.location?.origin ?? "http://localhost");
  const payload = JSON.stringify({ incident_id: body.incident_id ?? "", question: body.question ?? "" });
  const res = await fetch(new Request(url, { method: "POST", headers, body: payload, signal }));
  if (!res.ok || !res.body) {
    let message = res.statusText;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") message = j.detail;
    } catch {
      // not JSON: keep the status text
    }
    onEvent({ kind: "error", message: `${res.status} ${message}`.trim() });
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const e = parseEvent(buffer.slice(0, end));
      buffer = buffer.slice(end + 2);
      if (e) onEvent(e);
    }
  }
  const last = parseEvent(buffer);
  if (last) onEvent(last);
}
