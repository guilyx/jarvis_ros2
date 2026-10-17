import { describe, expect, it, vi } from "vitest";
import type { JarvisEvent } from "@/api/types";
import { LiveStream, seqAfter, type SocketLike } from "./stream";

class FakeSocket implements SocketLike {
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  sent: unknown[] = [];
  closed = false;
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.({});
  }
  push(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  drop(code = 1006) {
    this.onclose?.({ code });
  }
}

function event(seq: number): JarvisEvent {
  return {
    id: `e${seq}`,
    seq: String(seq),
    stamp: "2026-10-17T09:00:00.000000000Z",
    source: { robot: "r", node: "/n", component: "c", host: "" },
    kind: "LOG",
    code: "test.tick",
    severity: "INFO",
  };
}

function setup(capacity = 5000) {
  const sockets: FakeSocket[] = [];
  const live = new LiveStream(
    () => "ws://test/api/v1/stream",
    () => {
      const s = new FakeSocket();
      sockets.push(s);
      return s;
    },
    capacity,
  );
  return { live, sockets };
}

describe("LiveStream", () => {
  it("subscribes from now, then keeps events and snapshots", () => {
    const { live, sockets } = setup();
    live.start();
    sockets[0]!.open();
    expect(sockets[0]!.sent).toEqual([{ op: "subscribe" }]);
    sockets[0]!.push({ type: "faults", data: { last_seq: "10", faults: [] } });
    sockets[0]!.push({ type: "backfill_done", seq: "10", truncated: false });
    sockets[0]!.push({ type: "event", data: event(11) });
    expect(live.snapshot.status).toBe("live");
    expect(live.snapshot.faults).toEqual([]);
    expect(live.snapshot.events.map((e) => e.seq)).toEqual(["11"]);
    expect(live.snapshot.lastSeq).toBe("11");
  });

  it("resumes from the last seq after a drop, without repeating", () => {
    vi.useFakeTimers();
    const { live, sockets } = setup();
    live.start();
    sockets[0]!.open();
    sockets[0]!.push({ type: "backfill_done", seq: "10", truncated: false });
    sockets[0]!.push({ type: "event", data: event(11) });
    sockets[0]!.drop();
    expect(live.snapshot.status).toBe("reconnecting");
    vi.advanceTimersByTime(600);
    sockets[1]!.open();
    expect(sockets[1]!.sent).toEqual([{ op: "subscribe", since_seq: "11" }]);
    sockets[1]!.push({ type: "event", data: event(11) }); // the server would not, but never twice anyway
    sockets[1]!.push({ type: "event", data: event(12) });
    sockets[1]!.push({ type: "backfill_done", seq: "12", truncated: false });
    expect(live.snapshot.events.map((e) => e.seq)).toEqual(["11", "12"]);
    expect(live.snapshot.status).toBe("live");
    vi.useRealTimers();
  });

  it("stops on a refused token", () => {
    const { live, sockets } = setup();
    live.start();
    sockets[0]!.drop(1008);
    expect(live.snapshot.status).toBe("unauthorized");
    expect(sockets).toHaveLength(1);
  });

  it("keeps a bounded window and tells listeners about new events", () => {
    const { live, sockets } = setup(3);
    const seen: string[] = [];
    live.onEvent((e) => seen.push(e.seq));
    live.start();
    sockets[0]!.open();
    for (let i = 1; i <= 5; i++) sockets[0]!.push({ type: "event", data: event(i) });
    expect(live.snapshot.events.map((e) => e.seq)).toEqual(["3", "4", "5"]);
    expect(seen).toEqual(["1", "2", "3", "4", "5"]);
  });

  it("compares uint64 seqs exactly", () => {
    expect(seqAfter("18446744073709551615", "18446744073709551614")).toBe(true);
    expect(seqAfter("9007199254740993", "9007199254740992")).toBe(true);
    expect(seqAfter("5", "5")).toBe(false);
    expect(seqAfter("1", null)).toBe(true);
  });
});
