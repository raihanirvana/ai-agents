import { expect, test, type Page } from "@playwright/test";
import type { Artifact, Board, Candidate, Message, Project, Run, Ticket, TicketDetail } from "../../contracts/api/types";

/**
 * Display states that the fake-provider fixture cannot reach on demand (quota wait, user questions, evidence that
 * went missing, UAT identity). The API is replaced by contract-shaped fixtures: these tests prove how the GUI
 * renders and what it sends, not that a backend produces those states (DEV-008 and the domain suites do).
 */
const API = "http://127.0.0.1:19850";
const CORS = {
  "access-control-allow-origin": "http://127.0.0.1:19851", "access-control-allow-credentials": "true",
  "access-control-allow-headers": "content-type,x-csrf-token,idempotency-key", "access-control-allow-methods": "GET,POST,OPTIONS",
};

const project: Project = { id: "p1", name: "Mock proyek", mode: "new", brief: "Brief", brief_version: 1, revision: 4, onboarding: "pending", accepted_tip: null };
const ticket = (over: Partial<Ticket>): Ticket => ({
  id: "t1", project_id: "p1", number: 1, title: "Menu kopi", phase: "development", revision: 7, scope_version: 2,
  priority: 0, blocker: null, repair_cycles: 0, repair_limit: 3, ...over });
const run = (over: Partial<Run>): Run => ({
  id: "r1", project_id: "p1", ticket_id: "t1", scope_version: 2, revision: 3, generation: 1, role: "developer",
  runtime: "hermes", fake: false, lane: "execution", stage: "implement", status: "running", attempt: 1, usage: { model_calls: 2 },
  attempt_usage: {}, limits: { model_calls: 6 }, result: null, request_id: null, context_artifact_id: null, available_at: null, ...over });

interface State {
  tickets: Ticket[]; runs: Run[]; messages: Message[]; detail: TicketDetail | null;
  artifacts: { [id: string]: Artifact }; runInput: { [id: string]: Message | null };
  posts: { path: string; body: unknown; key: string | undefined }[]; events: "open" | "abort";
}
const fresh = (): State => ({ tickets: [], runs: [], messages: [], detail: null, artifacts: {}, runInput: {}, posts: [], events: "abort" });

async function mockApi(page: Page, state: State) {
  await page.route(`${API}/**`, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body, headers: CORS });
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers: CORS });
    if (request.method() === "POST") {
      state.posts.push({ path, body: request.postDataJSON(), key: request.headers()["idempotency-key"] });
      return json({ ...(path.endsWith("/stop") ? { run: run({ status: "cancelled" }), cleanup: "supervisor_pending" } : {}), answer_id: "a1", resumed: true });
    }
    if (path === "/auth/session") return json({ csrf_token: "csrf" });
    if (path === "/projects") return json({ projects: [project] });
    if (path === "/projects/p1/tickets") return json({ project, tickets: state.tickets, runs: state.runs, cursor: 5 } satisfies Board);
    if (path === "/projects/p1/messages") return json({ messages: state.messages, cursor: 5 });
    if (path === "/projects/p1/events") return state.events === "abort" ? route.abort("failed") : route.fulfill({ status: 200, contentType: "text/event-stream", body: ": open\n\n", headers: CORS });
    if (/^\/tickets\/[^/]+$/.test(path)) return state.detail ? json(state.detail) : json({ error: { code: "not_found", message: "no", details: {} } }, 404);
    const runMatch = /^\/runs\/([^/]+)(\/logs)?$/.exec(path);
    if (runMatch) {
      if (runMatch[2]) return json({ messages: [] });
      const found = state.runs.find((r) => r.id === runMatch[1]);
      return found ? json({ run: found, input: state.runInput[found.id] ?? null }) : json({ error: { code: "not_found", message: "no", details: {} } }, 404);
    }
    const artifact = /^\/artifacts\/([^/]+)$/.exec(path);
    if (artifact) return state.artifacts[artifact[1]] ? json({ artifact: state.artifacts[artifact[1]] }) : json({ error: { code: "not_found", message: "tidak ada", details: {} } }, 404);
    return json({ error: { code: "not_found", message: path, details: {} } }, 404);
  });
}

const artifact = (id: string, over: Partial<Artifact> = {}): Artifact => ({
  id, project_id: "p1", kind: "evidence", storage: "file", checksum: "c".repeat(64), size_bytes: 10, availability: "available",
  unavailable_reason: null, metadata: {}, ...over });

