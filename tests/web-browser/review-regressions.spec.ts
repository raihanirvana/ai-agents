import { expect, test, type Page } from "@playwright/test";
import type { Candidate, TicketDetail, Message } from "../../contracts/api/types";
import { API, CORS, fresh, mockApi, ticket, run, artifact } from "./review-fixtures";

const detail = (phase: "scope_review" | "uat" = "scope_review"): TicketDetail => ({
  ticket: ticket({ phase }), cursor: 5, dependencies: [], approvals: [], messages: [], candidates: [],
  versions: [{ version: 2, title: "Menu kopi", description: "Deskripsi awal", scope: { title: "Menu kopi", uac: [{ id: "UAC-1", text: "Menu tampil", mode: "manual" }], reverts_candidate_id: "previous-candidate" },
    uac: [{ id: "UAC-1", text: "Menu tampil", mode: "manual" }] }],
});
const candidate = (id = "c1"): Candidate => ({
  id, ticket_id: "t1", scope_version: 2, status: "verified", commit_sha: "a".repeat(40), base_sha: "b".repeat(40),
  target_artifact_id: `target-${id}`, target_digest: id.repeat(32), commit_artifact_id: "commit", build_artifact_id: "build",
  evidence_ids: ["qa", "preview-smoke"], preview: { verification_id: `v-${id}` },
  verifications: [{ id: `v-${id}`, status: "passed", target_digest: id.repeat(32), evidence_ids: ["qa"], counts: { passed: 1 }, results: {}, uac_coverage: {} }],
});
async function events(page: Page) {
  await page.addInitScript(() => {
    let stream: { listeners: Map<string, (e: { data: string }) => void>; onopen: (() => void) | null; onerror: (() => void) | null };
    (window as any).EventSource = class {
      listeners = new Map(); onerror = null; onopen = null;
      constructor() { stream = this; }
      addEventListener(name: string, fn: any) { this.listeners.set(name, fn); }
      close() {}
    };
    let cursor = 5;
    (window as any).reviewEvent = (type = "ticket.updated") => stream.listeners.get("state")?.({ data: JSON.stringify({ cursor: ++cursor, type, project_id: "p1", entity_type: null, entity_id: null, run_id: null, payload: {} }) });
    (window as any).reviewReconnect = () => { stream.onerror?.(); };
    (window as any).reviewOpen = () => { stream.onopen?.(); };
  });
}
const refresh = (page: Page, type = "ticket.updated") => page.evaluate(type => (window as any).reviewEvent(type), type);

test("scope confirmation expires when its version changes", async ({ page }) => {
  const s = fresh(); s.detail = detail(); s.tickets = [s.detail.ticket];
  await events(page); await mockApi(page, s); await page.goto("/#/p/p1/t/t1");
  await page.getByRole("button", { name: "Setujui scope v2", exact: true }).click();
  s.detail.ticket = { ...s.detail.ticket, revision: 8, scope_version: 3 }; s.tickets = [s.detail.ticket];
  s.detail.versions.push({ ...s.detail.versions[0], version: 3 });
  await refresh(page); await expect(page.locator(".ticket-head")).toContainText("scope v3");
  await expect(page.getByRole("button", { name: "Ya, setujui", exact: true })).toHaveCount(0);
  expect(s.posts).toHaveLength(0);
});

test("editing retains its original revision and revert identity after a concurrent update", async ({ page }) => {
  const s = fresh(); s.detail = detail(); s.tickets = [s.detail.ticket];
  await events(page); await mockApi(page, s); await page.goto("/#/p/p1/t/t1");
  await page.getByRole("button", { name: "Edit scope", exact: true }).click();
  await page.getByLabel("Judul", { exact: true }).fill("Draf pengguna");
  s.detail.ticket = { ...s.detail.ticket, revision: 8 }; s.tickets = [s.detail.ticket];
  await refresh(page); await expect(page.locator(".ticket-head")).toContainText("rev 8");
  await page.getByRole("button", { name: "Simpan sebagai versi baru" }).click();
  await expect.poll(() => s.posts.length).toBe(1);
  expect(s.posts[0].body).toMatchObject({ expected_revision: 7, document: { reverts_candidate_id: "previous-candidate" } });
});

