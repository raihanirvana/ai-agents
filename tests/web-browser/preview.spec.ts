import { expect, test, type BrowserContext, type Page } from "@playwright/test";

/**
 * Real Vite UI + real API + real PreviewService (Docker container without network, loopback proxy on localhost).
 * Tickets reach UAT through the real domain transitions with contract fixtures for QA evidence; the previewed page is
 * target code that probes the control plane from the preview origin. Run with playwright.preview.config.ts.
 */
const CONTROL = "http://127.0.0.1:19862";
const PREVIEW = "http://localhost:19863/";
const CODE = "test-only-preview-code-0123456789abcdef";

async function login(page: Page) {
  await page.goto("/");
  const code = page.getByLabel("Kode login lokal");
  if (await code.isVisible({ timeout: 3000 }).catch(() => false)) {
    await code.fill(CODE);
    await page.getByRole("button", { name: "Masuk" }).click();
  }
  await expect(page.getByRole("heading", { name: "Proyek", level: 1 })).toBeVisible();
}

async function openTicket(page: Page, title: string) {
  if (!(await page.locator(".board").isVisible().catch(() => false))) {
    await login(page);
    await page.locator(".project-list button").first().click();
  }
  await page.locator("article.card", { hasText: title }).getByRole("button", { name: new RegExp(title) }).click();
  await expect(page.getByRole("heading", { name: new RegExp(title), level: 2 })).toBeVisible();
  return page.locator('section[aria-label="Preview untuk UAT"]');
}

const json = async (path: string) => (await fetch(CONTROL + path)).json();

/**
 * WSL2 forwards a Linux loopback listener to Windows with a short delay (and again after a listener is replaced on the
 * same port). The product is ready when the panel says so; this only waits for the test host's forwarding.
 */
async function forwarded(expected: string) {
  await expect.poll(async () => {
    try { return (await (await fetch(PREVIEW, { signal: AbortSignal.timeout(2000) })).text()).includes(expected); } catch { return false; }
  }, { timeout: 30000, intervals: [250] }).toBe(true);
}

test("a preview is opened from the tested artifact on localhost with no control credential, and stop closes it", async ({ page, context }) => {
  const sent: Promise<{ url: string; headers: { [k: string]: string } }>[] = [];
  context.on("request", (r) => { if (r.url().startsWith("http://localhost:19863")) sent.push(r.allHeaders().then((headers) => ({ url: r.url(), headers }))); });
  const panel = await openTicket(page, "Preview A");
  const control = await context.cookies("http://127.0.0.1:19861");
  expect(control.map((c) => c.name)).toContain("ai_team_session");  // a control credential exists, so the check below means something
  await expect(panel.getByText("Belum dibuka")).toBeVisible();

  await panel.getByRole("button", { name: "Buka preview" }).click();
  await expect(panel).toHaveAttribute("data-preview-status", "ready");
  await expect(panel.getByText("coffee-menu-v1")).toBeVisible();
  const link = panel.getByRole("link", { name: "Buka preview di tab baru" });
  await expect(link).toHaveAttribute("href", PREVIEW);
  await expect(link).toHaveAttribute("rel", /noopener/);

  await forwarded("Latte 4.00");
  const [popup] = await Promise.all([context.waitForEvent("page"), link.click()]);
  await expect(popup.locator("#app")).toHaveText("Latte 4.00");
  await expect(popup.locator("#probe")).not.toHaveText("pending");
  // The target page tried to read and mutate the control plane: both attempts are blocked.
  expect(JSON.parse(await popup.locator("#probe").innerText())).toEqual({ get: "blocked", post: "blocked" });

  const requests = await Promise.all(sent);
  expect(requests.length).toBeGreaterThan(1);
  for (const r of requests) {
    expect(Object.keys(r.headers).map((k) => k.toLowerCase())).not.toEqual(expect.arrayContaining(["cookie"]));
    for (const forbidden of ["cookie", "authorization", "x-csrf-token"]) expect(r.headers[forbidden]).toBeUndefined();
  }
  expect(await context.cookies(PREVIEW)).toEqual([]);
  // A top-level navigation to the control-host alias carries its host-only cookie.
  // The preview supervisor must refuse it before the request reaches the target.
  const alias = await context.newPage();
  const rejected = await alias.goto("http://127.0.0.1:19863/");
  expect(rejected?.status()).toBe(403);
  await alias.close();

  // Server side: everything the preview origin sent to the control API was refused and carried no credential.
  const fromPreview = (await json("/captured")).filter((e: { origin: string }) => e.origin === "http://localhost:19863");
  expect(fromPreview.length).toBeGreaterThan(0);
  for (const e of fromPreview) expect(e).toMatchObject({ status: 403, cookie: false, authorization: false });
  expect(await json("/containers")).toHaveLength(1);

  await page.bringToFront();
  await panel.getByRole("button", { name: "Hentikan preview" }).click();
  await expect(panel).toHaveAttribute("data-preview-status", "stopped");
  await expect(panel.getByText(/dihentikan oleh Anda/)).toBeVisible();
  expect(await json("/containers")).toEqual([]);
  await expect(popup.goto(PREVIEW)).rejects.toThrow(/ERR_CONNECTION_REFUSED/);
  await popup.close();
});

