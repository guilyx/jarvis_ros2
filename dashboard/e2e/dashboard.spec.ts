import { expect, test, type Page } from "@playwright/test";

// Works against any Jarvis whose e-stop is latched (the fake's scenario starts so; on the demo,
// `inject estop` first) and that has the generic restart-lifecycle-node plan.

async function confirm(page: Page, button: string, note: string, field = "Note") {
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox", { name: field }).fill(note);
  await dialog.getByRole("button", { name: button, exact: true }).click();
  await expect(dialog).toBeHidden();
}

test("the overview is live", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("live-status")).toHaveText("LIVE");
  await expect(page.getByTestId("robot")).not.toHaveText("—");
  await expect(page.getByTestId("kpi-active-faults")).toBeVisible();
  await expect(page.getByTestId("attention-fault").filter({ hasText: "safety.estop.engaged" })).toBeVisible();
});

test("a fault shows its runbook from the catalog", async ({ page }) => {
  await page.goto("/faults");
  const row = page.getByTestId("fault-row").filter({ hasText: "safety.estop.engaged" });
  await row.getByRole("link", { name: "safety.estop.engaged" }).click();
  const runbook = page.getByTestId("runbook");
  await expect(runbook.getByRole("heading", { level: 1 })).toContainText("safety.estop.engaged");
  await expect(runbook.getByRole("heading", { level: 2 }).first()).toBeVisible();
});

test("an operator acknowledges, then clears the latched e-stop", async ({ page }) => {
  await page.goto("/faults");
  const row = page.getByTestId("fault-row").filter({ hasText: "safety.estop.engaged" });
  await expect(row).toContainText("LATCHED");
  const ack = row.getByRole("button", { name: "Acknowledge safety.estop.engaged" });
  if (await ack.isVisible()) {
    await ack.click();
    await confirm(page, "Acknowledge", "e2e: on my way");
    await expect(row.getByRole("button", { name: "Acknowledge safety.estop.engaged" })).toBeHidden();
  }
  await row.getByRole("button", { name: "Clear safety.estop.engaged" }).click();
  await confirm(page, "Clear", "e2e: released the e-stop", "Reason");
  await expect(row).toHaveCount(0);

  await page.goto("/system");
  await expect(
    page.getByTestId("audit-row").filter({ hasText: "cleared safety.estop.engaged" }).first(),
  ).toBeVisible();
});

async function setMode(page: Page, mode: "ENABLED" | "DRY RUN", reason: string) {
  const menu = page.getByRole("button", { name: "Recovery mode", exact: true });
  if ((await menu.textContent())?.includes(mode)) return;
  await menu.click();
  await page.getByRole("menuitem", { name: new RegExp(mode) }).click();
  await confirm(page, `Set ${mode}`, reason, "Reason");
  await expect(menu).toContainText(mode);
}

test("the kill switch goes through a reason, and the stream shows it", async ({ page }) => {
  await page.goto("/events?kind=OPERATOR");
  await expect(page.getByTestId("live-status")).toHaveText("LIVE");
  try {
    await setMode(page, "ENABLED", "e2e: start from ENABLED");
    await setMode(page, "DRY RUN", "e2e: maintenance");
    await expect(
      page.getByTestId("event-row").filter({ hasText: "jarvis.recovery.mode" }).first(),
    ).toBeVisible();
  } finally {
    await setMode(page, "ENABLED", "e2e: done");
  }
});

