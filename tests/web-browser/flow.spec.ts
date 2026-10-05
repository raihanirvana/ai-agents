import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const CONTROL = "http://127.0.0.1:19852";
const CODE = "test-only-web-code-0123456789abcdef";

const ticket = (key: string, title: string, uac: string[]) => ({
  key, title, description: `${title} untuk kedai kopi`,
  uac: uac.map((text, i) => ({ id: `UAC-${i + 1}`, text })), depends_on_keys: [],
});
const breakdown = {
  kind: "proposal", summary: "Kedai kopi dipecah menjadi tiga tiket.", assumptions: ["Satu outlet"],
  tickets: [
    ticket("profile", "Profil kedai", ["Pengguna melihat nama dan jam buka"]),
    ticket("menu", "Menu kopi", ["Menu menampilkan minuman"]),
    ticket("transaksi", "Transaksi", ["Kasir mencatat pesanan"]),
  ],
};
const revision = (uac: string[], summary = "Revisi menu") => ({
  kind: "revision", summary, title: "Menu kopi", description: "Menu kopi untuk kedai kopi",
  uac: uac.map((text, i) => ({ id: `UAC-${i + 1}`, text })), depends_on_ticket_ids: [],
});

async function script(request: APIRequestContext, replies: unknown[]) {
  const response = await request.post(`${CONTROL}/script`, { data: { replies } });
  expect(response.status()).toBe(204);
}

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("Kode login lokal").fill(CODE);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("heading", { name: "Proyek", level: 1 })).toBeVisible();
}

async function createProject(page: Page, name: string, brief = "") {
  await page.getByLabel("Nama").fill(name);
  if (brief) await page.getByLabel("Brief (opsional)").fill(brief);
  await page.getByRole("button", { name: "Buat proyek" }).click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
}

const column = (page: Page, phase: string) => page.locator(`.column[data-phase="${phase}"]`);
const cards = (page: Page, phase: string) => column(page, phase).locator("article.card");
const titles = (page: Page, phase: string) => cards(page, phase).locator(".card-title").allTextContents();

async function send(page: Page, message: string) {
  await page.getByLabel("Pesan untuk PO").fill(message);
  await page.getByRole("button", { name: "Kirim", exact: true }).click();
}

