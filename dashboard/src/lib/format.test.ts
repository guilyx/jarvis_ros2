import { describe, expect, it } from "vitest";
import { atLeast, between, duration, glob, percent, severityName, toMs } from "./format";

describe("format", () => {
  it("reads the gateway's nanosecond stamps", () => {
    expect(toMs("2026-10-17T09:14:03.120456789Z")).toBe(Date.UTC(2026, 9, 17, 9, 14, 3, 120));
    expect(toMs("2026-10-17T09:14:03Z")).toBe(Date.UTC(2026, 9, 17, 9, 14, 3));
    expect(toMs("2026-10-17T09:14:03.5+00:00")).toBe(Date.UTC(2026, 9, 17, 9, 14, 3, 500));
    expect(toMs(null)).toBeNull();
    expect(between("2026-10-17T09:00:00Z", "2026-10-17T09:01:30.000000000Z")).toBe(90);
  });

  it("says durations the way people do", () => {
    expect(duration(4.21)).toBe("4.2 s");
    expect(duration(42)).toBe("42 s");
    expect(duration(192)).toBe("3 min 12 s");
    expect(duration(7500)).toBe("2 h 5 min");
    expect(duration(3 * 86400 + 4 * 3600)).toBe("3 d 4 h");
    expect(duration(null)).toBe("—");
    expect(percent(0.875)).toBe("88 %");
  });

  it("matches codes like the gateway (fnmatch globs)", () => {
    expect(glob("ros.*").test("ros.topic.stale")).toBe(true);
    expect(glob("ros.*").test("diag.ros.x")).toBe(false);
    expect(glob("a?c").test("abc")).toBe(true);
    expect(glob("jarvis.recovery.(x)").test("jarvis.recovery.(x)")).toBe(true);
  });

  it("orders severities", () => {
    expect(atLeast("ERROR", "WARN")).toBe(true);
    expect(atLeast("INFO", "WARN")).toBe(false);
    expect(atLeast("INFO", undefined)).toBe(true);
    expect(severityName(40)).toBe("ERROR");
    expect(severityName(50)).toBe("CRITICAL");
  });
});
