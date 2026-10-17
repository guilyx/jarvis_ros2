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