test("brief to proposal, discussion, edits, accept/reject, batch approval and reload", async ({ page, request }) => {
  await script(request, [breakdown]);
  await login(page);
  await createProject(page, "Kedai Kopi", "Aplikasi kedai kopi: profil, menu, dan transaksi.");

  // Brief -> PO proposal, produced through the real API, queue, supervisor and structured runtime.
  await send(page, "Pecah brief ini menjadi tiket");
  await expect(page.locator(".msg--agent", { hasText: "Proposed 3 ticket(s)" })).toBeVisible();
  await expect(cards(page, "scope_review")).toHaveCount(3);
  expect(await titles(page, "scope_review")).toEqual(expect.arrayContaining([
    expect.stringContaining("Profil kedai"), expect.stringContaining("Menu kopi"), expect.stringContaining("Transaksi")]));
  // Fake output is labelled and never described as real QA.
  await expect(page.locator(".topbar").getByText("FAKE · bukan QA nyata")).toBeVisible();
  await expect(page.locator(".msg--agent").getByText("FAKE · bukan QA nyata").first()).toBeVisible();
  await expect(page.getByText("Asumsi: Satu outlet")).toBeVisible();

  // Nothing is approved by the PO.
  await expect(cards(page, "ready")).toHaveCount(0);

  // Discuss: ask the PO to revise one ticket; first proposal is rejected, the second accepted.
  await page.locator(".created").getByRole("button", { name: /Menu kopi/ }).click();
  await expect(page.getByRole("heading", { name: /Menu kopi/, level: 2 })).toBeVisible();
  await expect(page.getByText("Menu menampilkan minuman")).toBeVisible();

  await script(request, [revision(["Menu menampilkan minuman", "Menu menampilkan harga"], "Tambah harga")]);
  await page.getByLabel("Minta PO merevisi tiket ini").fill("Tambahkan kriteria harga");
  await page.getByRole("button", { name: "Kirim ke PO" }).click();
  const proposal = page.locator(".proposal").first();
  await expect(proposal.getByText("Menunggu keputusan Anda")).toBeVisible();
  await expect(proposal.locator(".diff-added")).toContainText("Menu menampilkan harga");
  await proposal.getByRole("button", { name: "Tolak" }).click();
  await expect(proposal.getByText("Ditolak")).toBeVisible();
  await expect(page.getByRole("heading", { name: /Menu kopi/, level: 2 })).toBeVisible();
  await expect(page.getByText("scope v1").first()).toBeVisible();

  await script(request, [revision(["Menu menampilkan minuman", "Menu menampilkan harga dalam rupiah"], "Harga rupiah")]);
  await page.getByLabel("Minta PO merevisi tiket ini").fill("Harga harus rupiah");
  await page.getByRole("button", { name: "Kirim ke PO" }).click();
  const second = page.locator(".proposal").first();
  await expect(second.getByText("Menunggu keputusan Anda")).toBeVisible();
  await second.getByRole("button", { name: "Terima revisi" }).click();
  await expect(second.getByText("Diterima")).toBeVisible();
  await expect(page.locator(".ticket-head").getByText("scope v2")).toBeVisible();
  await expect(page.locator(".uac")).toContainText("Menu menampilkan harga dalam rupiah");
  // Accepting a revision does not approve it.
  await expect(cards(page, "scope_review")).toHaveCount(3);

  // The user edits scope directly as well; this creates another version.
  await page.getByRole("button", { name: "Edit scope" }).click();
  await page.getByLabel("Teks UAC-1").fill("Menu menampilkan minuman panas dan dingin");
  await page.getByLabel("Mode UAC-1").selectOption("manual");
  await page.getByRole("button", { name: "Simpan sebagai versi baru" }).click();
  await expect(page.locator(".ticket-head").getByText("scope v3")).toBeVisible();
  await expect(page.locator(".uac").getByText("manual")).toBeVisible();

  // Drag/drop changes priority, and the order survives reload because the backend owns it.
  const before = await titles(page, "scope_review");
  const last = cards(page, "scope_review").last();
  await last.dragTo(cards(page, "scope_review").first());
  await expect.poll(() => titles(page, "scope_review").then((t) => t[0])).toBe(before[before.length - 1]);
  const dragged = await titles(page, "scope_review");

  // Reload keeps session, selection, board, conversation.
  await page.reload();
  await expect(page.getByRole("heading", { name: /Menu kopi/, level: 2 })).toBeVisible();
  await expect(cards(page, "scope_review")).toHaveCount(3);
  expect(await titles(page, "scope_review")).toEqual(dragged);
  await page.getByRole("tab", { name: "Chat PO" }).click();
  await expect(page.locator(".msg--agent", { hasText: "Proposed 3 ticket(s)" })).toBeVisible();
  await expect(page.locator(".msg--user", { hasText: "Pecah brief ini menjadi tiket" })).toBeVisible();

  // Explicit batch approval of the exact scope versions shown.
  await column(page, "scope_review").getByLabel("Pilih semua").check();
  await column(page, "scope_review").getByRole("button", { name: /Setujui scope terpilih \(3\)/ }).click();
  await column(page, "scope_review").getByRole("button", { name: "Ya, setujui scope" }).click();
  await expect(cards(page, "scope_review")).toHaveCount(0);
  await expect(cards(page, "ready")).toHaveCount(3);
  await page.reload();
  await expect(cards(page, "ready")).toHaveCount(3);
});