test("an operator puts a component under maintenance, then ends it", async ({ page }) => {
  await page.goto("/events?kind=OPERATOR");
  await expect(page.getByTestId("live-status")).toHaveText("LIVE");
  await page.getByRole("button", { name: "Start maintenance" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox", { name: "Component" }).fill("e2e-arm");
  await dialog.getByRole("combobox", { name: "For" }).selectOption("15");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("e2e: new gripper");
  await dialog.getByRole("button", { name: "Start maintenance" }).click();
  await expect(dialog).toBeHidden();

  const banner = page.getByTestId("maintenance");
  await expect(banner).toContainText("e2e-arm under maintenance until");
  await expect(banner).toContainText("e2e: new gripper");
  await expect(
    page.getByTestId("event-row").filter({ hasText: "jarvis.maintenance.started" }).first(),
  ).toBeVisible();
  await banner.getByRole("button", { name: "End maintenance of e2e-arm" }).click();
  await expect(banner).toBeHidden();
  await expect(
    page.getByTestId("event-row").filter({ hasText: "jarvis.maintenance.ended" }).first(),
  ).toBeVisible();
});

test("an operator runs a plan in dry run and follows it", async ({ page }) => {
  await page.goto("/recovery");
  await page.getByRole("button", { name: "Run plan…" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("combobox", { name: "Plan" }).selectOption("restart-lifecycle-node");
  await dialog.getByRole("textbox", { name: "node", exact: true }).fill("/demo/scan_relay");
  await dialog.getByRole("textbox", { name: "component", exact: true }).fill("lidar");
  await dialog.getByRole("textbox", { name: "Rationale" }).fill("e2e");
  await dialog.getByRole("switch", { name: "Dry run" }).click();
  await dialog.getByRole("button", { name: "Run", exact: true }).click();
  await expect(page).toHaveURL(/\/recovery\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("restart-lifecycle-node");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("SUCCEEDED", { timeout: 45_000 });
});

test("a rule goes into dry run and back", async ({ page }) => {
  await page.goto("/rules");
  const toggle = page
    .getByTestId("rule-row")
    .first()
    .getByRole("switch", { name: / dry run$/ });
  const before = await toggle.getAttribute("aria-checked");
  await toggle.click();
  await expect(toggle).not.toHaveAttribute("aria-checked", before!);
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", before!);
});

test("a rule's changes are backtested over the robot's history", async ({ page }) => {
  test.skip(!process.env.E2E_BASE_URL, "the fake has no jarvis tool to replay with: the demo does");
  await page.goto("/rules");
  const first = page.getByTestId("rule-row").first().getByRole("link");
  const id = (await first.textContent())!.trim();
  await first.click();
  await page.getByRole("link", { name: "Try changes in the rule lab" }).click();
  await expect(page.getByRole("textbox", { name: "Draft rules YAML" })).toHaveValue(
    new RegExp(`- id: ${id}\n`),
  );
  await page.getByRole("button", { name: "Backtest" }).click();
  await expect(page.getByTestId("lab-summary")).toContainText("Replayed", { timeout: 30_000 });
  const row = page.getByTestId("lab-rule");
  await expect(row).toContainText(id);
  await expect(row).toContainText("replaces the live rule");
  // Unchanged, it decides what the robot's rule decided.
  await expect(page.getByTestId("lab-same")).toBeVisible();
});

test("a rule operators keep doing by hand is drafted in the rule lab", async ({ page }) => {
  // The suggestion itself is the gateway's (test_suggest.py): here, what the dashboard makes of one.
  const yaml =
    "schema: jarvis.rules/v1\nrules:\n- id: sensor-scan-stale-restart-lifecycle-node\n" +
    "  when:\n    event: { kind: FAULT_STATE, code: sensor.scan.stale, attrs.state: ACTIVE }\n" +
    "  then:\n  - recover: { plan: restart-lifecycle-node }\n";
  const at = new Date(Date.now() - 30 * 60_000).toISOString();
  await page.route("**/api/v1/rules:suggest*", (route) =>
    route.fulfill({
      json: {
        hours: 168,
        events: 40,
        suggestions: [
          {
            id: "sensor-scan-stale-restart-lifecycle-node",
            code: "sensor.scan.stale",
            plan: "restart-lifecycle-node",
            args: {},
            runs: 3,
            succeeded: 3,
            operators: ["alice", "bob"],
            components: ["lidar"],
            first: at,
            last: at,
            median_after_s: 75,
            evidence: [],
            yaml,
          },
        ],
      },
    }),
  );
  await page.goto("/rules");
  await expect(page.getByTestId("suggested-count")).toHaveText("1 suggested");
  await page.getByRole("link", { name: /Rule lab/ }).click();
  const suggestion = page.getByTestId("lab-suggestion");
  await expect(suggestion).toContainText("restart-lifecycle-node after sensor.scan.stale");
  await expect(suggestion).toContainText("Run by hand 3 times by alice, bob on lidar");
  await suggestion.getByRole("button", { name: "Draft the rule" }).click();
  await expect(page.getByRole("textbox", { name: "Draft rules YAML" })).toHaveValue(yaml);
  await expect(page.getByRole("combobox", { name: "Window" })).toHaveValue("1");
});

test("events open in a drawer with their causal chain", async ({ page }) => {
  await page.goto("/events");
  await expect(page.getByTestId("event-row").first()).toBeVisible();
  await page.getByTestId("event-row").first().click();
  const sheet = page.getByRole("dialog");
  await expect(sheet.getByText("Caused by")).toBeVisible();
  await expect(page).toHaveURL(/event=/);
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
});

test("an incident has a timeline", async ({ page }) => {
  await page.goto("/incidents");
  await page.getByTestId("incident-row").first().getByRole("link").click();
  await expect(page.getByRole("img", { name: "Incident timeline" })).toBeVisible();
  await expect(page.getByText(/^Events \(\d+\)$/)).toBeVisible();
});

test("analytics show health per component and the Pareto of faults", async ({ page }) => {
  await page.goto("/analytics?hours=24");
  await expect(page.getByTestId("component-row").first()).toBeVisible();
  await expect(page.getByRole("img", { name: "Pareto of fault codes" })).toBeVisible();
});

test("an operator asks the agent about an incident", async ({ page }) => {
  await page.goto("/incidents");
  await page.getByTestId("incident-row").first().getByRole("link").click();
  await page.getByRole("button", { name: "Ask agent" }).click();
  const box = page.getByTestId("ask-box");
  await expect(box).toContainText("about incident");
  await box.getByRole("textbox", { name: "Question" }).fill("What happened?");
  await box.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(box.getByTestId("ask-progress")).toContainText("window");
  await expect(box.getByTestId("insight-card")).toBeVisible({ timeout: 30_000 });
});

test("the fleet view shows this robot, and leads to its faults", async ({ page }) => {
  // No fleet configured on this gateway: this robot alone (its faults depend on the tests before).
  await page.goto("/fleet");
  await expect(page.getByTestId("fleet-summary")).toContainText("1 robot");
  const card = page.getByTestId("fleet-robot");
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("this robot");
  await card.getByRole("link", { name: "Its faults" }).click();
  await expect(page).toHaveURL(/\/faults/);
});