test("switching closes the previous preview, only one container exists, and reopening serves the same artifact", async ({ page, context }) => {
  const c = await openTicket(page, "Preview C");
  await c.getByRole("button", { name: "Buka preview" }).click();
  await expect(c).toHaveAttribute("data-preview-status", "ready");
  const tab = await context.newPage();
  await forwarded("Latte 4.00");
  await tab.goto(PREVIEW);
  await expect(tab.locator("#app")).toHaveText("Latte 4.00");

  const b = await openTicket(page, "Preview B");
  await b.getByRole("button", { name: "Buka preview" }).click();
  await expect(b).toHaveAttribute("data-preview-status", "ready");
  expect(await json("/containers")).toHaveLength(1);
  await forwarded("Mocha 5.00");
  await tab.reload();
  await expect(tab.locator("#app")).toHaveText("Mocha 5.00");

  const again = await openTicket(page, "Preview C");
  await expect(again).toHaveAttribute("data-preview-status", "stopped");
  await expect(again.getByText(/digantikan preview lain/)).toBeVisible();
  await again.getByRole("button", { name: "Buka ulang preview" }).click();
  await expect(again).toHaveAttribute("data-preview-status", "ready");
  await forwarded("Latte 4.00");
  await tab.reload();
  await expect(tab.locator("#app")).toHaveText("Latte 4.00");
  expect(await json("/containers")).toHaveLength(1);
  await again.getByRole("button", { name: "Hentikan preview" }).click();
  await expect(again).toHaveAttribute("data-preview-status", "stopped");
  await tab.close();
});

test("feedback that requests changes sends the ticket back to development and closes the preview of that candidate", async ({ page }) => {
  const panel = await openTicket(page, "Preview D");
  await panel.getByRole("button", { name: "Buka preview" }).click();
  await expect(panel).toHaveAttribute("data-preview-status", "ready");
  await page.getByLabel("Minta perubahan").fill("Harga latte seharusnya 4.50");
  await page.getByRole("button", { name: "Kirim permintaan perubahan" }).click();
  await expect(page.locator('.column[data-phase="development"] article.card', { hasText: "Preview D" })).toBeVisible();
  await expect(panel).toHaveAttribute("data-preview-status", "stopped", { timeout: 20000 });
  await expect(panel.getByText(/kandidat diganti/)).toBeVisible();
  expect(await json("/containers")).toEqual([]);
  await expect(panel.getByRole("button", { name: /Buka/ })).toHaveCount(0);  // the old candidate is no longer previewable
});

test("an artifact that went missing makes the preview unavailable instead of serving something else", async ({ page }) => {
  const before = (await json("/previews")).length;
  await fetch(CONTROL + "/break-bundle", { method: "POST", body: JSON.stringify({ title: "Preview E" }) });
  const panel = await openTicket(page, "Preview E");
  await panel.getByRole("button", { name: "Buka preview" }).click();
  const alert = page.getByRole("alert").filter({ hasText: "artifact_unavailable" });
  await expect(alert).toBeVisible();
  await expect(panel).toHaveAttribute("data-preview-status", "none");
  expect(await json("/previews")).toHaveLength(before);
  expect(await json("/containers")).toEqual([]);
});