test("waiting quota/input, blockers and the fake label are visible; stop sends an empty body", async ({ page }) => {
  const state = fresh();
  state.tickets = [
    ticket({ id: "t1", number: 1, title: "Menu kopi" }),
    ticket({ id: "t2", number: 2, title: "Transaksi", blocker: { reason: "needs_human", resolution: "authorize" } }),
  ];
  const question: Message = {
    id: "m-q", project_id: "p1", ticket_id: "t1", thread_id: "ask:1", seq: 1, sender: "agent:developer", recipient: "user", kind: "input_request",
    body: "Hapus baris saat jumlah nol?", reply_to: null, metadata: {}, attachment_ids: [], created_at: "2026-10-05T01:00:00Z",
    input: { id: "m-q", thread_id: "ask:1", project_id: "p1", ticket_id: "t1", scope_version: 2, recipient: "user", job_id: "r2",
      generation: 4, status: "open", answer_id: null, answer: null, attempt_status: null } };
  state.runInput.r2 = question;
  state.runs = [
    run({ id: "r1", status: "waiting_quota", available_at: "2026-10-05T12:30:00Z", runtime: "hermes" }),
    run({ id: "r2", role: "developer", status: "waiting_input", revision: 9, generation: 4, request_id: "m-q" }),
    run({ id: "r3", role: "po", runtime: "structured:fake", fake: true, status: "running", stage: "chat", ticket_id: null }),
  ];
  await mockApi(page, state);
  await page.goto("/#/p/p1");
  await page.getByRole("tab", { name: /Aktivitas/ }).click();

  await expect(page.locator('.run[data-run="r1"]').getByText("Menunggu kuota provider")).toBeVisible();
  await expect(page.locator('.run[data-run="r1"]').getByText("Run tidak gagal")).toBeVisible();
  await expect(page.locator('.run[data-run="r1"]').getByText("FAKE")).toHaveCount(0);
  await expect(page.locator('.run[data-run="r3"]').getByText("FAKE · bukan QA nyata")).toBeVisible();
  await expect(page.locator(".topbar").getByText("FAKE · bukan QA nyata")).toBeVisible();
  await expect(page.locator("#blk-h + ul, #blk-h ~ ul").getByText("Perlu keputusan Anda")).toBeVisible();
  // The board marks the same states.
  await expect(page.locator("article.card", { hasText: "Menu kopi" }).getByText("Menunggu kuota")).toBeVisible();
  await expect(page.locator("article.card", { hasText: "Menu kopi" }).getByText("Menunggu jawaban")).toBeVisible();

  // The user answers the blocking question with the exact scope/generation/revision the run reported.
  const waiting = page.locator('.run[data-run="r2"]');
  await expect(waiting.getByText("Hapus baris saat jumlah nol?")).toBeVisible();
  await waiting.getByLabel("Jawaban untuk run").fill("Ya, hapus.");
  await waiting.getByRole("button", { name: "Jawab dan lanjutkan" }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].path).toBe("/runs/r2/input");
  expect(state.posts[0].body).toEqual({ expected_revision: 9, request_id: "m-q", scope_version: 2, generation: 4, answer: "Ya, hapus." });
  expect(state.posts[0].key).toBeTruthy();

  // Stop: empty JSON object, never expected_revision.
  const active = page.locator('.run[data-run="r1"]');
  await active.getByRole("button", { name: "Stop", exact: true }).click();
  await active.getByRole("button", { name: "Ya, hentikan run" }).click();
  await expect.poll(() => state.posts.length).toBe(2);
  expect(state.posts[1].path).toBe("/runs/r1/stop");
  expect(state.posts[1].body).toEqual({});
});

test("ticket evidence shows target identity, missing artifacts, manual UAC and sends the pinned UAT decision", async ({ page }) => {
  const state = fresh();
  const t = ticket({ phase: "uat", revision: 12, scope_version: 2 });
  const candidate: Candidate = {
    id: "c1", ticket_id: "t1", scope_version: 2, commit_sha: "a".repeat(40), base_sha: "b".repeat(40), status: "verified",
    target_artifact_id: "art-target", target_digest: "sha256:" + "d".repeat(64), evidence_ids: ["ev-ok", "ev-gone"], preview: null,
    commit_artifact_id: "art-commit", build_artifact_id: "art-build",
    verifications: [{ id: "v1", status: "passed", target_digest: "sha256:" + "d".repeat(64), evidence_ids: ["ev-ok", "ev-gone"],
      counts: { executed: 4, passed: 4, failed: 0 }, results: {}, uac_coverage: { "UAC-1": ["test_menu"], "UAC-2": [] } }] };
  state.tickets = [t];
  state.runs = [run({ id: "rq", role: "qa", runtime: "structured:fake", fake: true, status: "succeeded", stage: "qa" })];
  state.artifacts = {
    "art-target": artifact("art-target", { kind: "target" }), "art-commit": artifact("art-commit", { kind: "commit" }),
    "art-build": artifact("art-build", { kind: "build" }), "ev-ok": artifact("ev-ok"),
    "ev-gone": artifact("ev-gone", { availability: "unavailable", unavailable_reason: "berkas hilang dari disk" }) };
  state.detail = {
    ticket: t, cursor: 5, candidates: [candidate], messages: [], dependencies: [{ upstream_id: "t0", state: "accepted", scope_version: 1, candidate_id: null, integration_sha: "e".repeat(40), revalidation: null }],
    versions: [{ version: 2, title: "Menu kopi", description: "Menu", uac: [{ id: "UAC-1", text: "Menu tampil" }, { id: "UAC-2", text: "Tampilan nyaman menurut pemilik", mode: "manual" }], scope: null }],
    approvals: [{ id: "ap1", type: "baseline_waiver", scope_version: 2, candidate_id: null, target_digest: null, evidence_ids: [],
      details: { status: "waived", reason: "Kegagalan baseline sudah diketahui" } }] };
  await mockApi(page, state);
  await page.goto("/#/p/p1/t/t1");

  const panel = page.locator(".ticket");
  await expect(panel.getByText("Tiket ini memiliki run fake")).toBeVisible();
  await expect(panel.getByText(/sha256:d{6,}/).first()).toBeVisible();
  await expect(panel.locator('[data-artifact="ev-gone"]')).toContainText("tidak tersedia: berkas hilang dari disk");
  await expect(panel.locator('[data-artifact="ev-ok"]')).not.toContainText("tidak tersedia");
  await expect(panel.locator('[data-artifact="ev-ok"] a')).toHaveAttribute("href", `${API}/artifacts/ev-ok/content`);
  await expect(panel.getByText("tanpa tes")).toBeVisible();
  await expect(panel.locator('[data-approval="baseline_waiver"]')).toContainText("Kegagalan baseline sudah diketahui");
  await expect(panel.locator(".uac").getByText("manual")).toBeVisible();

  // The manual UAC must be confirmed by the user before the accept action is available.
  await expect(panel.getByRole("button", { name: "Terima (UAT)" })).toBeDisabled();
  await panel.getByLabel(/UAC-2: Tampilan nyaman/).check();
  await panel.getByRole("button", { name: "Terima (UAT)" }).click();
  await panel.getByRole("button", { name: "Ya, terima target ini" }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].path).toBe("/tickets/t1/uat-decisions");
  expect(state.posts[0].body).toEqual({
    expected_revision: 12, candidate_id: "c1", scope_version: 2, target_artifact_id: "art-target",
    target_digest: "sha256:" + "d".repeat(64), verification_id: "v1", evidence_ids: ["ev-ok", "ev-gone"], manual_uac_ids: ["UAC-2"] });
});