test("UAT submits the complete candidate evidence including preview smoke", async ({ page }) => {
  const s = fresh(); s.detail = detail("uat"); s.detail.candidates = [candidate()]; s.tickets = [s.detail.ticket];
  await mockApi(page, s); await page.goto("/#/p/p1/t/t1");
  await page.getByLabel("UAC-1: Menu tampil").check();
  await page.getByRole("button", { name: "Terima (UAT)" }).click();
  await page.getByRole("button", { name: "Ya, terima target ini" }).click();
  await expect.poll(() => s.posts.length).toBe(1);
  expect(s.posts[0].body).toMatchObject({ evidence_ids: ["qa", "preview-smoke"] });
});

test("manual UAC and confirmation do not transfer to a new target", async ({ page }) => {
  const s = fresh(); s.detail = detail("uat"); s.detail.candidates = [candidate()]; s.tickets = [s.detail.ticket];
  await events(page); await mockApi(page, s); await page.goto("/#/p/p1/t/t1");
  await page.getByLabel("UAC-1: Menu tampil").check();
  await page.getByRole("button", { name: "Terima (UAT)" }).click();
  s.detail.candidates = [candidate("c2")]; s.detail.ticket = { ...s.detail.ticket, revision: 8 }; s.tickets = [s.detail.ticket];
  await refresh(page); await expect(page.locator(".uat h4")).toContainText("c2c2");
  await expect(page.getByLabel("UAC-1: Menu tampil")).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Ya, terima target ini" })).toHaveCount(0);
});

test("unchecking manual UAC disables an open confirmation", async ({ page }) => {
  const s = fresh(); s.detail = detail("uat"); s.detail.candidates = [candidate()]; s.tickets = [s.detail.ticket];
  await mockApi(page, s); await page.goto("/#/p/p1/t/t1");
  await page.getByLabel("UAC-1: Menu tampil").check();
  await page.getByRole("button", { name: "Terima (UAT)" }).click();
  await page.getByLabel("UAC-1: Menu tampil").uncheck();
  await expect(page.getByRole("button", { name: "Ya, terima target ini" })).toBeDisabled();
});

