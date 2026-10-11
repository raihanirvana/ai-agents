import { expect, test } from "@playwright/test";
import type { UiSpec } from "../../contracts/api/types";
import { fresh, mockApi, ticket } from "./review-fixtures";

// Contract-shaped display fixtures; browser rendering evidence, not product QA/provider evidence.
const spec: UiSpec = {
  kind: "ui_spec", revision: 2, summary: "Rancangan katalog",
  tokens: { direction: "Katalog ringkas", colors: [{ name: "accent", value: "#2B6CB0" }, { name: "background", value: "#F6F7F8" }, { name: "text", value: "#1F2933" }],
    fonts: [{ role: "body", family: "Inter", fallback: "sans-serif" }], spacing_px: [4, 8, 16],
    radius: [{ name: "control", px: 6 }] },
  screens: [{ id: "catalog", purpose: "Daftar buku", components: [
    { testid: "add-book", role: "button", name_key: "add", purpose: "Tambah" },
    { testid: "error-a", role: "alert", purpose: "Kesalahan pertama" },
    { testid: "error-b", role: "alert", purpose: "Kesalahan kedua" },
  ] }],
  copy: [{ key: "add", text: "Tambah buku" }, { key: "error", text: "Isi judul <script>contoh</script>" }],
  states: [{ screen: "catalog", kind: "error", copy_key: "error", testid: "error-a" },
    { screen: "catalog", kind: "error", copy_key: "error", testid: "error-b" }],
  feedback: [{ action_testid: "add-book", outcome: "error", kind: "inline", copy_key: "error", target_testid: "error-a" }],
};

test("current UiSpec renders tokens, controls, literal copy and distinct state targets with FAKE label", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => { if (message.type() === "error" && message.text().includes("same key")) errors.push(message.text()); });
  const state = fresh();
  state.tickets = [ticket({})];
  state.detail = { ticket: state.tickets[0], versions: [], dependencies: [], approvals: [], candidates: [], messages: [], cursor: 5,
    ui_spec: { revision: 2, no_ui_change: false, summary: spec.summary, spec, fake: true } };
  await mockApi(page, state);
  await page.goto("/#/p/p1/t/t1");
  const panel = page.getByRole("region", { name: /Spesifikasi UI/ });
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("rev 2");
  await expect(panel).toContainText("FAKE");
  await expect(panel).toContainText("#2B6CB0");
  await expect(panel.locator(".uispec-chip").first()).toHaveCSS("background-color", "rgb(43, 108, 176)");
  await expect(panel.getByText("Inter", { exact: true })).toHaveCSS("font-family", /Inter/);
  await expect(panel).toContainText("4 / 8 / 16 px");
  await expect(panel).toContainText("control 6px");
  await panel.getByText(/Layar catalog:/).click();
  await expect(panel.getByRole("cell", { name: "add-book", exact: true })).toBeVisible();
  await panel.getByText("Copy (2)", { exact: true }).click();
  await expect(panel.getByRole("cell", { name: spec.copy[1].text, exact: true })).toBeVisible();
  await panel.getByText("State dan feedback", { exact: true }).click();
  await expect(panel.getByRole("cell", { name: /catalog:.*error-a/ })).toBeVisible();
  await expect(panel.getByRole("cell", { name: /catalog:.*error-b/ })).toBeVisible();
  await expect(panel.getByRole("cell", { name: /Inline.*error-a/ })).toBeVisible();
  expect(errors).toEqual([]);
});

test("no_ui_change shows the persisted reason and fake origin", async ({ page }) => {
  const state = fresh();
  state.tickets = [ticket({})];
  state.detail = { ticket: state.tickets[0], versions: [], dependencies: [], approvals: [], candidates: [], messages: [], cursor: 5,
    ui_spec: { revision: 0, no_ui_change: true, summary: "Perubahan internal saja", spec: null, fake: true } };
  await mockApi(page, state);
  await page.goto("/#/p/p1/t/t1");
  const panel = page.getByRole("region", { name: /Spesifikasi UI/ });
  await expect(panel).toContainText("Perubahan internal saja");
  await expect(panel).toContainText("FAKE");
  await expect(panel.locator(".uispec-chip")).toHaveCount(0);
});

test("long valid copy stays within the ticket panel at desktop width", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const state = fresh();
  const large = structuredClone(spec);
  large.copy.push({ key: "x".repeat(64), text: "x".repeat(900) });
  state.tickets = [ticket({})];
  state.detail = { ticket: state.tickets[0], versions: [], dependencies: [], approvals: [], candidates: [], messages: [], cursor: 5,
    ui_spec: { revision: 2, no_ui_change: false, summary: large.summary, spec: large, fake: true } };
  await mockApi(page, state);
  await page.goto("/#/p/p1/t/t1");
  await page.getByText("Copy (3)", { exact: true }).click();
  const body = page.locator(".tab-body");
  expect(await body.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
});
