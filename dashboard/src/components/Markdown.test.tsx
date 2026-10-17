import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown, parseMarkdown } from "./Markdown";

const RUNBOOK = `# ros.topic.stale: a watched topic went silent

**Severity** ERROR · raised by the topic watchdog

## Check

1. Is the publisher running?
   \`ros2 node list\`
2. Is the topic published?

   \`\`\`bash
   ros2 topic hz /scan
   \`\`\`

3. Restart it.

- one
- two

> Escalate when it comes back within the hour.

---
`;

describe("Markdown", () => {
  it("reads what runbooks are written in", () => {
    expect(parseMarkdown(RUNBOOK)).toEqual([
      { kind: "heading", level: 1, text: "ros.topic.stale: a watched topic went silent" },
      { kind: "paragraph", text: "**Severity** ERROR · raised by the topic watchdog" },
      { kind: "heading", level: 2, text: "Check" },
      {
        kind: "list",
        ordered: true,
        start: 1,
        items: ["Is the publisher running? `ros2 node list`", "Is the topic published?"],
      },
      { kind: "code", text: "ros2 topic hz /scan" },
      { kind: "list", ordered: true, start: 3, items: ["Restart it."] },
      { kind: "list", ordered: false, start: 1, items: ["one", "two"] },
      { kind: "quote", text: "Escalate when it comes back within the hour." },
      { kind: "rule" },
    ]);
  });

  it("renders text, never HTML, and only http(s) links", () => {
    render(
      <Markdown
        text={
          "<img src=x onerror=alert(1)> see [docs](https://example.com/x) and [local](javascript:alert(1)) `<b>`"
        }
      />,
    );
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText(/onerror/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "docs" }).getAttribute("href")).toBe("https://example.com/x");
    expect(screen.queryByRole("link", { name: "local" })).toBeNull();
    expect(screen.getByText("<b>").tagName).toBe("CODE");
  });
});