test("HTTP 502 preserves the idempotency key on a user retry", async ({ page }) => {
  const s = fresh(); await mockApi(page, s);
  const keys: string[] = [];
  await page.route(`${API}/projects/p1/messages`, async route => {
    if (route.request().method() !== "POST") return route.fallback();
    keys.push(route.request().headers()["idempotency-key"]);
    return route.fulfill({ status: keys.length === 1 ? 502 : 200, headers: CORS, json: keys.length === 1 ? {} : { message: {} } });
  });
  await page.goto("/#/p/p1"); await page.getByLabel("Pesan untuk PO").fill("Buat menu");
  await page.getByRole("button", { name: "Kirim", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("502");
  await page.getByRole("button", { name: "Kirim", exact: true }).click();
  await expect.poll(() => keys.length).toBe(2); expect(keys[1]).toBe(keys[0]);
});

test("blocking questions appear in chat and answer through their bound run", async ({ page }) => {
  const s = fresh();
  const q: Message = { id: "q1", project_id: "p1", ticket_id: null, thread_id: "job:r1", seq: 1,
    sender: "agent:po", recipient: "user", kind: "input_request", body: "Mata uang apa?", reply_to: null,
    attachment_ids: [], created_at: "2026-10-05T01:00:00Z", metadata: {},
    input: { id: "q1", project_id: "p1", ticket_id: null, thread_id: "job:r1", scope_version: null,
      recipient: "user", job_id: "r1", generation: 1, status: "open", answer_id: null, answer: null, attempt_status: null } };
  s.messages = [q]; s.runs = [run({ role: "po", ticket_id: null, scope_version: null, status: "waiting_input", request_id: "q1" })];
  await mockApi(page, s); await page.goto("/#/p/p1");
  await expect(page.getByText("Mata uang apa?", { exact: true })).toBeVisible();
  await page.getByLabel("Jawaban Anda").fill("IDR"); await page.getByRole("button", { name: "Jawab", exact: true }).click();
  await expect.poll(() => s.posts.length).toBe(1);
  expect(s.posts[0]).toMatchObject({ path: "/runs/r1/input", body: { request_id: "q1", expected_revision: 3, generation: 1, scope_version: null, answer: "IDR" } });
});

test("opening brief starts with existing content and retains the edit revision", async ({ page }) => {
  const s = fresh(); await mockApi(page, s); await page.goto("/#/p/p1");
  await page.locator("details.brief summary").click();
  await expect(page.getByLabel("Brief proyek")).toHaveValue("Brief");
});

test("changing batch selection invalidates the first approval click", async ({ page }) => {
  const s = fresh(); s.tickets = [ticket({ phase: "scope_review" }), ticket({ id: "t2", number: 2, phase: "scope_review" })];
  await mockApi(page, s); await page.goto("/#/p/p1");
  await page.getByLabel("Pilih tiket 1 untuk approval").check();
  await page.getByRole("button", { name: "Setujui scope terpilih (1)" }).click();
  await page.getByLabel("Pilih tiket 2 untuk approval").check();
  await expect(page.getByRole("button", { name: "Ya, setujui scope", exact: true })).toHaveCount(0);
  expect(s.posts).toHaveLength(0);
});

test("reconnected stream without new state events restores the live indicator", async ({ page }) => {
  const s = fresh(); await events(page); await mockApi(page, s); await page.goto("/#/p/p1");
  await expect(page.locator(".composer")).toBeVisible();
  await page.evaluate(() => (window as any).reviewReconnect());
  await expect(page.locator(".topbar")).toContainText("Menyambung ulang");
  await page.evaluate(() => (window as any).reviewOpen());
  await expect(page.locator(".topbar")).toContainText("Terhubung");
});

test("SSE cursor cannot skip messages between the initial reads and stream setup", async ({ page }) => {
  const s = fresh(); await events(page); await mockApi(page, s);
  let boardReads = 0;
  let firstMessagesRead!: () => void;
  const firstMessages = new Promise<void>(resolve => { firstMessagesRead = resolve; });
  let messagesReads = 0;
  const m: Message = { id: "answer", project_id: "p1", ticket_id: null, thread_id: "chat:p1", seq: 1,
    sender: "agent:po", recipient: "user", kind: "chat", body: "Jawaban di antara snapshot", reply_to: null,
    metadata: {}, attachment_ids: [], created_at: "2026-10-05T01:00:00Z" };
  await page.route(`${API}/projects/p1/tickets`, async route => {
    if (++boardReads === 2) { await firstMessages; s.messages = [m]; }
    return route.fulfill({ headers: CORS, json: { project: { id: "p1", name: "Mock", brief: "Brief", brief_version: 1, revision: 4 }, tickets: [], runs: [], cursor: boardReads === 1 ? 5 : 6 } });
  });
  await page.route(`${API}/projects/p1/messages`, async route => {
    if (++messagesReads === 1) {
      await route.fulfill({ headers: CORS, json: { messages: [], cursor: 5 } });
      firstMessagesRead();
    } else await route.fallback();
  });
  await page.goto("/#/p/p1");
  await expect(page.getByText(m.body, { exact: true })).toBeVisible();
});

test("handoff replies remain visible in ticket history", async ({ page }) => {
  const s = fresh(); s.detail = detail(); s.tickets = [s.detail.ticket];
  s.detail.messages = [{ id: "reply", project_id: "p1", ticket_id: "t1", thread_id: "handoff", seq: 2,
    sender: "agent:technical-lead", recipient: "agent:developer", kind: "chat", body: "Gunakan kontrak versi 2", reply_to: "question",
    metadata: {}, attachment_ids: [], created_at: "2026-10-05T01:00:00Z" }];
  await mockApi(page, s); await page.goto("/#/p/p1/t/t1");
  await expect(page.getByText("Gunakan kontrak versi 2", { exact: true })).toBeVisible();
});

test("queued generation zero does not issue an invalid log request", async ({ page }) => {
  const s = fresh(); s.runs = [run({ status: "queued", generation: 0 })]; await mockApi(page, s);
  const logs: string[] = [];
  await page.route(`${API}/runs/r1/logs*`, route => { logs.push(route.request().url()); return route.fulfill({ status: 422, headers: CORS, json: { error: { code: "invalid", message: "generation must be >= 1", details: {} } } }); });
  await page.goto("/#/p/p1"); await page.getByRole("tab", { name: /Aktivitas/ }).click();
  await page.getByRole("button", { name: "Detail", exact: true }).click();
  await expect(page.getByText("Belum ada log.", { exact: true })).toBeVisible();
  expect(logs).toHaveLength(0);
});

test("artifact availability updates on events without reloading the ticket", async ({ page }) => {
  const s = fresh(); s.detail = detail("uat"); s.detail.candidates = [candidate()]; s.tickets = [s.detail.ticket];
  s.artifacts.qa = artifact("qa"); await events(page); await mockApi(page, s); await page.goto("/#/p/p1/t/t1");
  await expect(page.locator('[data-artifact="qa"] a')).toBeVisible();
  s.artifacts.qa = artifact("qa", { availability: "unavailable", unavailable_reason: "missing" }); s.detail.cursor = 6;
  await refresh(page, "artifact.unavailable"); await expect(page.locator('[data-artifact="qa"]')).toContainText("tidak tersedia: missing");
  await expect(page.locator('[data-artifact="qa"] a')).toHaveCount(0);
});

test("UAT decision succeeds against real API with the distinct smoke receipt", async ({ page, request }) => {
  // Synthetic domain/verification receipts, not a real QA harness or provider.
  const seeded = await request.post("http://127.0.0.1:19852/fixture/uat", { data: {} });
  expect(seeded.status()).toBe(200);
  const ids = await seeded.json();
  await page.goto("/"); await page.getByLabel("Kode login lokal").fill("test-only-web-code-0123456789abcdef");
  await page.getByRole("button", { name: "Masuk", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Proyek", exact: true })).toBeVisible();
  await page.goto(`/#/p/${ids.project_id}/t/${ids.ticket_id}`);
  await expect(page.locator(".ticket-head")).toContainText("uat");
  await page.getByRole("button", { name: "Terima (UAT)" }).click();
  const response = page.waitForResponse(r => r.url().endsWith("/uat-decisions") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Ya, terima target ini" }).click();
  const r = await response; expect(r.status()).toBe(200);
  expect(r.request().postDataJSON().evidence_ids).toEqual(ids.evidence_ids);
  await expect(page.locator(".ticket-head")).toContainText("integrating");
});

test("project creation retry retains its key, while a different body gets another key", async ({ page }) => {
  const s = fresh(); await mockApi(page, s);
  const posts: { key: string; name: string }[] = [];
  await page.route(`${API}/projects`, async route => {
    if (route.request().method() !== "POST") return route.fallback();
    posts.push({ key: route.request().headers()["idempotency-key"], name: route.request().postDataJSON().name });
    return route.fulfill({ status: 502, headers: CORS, json: {} });
  });
  await page.goto("/"); await page.getByLabel("Nama", { exact: true }).fill("Kedai");
  await page.getByRole("button", { name: "Buat proyek", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("502");
  await page.getByRole("button", { name: "Buat proyek", exact: true }).click();
  await expect.poll(() => posts.length).toBe(2); expect(posts[1].key).toBe(posts[0].key);
  await expect(page.getByRole("button", { name: "Buat proyek", exact: true })).toBeEnabled();
  await page.getByLabel("Nama", { exact: true }).fill("Kedai lain");
  await page.getByRole("button", { name: "Buat proyek", exact: true }).click();
  await expect.poll(() => posts.length).toBe(3); expect(posts[2].key).not.toBe(posts[0].key);
});

test("repair authorization never sends more than the API maximum of three cycles", async ({ page }) => {
  const s = fresh(); s.detail = detail(); s.detail.ticket.blocker = { reason: "needs_human" }; s.tickets = [s.detail.ticket];
  await mockApi(page, s); await page.goto("/#/p/p1/t/t1");
  await expect(page.getByLabel("Izinkan siklus perbaikan tambahan")).toHaveAttribute("max", "3");
  await page.getByLabel("Izinkan siklus perbaikan tambahan").fill("5");
  await page.getByRole("button", { name: "Izinkan 3 siklus", exact: true }).click();
  await expect.poll(() => s.posts.length).toBe(1);
  expect(s.posts[0].body).toMatchObject({ additional_cycles: 3 });
});

test("a concurrent real scope edit cannot be overwritten by a draft after SSE refresh", async ({ page, request }) => {
  const seed = await request.post("http://127.0.0.1:19852/fixture/scope", { data: {} }); expect(seed.status()).toBe(200);
  const ids = await seed.json();
  await page.goto("/"); await page.getByLabel("Kode login lokal").fill("test-only-web-code-0123456789abcdef");
  await page.getByRole("button", { name: "Masuk", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Proyek", exact: true })).toBeVisible();
  await page.goto(`/#/p/${ids.project_id}/t/${ids.ticket_id}`);
  await page.getByRole("button", { name: "Edit scope", exact: true }).click();
  await page.getByLabel("Judul", { exact: true }).fill("Draf lama");
  const changed = await page.evaluate(async ({ ticket_id, base }) => {
    const headers = { "Content-Type": "application/json", "X-CSRF-Token": (await (await fetch(base + "/auth/session", { credentials: "include" })).json()).csrf_token, "Idempotency-Key": crypto.randomUUID() };
    const d = await (await fetch(`${base}/tickets/${ticket_id}`, { credentials: "include" })).json();
    const r = await fetch(`${base}/tickets/${ticket_id}/scope-versions`, { method: "POST", credentials: "include", headers,
      body: JSON.stringify({ expected_revision: d.ticket.revision, document: { ...d.versions.at(-1).scope, title: "Perubahan tab lain", description: d.versions.at(-1).description, uac: d.versions.at(-1).uac } }) });
    return r.status;
  }, { ticket_id: ids.ticket_id, base: API });
  expect(changed).toBe(200);
  await expect(page.locator(".ticket-head h2")).toContainText("Perubahan tab lain");
  const response = page.waitForResponse(r => r.url().endsWith("/scope-versions") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Simpan sebagai versi baru" }).click();
  expect((await response).status()).toBe(409);
  await expect(page.getByRole("alert")).toContainText("conflict");
  await expect(page.getByLabel("Judul", { exact: true })).toHaveValue("Draf lama");
  await expect(page.locator(".ticket-head h2")).toContainText("Perubahan tab lain");
});

test("accepted proposal keeps its historical diff against its base version", async ({ page }) => {
  const s = fresh(); s.detail = detail(); s.tickets = [s.detail.ticket];
  s.detail.versions.unshift({ ...s.detail.versions[0], version: 1, title: "Judul awal", scope: { title: "Judul awal", uac: [] } });
  const proposal: Message = { id: "proposal", project_id: "p1", ticket_id: "t1", thread_id: "scope", seq: 1, sender: "agent:po",
    recipient: "user", kind: "chat", body: "Usulan", reply_to: null, metadata: { intent: "scope_proposal", base_version: 1, document: { title: "Menu kopi", uac: [] } },
    attachment_ids: [], created_at: "2026-10-05T01:00:00Z" };
  s.detail.messages = [proposal, { ...proposal, id: "accepted", seq: 2, sender: "user", body: "accepted", reply_to: "proposal", metadata: { intent: "scope_decision" } }];
  await mockApi(page, s); await page.goto("/#/p/p1/t/t1");
  await expect(page.locator(".proposal del").first()).toHaveText("Judul awal");
  await expect(page.locator(".proposal ins").first()).toHaveText("Menu kopi");
});

test("editor preserves description stored outside the scope metadata object", async ({ page }) => {
  const s = fresh(); s.detail = detail(); s.detail.versions[0].scope = { dependencies: [] }; s.tickets = [s.detail.ticket];
  await mockApi(page, s); await page.goto("/#/p/p1/t/t1");
  await page.getByRole("button", { name: "Edit scope", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Deskripsi", exact: true })).toHaveValue("Deskripsi awal");
});