test("event stream loss is visible and a needs_human blocker offers a bounded repair authorization", async ({ page }) => {
  const state = fresh();
  const t = ticket({ phase: "development", blocker: { reason: "needs_human", resolution: "authorize" }, repair_cycles: 3 });
  state.tickets = [t];
  state.detail = { ticket: t, cursor: 5, candidates: [], messages: [], dependencies: [], approvals: [],
    versions: [{ version: 2, title: "Menu kopi", description: "", uac: [{ id: "UAC-1", text: "Menu tampil" }], scope: null }] };
  await mockApi(page, state);
  await page.goto("/#/p/p1/t/t1");
  await expect(page.locator(".topbar").getByText("Menyambung ulang…")).toBeVisible();
  await expect(page.locator(".ticket-head .blocker")).toContainText("batas perbaikan tercapai");
  await page.getByLabel("Izinkan siklus perbaikan tambahan").fill("2");
  await page.getByRole("button", { name: "Izinkan 2 siklus" }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0]).toMatchObject({ path: "/tickets/t1/repair-authorizations", body: { expected_revision: 7, additional_cycles: 2 } });
});

for (const size of [{ width: 1280, height: 720 }, { width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
  test(`board, chat and ticket fit a ${size.width}x${size.height} screen without page scrolling`, async ({ page }) => {
    await page.setViewportSize(size);
    const state = fresh();
    const phases = ["scope_review", "ready", "development", "technical_review", "qa", "uat", "integrating", "accepted"] as const;
    state.tickets = phases.flatMap((phase, i) => [1, 2, 3].map((n) => ticket({
      id: `t${i}${n}`, number: i * 3 + n, title: `Tiket ${phase} ${n} dengan judul yang cukup panjang untuk membungkus`, phase })));
    state.detail = { ticket: state.tickets[0], cursor: 5, candidates: [], messages: [], dependencies: [], approvals: [],
      versions: [{ version: 2, title: "Menu", description: "", uac: [{ id: "UAC-1", text: "Satu" }], scope: null }] };
    await mockApi(page, state);
    await page.goto("/#/p/p1");
    await expect(page.locator("article.card").first()).toBeVisible();
    const fits = () => page.evaluate(() => ({ overflowX: document.documentElement.scrollWidth - window.innerWidth, overflowY: document.documentElement.scrollHeight - window.innerHeight }));
    expect(await fits()).toEqual({ overflowX: 0, overflowY: 0 });
    // Board and chat are usable at the same time: the composer is on screen and the board scrolls on its own.
    const send = await page.getByRole("button", { name: "Kirim", exact: true }).boundingBox();
    expect(send && send.y + send.height <= size.height).toBe(true);
    const board = await page.locator(".columns").boundingBox();
    expect(board && board.width > 500 && board.height > 250).toBe(true);
    await page.locator("article.card").first().getByRole("button", { name: /Tiket scope_review 1/ }).click();
    await expect(page.getByRole("heading", { name: /Tiket scope_review 1/, level: 2 })).toBeVisible();
    expect(await fits()).toEqual({ overflowX: 0, overflowY: 0 });
  });
}