test("a stale command shows the revision conflict and the current data", async ({ browser, request }) => {
  await script(request, [breakdown]);
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const first = await context.newPage();
  await login(first);
  await createProject(first, "Konflik");
  await send(first, "Buat tiket");
  await expect(cards(first, "scope_review")).toHaveCount(3);
  const projectUrl = first.url();
  const staleOrder = await titles(first, "scope_review");

  const second = await context.newPage();
  await second.goto(projectUrl);
  await expect(cards(second, "scope_review")).toHaveCount(3);

  // Freeze the first tab's view so that it submits stale revisions (real SSE refresh would otherwise fix it).
  const frozen: { [url: string]: unknown } = {};
  for (const path of ["**/projects/*/tickets", "**/projects/*/messages*"]) {
    const sample = await first.evaluate(async (pattern) => {
      const base = "http://127.0.0.1:19850";
      const id = location.hash.split("/")[2];
      const url = pattern.includes("messages") ? `${base}/projects/${id}/messages` : `${base}/projects/${id}/tickets`;
      return { url, body: await (await fetch(url, { credentials: "include" })).json() };
    }, path);
    frozen[sample.url] = sample.body;
  }
  await first.route(/\/projects\/[^/]+\/(tickets|messages)(\?.*)?$/, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const url = route.request().url().split("?")[0];
    await route.fulfill({
      json: frozen[url], headers: { "access-control-allow-origin": "http://127.0.0.1:19851", "access-control-allow-credentials": "true" } });
  });

  // The second tab moves a card; every ticket revision in the column advances.
  await cards(second, "scope_review").last().getByRole("button", { name: /Naikkan prioritas/ }).click();
  await expect.poll(() => titles(second, "scope_review").then((t) => t.join("|"))).not.toBe(staleOrder.join("|"));

  await cards(first, "scope_review").nth(1).getByRole("button", { name: /Naikkan prioritas/ }).click();
  const alert = first.getByRole("alert").filter({ hasText: "conflict" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("revisi berubah");
  await context.close();
});

test("stop sends an empty body without expected_revision and the run shows as cancelled", async ({ page, request }) => {
  await script(request, ["HOLD"]);
  await login(page);
  await createProject(page, "Stop run");
  await send(page, "Mulai lalu hentikan");
  await page.getByRole("tab", { name: /Aktivitas/ }).click();
  const run = page.locator(".run").first();
  await expect(run).toHaveAttribute("data-status", /running|queued/);
  await expect(run.getByText("FAKE · bukan QA nyata")).toBeVisible();

  const requestSent = page.waitForRequest((r) => r.method() === "POST" && /\/runs\/[^/]+\/stop$/.test(r.url()));
  await run.getByRole("button", { name: "Stop", exact: true }).click();
  await run.getByRole("button", { name: "Ya, hentikan run" }).click();
  const sent = await requestSent;
  expect(sent.postData()).toBe("{}");
  expect(sent.headers()["idempotency-key"]).toBeTruthy();
  await expect(run).toHaveAttribute("data-status", "cancelled");
  await expect(run.getByRole("button", { name: "Stop", exact: true })).toHaveCount(0);
  await request.post(`${CONTROL}/gate/open`);
});

test("office follows a real persisted team run through SSE and keeps its message thread after reload", async ({ page, request }) => {
  await script(request, ["HOLD"]);
  await login(page);
  await createProject(page, "Kantor langsung");
  await send(page, "Mulai breakdown PO untuk proyek ini");
  await page.getByRole("tab", { name: "Kantor" }).click();

  await expect(page.getByRole("heading", { name: "Kantor tim" })).toBeVisible();
  await expect(page.locator(".office-connection")).toContainText("Status mengikuti event langsung");
  await expect(page.locator(".office-fake")).toContainText("run FAKE");
  await expect(page.locator(".office-roles").getByRole("button", { name: /Product Owner/ })).toContainText("Menyusun rencana");
  await expect(page.getByLabel("Konteks Product Owner")).toContainText("Mulai breakdown PO untuk proyek ini");

  await page.getByRole("tab", { name: /Aktivitas/ }).click();
  const active = page.locator(".run").first();
  await expect(active).toHaveAttribute("data-status", /running|queued/);
  await active.getByRole("button", { name: "Stop", exact: true }).click();
  await active.getByRole("button", { name: "Ya, hentikan run" }).click();
  await expect(active).toHaveAttribute("data-status", "cancelled");

  await page.getByRole("tab", { name: "Kantor" }).click();
  await expect(page.locator(".office-roles").getByRole("button", { name: /Product Owner/ })).toContainText("Siaga");
  await expect(page.getByLabel("Konteks Product Owner")).toContainText("Mulai breakdown PO untuk proyek ini");
  await page.reload();
  await page.getByRole("tab", { name: "Kantor" }).click();
  await expect(page.getByLabel("Konteks Product Owner")).toContainText("Mulai breakdown PO untuk proyek ini");
  await request.post(`${CONTROL}/gate/open`);
});
