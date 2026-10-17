import type { ExecutionsSnapshot, Fault, JarvisEvent } from "@/api/types";

export type LiveStatus = "connecting" | "live" | "reconnecting" | "unauthorized";

export interface LiveState {
  status: LiveStatus;
  /** Events received since the page opened, oldest first (bounded). */
  events: JarvisEvent[];
  faults: Fault[] | null;
  executions: ExecutionsSnapshot | null;
  lastSeq: string | null;
}

type Message =
  | { type: "event"; data: JarvisEvent }
  | { type: "faults"; data: { last_seq: string; faults: Fault[] } }
  | { type: "executions"; data: ExecutionsSnapshot }
  | { type: "backfill_done"; seq: string; truncated: boolean }
  | { type: "heartbeat"; seq: string }
  | { type: "pong" };

export interface SocketLike {
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: string }) => void) | null;
  onclose: ((ev: { code: number }) => void) | null;
  send(data: string): void;
  close(): void;
}

/** seq values are uint64 decimal strings. */
export function seqAfter(a: string, b: string | null): boolean {
  return b === null || BigInt(a) > BigInt(b);
}

/**
 * The single live connection. It subscribes from the last seq it saw, so a reconnect (network
 * blip, gateway restart) replays exactly what was missed: the gateway backfills from its history
 * and never sends an event twice.
 */
export class LiveStream {
  private state: LiveState = {
    status: "connecting",
    events: [],
    faults: null,
    executions: null,
    lastSeq: null,
  };
  private listeners = new Set<() => void>();
  private eventListeners = new Set<(e: JarvisEvent) => void>();
  private socket: SocketLike | null = null;
  private retry = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  constructor(
    private readonly url: () => string,
    private readonly open: (url: string) => SocketLike = (u) => new WebSocket(u) as unknown as SocketLike,
    private readonly capacity = 5000,
  ) {}

  get snapshot(): LiveState {
    return this.state;
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** Called for every new event (not for snapshots): views refetch what it changes. */
  onEvent(listener: (e: JarvisEvent) => void) {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.socket?.close();
    this.socket = null;
  }

  /** Reconnect now (the token changed, or the user asked). */
  restart() {
    this.stop();
    this.retry = 0;
    this.set({ status: "connecting" });
    this.start();
  }

  private set(patch: Partial<LiveState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((l) => l());
  }

  private connect() {
    const socket = this.open(this.url());
    this.socket = socket;
    socket.onopen = () => {
      const subscribe: Record<string, unknown> = { op: "subscribe" };
      if (this.state.lastSeq !== null) subscribe.since_seq = this.state.lastSeq;
      socket.send(JSON.stringify(subscribe));
    };
    socket.onmessage = (ev) => this.handle(JSON.parse(ev.data) as Message);
    socket.onclose = (ev) => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.stopped) return;
      if (ev.code === 1008) {
        this.set({ status: "unauthorized" });
        return;
      }
      this.set({ status: "reconnecting" });
      const delay = Math.min(15_000, 500 * 2 ** this.retry++);
      this.timer = setTimeout(() => this.connect(), delay);
    };
  }

  private handle(m: Message) {
    switch (m.type) {
      case "event": {
        if (!seqAfter(m.data.seq, this.state.lastSeq)) return;
        const events =
          this.state.events.length >= this.capacity ? this.state.events.slice(1) : [...this.state.events];
        events.push(m.data);
        this.set({ events, lastSeq: m.data.seq });
        this.eventListeners.forEach((l) => l(m.data));
        return;
      }
      case "faults":
        this.set({ faults: m.data.faults });
        return;
      case "executions":
        this.set({ executions: m.data });
        return;
      case "backfill_done":
        this.retry = 0;
        // A first subscription starts at the gateway's newest seq: resume from there.
        this.set({ status: "live", lastSeq: this.state.lastSeq ?? m.seq });
        return;
      default:
        return;
    }
  }
}
