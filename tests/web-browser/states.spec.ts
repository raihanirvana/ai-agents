import { expect, test, type Page } from "@playwright/test";
import type { Artifact, Board, Candidate, Message, Preview, Project, Release, Run, StateEvent, Ticket, TicketDetail } from "../../contracts/api/types";

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
  posts: { path: string; body: unknown; key: string | undefined }[]; events: "open" | "abort" | "office-update"; releases?: Release[];
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
    if (path === "/projects/p1/tickets") return json({ project, tickets: state.tickets, runs: state.runs, preview: null, releases: state.releases ?? [], cursor: 5 } satisfies Board);
    if (path === "/projects/p1/messages") return json({ messages: state.messages, cursor: 5 });
    if (path === "/projects/p1/events") {
      if (state.events === "abort") return route.abort("failed");
      if (state.events === "office-update") {
        state.events = "open";
        state.runs = state.runs.map((item) => item.role === "qa" ? { ...item, status: "succeeded", revision: item.revision + 1 } : item);
        const event: StateEvent = { cursor: 6, project_id: "p1", type: "job.completed", entity_type: "jobs", entity_id: "r-qa",
          run_id: "r-qa", payload: { status: "succeeded" }, created_at: "2026-10-05T01:01:00Z" };
        return route.fulfill({ status: 200, contentType: "text/event-stream", body: `id: 6\nevent: state\ndata: ${JSON.stringify(event)}\n\n`, headers: CORS });
      }
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": open\n\n", headers: CORS });
    }
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
    commit_artifact_id: "art-commit", build_artifact_id: "art-build", live_preview: null, integrated_sha: null, integration: null,
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
    await page.locator("article.card").first().locator(".chips").click();
    await expect(page.getByRole("heading", { name: /Tiket scope_review 1/, level: 2 })).toBeVisible();
    expect(await fits()).toEqual({ overflowX: 0, overflowY: 0 });
  });
}

test("a failed preview shows why and leaves nothing running; a non-localhost URL is never offered as a link", async ({ page }) => {
  const state = fresh();
  const t = ticket({ phase: "uat", revision: 12, scope_version: 2 });
  const preview = (over: Partial<Preview>): Preview => ({
    id: "pv1", project_id: "p1", ticket_id: "t1", candidate_id: "c1", scope_version: 2, target_artifact_id: "art-target",
    target_digest: "sha256:" + "d".repeat(64), status: "failed", revision: 3, url: null, port: 5180, stop_reason: null,
    error: "PreviewFailed: smoke health check failed: HTTP 404", requested_at: "2026-10-05T01:00:00Z", ready_at: null,
    stopped_at: "2026-10-05T01:00:05Z", details: { build_digest: "b".repeat(64), config_digest: "c".repeat(64), fixture: { id: "coffee-menu-v1" }, evidence_ids: ["ev-ok"] }, ...over });
  const candidate: Candidate = {
    id: "c1", ticket_id: "t1", scope_version: 2, commit_sha: "a".repeat(40), base_sha: "b".repeat(40), status: "verified",
    target_artifact_id: "art-target", target_digest: "sha256:" + "d".repeat(64), evidence_ids: ["ev-ok"], preview: null,
    commit_artifact_id: "art-commit", build_artifact_id: "art-build", live_preview: preview({}), integrated_sha: null, integration: null, verifications: [] };
  state.tickets = [t];
  state.artifacts = { "art-target": artifact("art-target"), "art-commit": artifact("art-commit"), "art-build": artifact("art-build"), "ev-ok": artifact("ev-ok") };
  state.detail = { ticket: t, cursor: 5, candidates: [candidate], messages: [], dependencies: [], approvals: [],
    versions: [{ version: 2, title: "Menu kopi", description: "", uac: [{ id: "UAC-1", text: "Menu tampil" }], scope: null }] };
  await mockApi(page, state);
  await page.goto("/#/p/p1/t/t1");
  const panel = page.locator('section[aria-label="Preview untuk UAT"]');
  await expect(panel).toHaveAttribute("data-preview-status", "failed");
  await expect(panel.getByRole("alert")).toContainText("smoke health check failed: HTTP 404");
  await expect(panel.getByRole("button", { name: "Buka ulang preview" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Hentikan preview" })).toHaveCount(0);
  await panel.getByRole("button", { name: "Buka ulang preview" }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0]).toMatchObject({ path: "/tickets/t1/candidates/c1/previews", body: {} });
  expect(state.posts[0].key).toBeTruthy();

  candidate.live_preview = preview({ status: "ready", url: "http://127.0.0.1:5180/", error: null, stopped_at: null, ready_at: "2026-10-05T01:00:06Z" });
  await page.reload();
  await expect(panel).toHaveAttribute("data-preview-status", "ready");
  await expect(panel.getByRole("link")).toHaveCount(0);  // control host: refused as a preview origin
  candidate.live_preview = preview({ status: "ready", url: "http://localhost:5180/", error: null, stopped_at: null, ready_at: "2026-10-05T01:00:06Z" });
  await page.reload();
  await expect(panel.getByRole("link", { name: "Buka preview di tab baru" })).toHaveAttribute("href", "http://localhost:5180/");
});

test("an integrating ticket shows the pending/blocked operation, the refs involved and the integration evidence", async ({ page }) => {
  const state = fresh();
  const t = ticket({ phase: "integrating", revision: 14, scope_version: 2,
    blocker: { reason: "integration_blocked", resolution: "operator must inspect", detail: "accepted ref diverged" } });
  const c: Candidate = {
    id: "c1", ticket_id: "t1", scope_version: 2, commit_sha: "a".repeat(40), base_sha: "b".repeat(40), status: "verified",
    target_artifact_id: "art-target", target_digest: "sha256:" + "d".repeat(64), evidence_ids: [], preview: null,
    commit_artifact_id: "art-commit", build_artifact_id: "art-build", live_preview: null, integrated_sha: null,
    integration: { operation_id: "op1", status: "blocked", expected_base: "b".repeat(40), target_sha: "a".repeat(40),
      observed_tip: "f".repeat(40), reason: "accepted ref diverged from both the expected base and the target", evidence_artifact_id: "ev-int" },
    verifications: [] };
  state.tickets = [t];
  state.artifacts = { "art-target": artifact("art-target"), "art-commit": artifact("art-commit"), "art-build": artifact("art-build"),
    "ev-int": artifact("ev-int", { kind: "report" }) };
  state.detail = { ticket: t, cursor: 5, candidates: [c], messages: [], dependencies: [], approvals: [],
    versions: [{ version: 2, title: "Menu kopi", description: "", uac: [{ id: "UAC-1", text: "Menu tampil" }], scope: null }] };
  await mockApi(page, state);
  await page.goto("/#/p/p1/t/t1");
  const row = page.locator(".integration");
  await expect(row).toHaveAttribute("data-integration-status", "blocked");
  await expect(row).toContainText("Diblokir");
  await expect(row).toContainText("bbbbbbbbbb");
  await expect(row).toContainText("ffffffffff");
  await expect(row.locator('[data-artifact="ev-int"]')).toContainText("bukti integrasi");
  await expect(page.locator(".ticket-head .blocker")).toContainText("Integrasi diblokir");
  await expect(page.locator('section[aria-label="Preview untuk UAT"]')).toHaveCount(0);  // not in UAT any more
});

const release = (over: Partial<Release> = {}): Release => ({
  id: "rel-aaaaaaaa11", project_id: "p1", status: "draft", accepted_tip: "a".repeat(40),
  scope: [
    { ticket_id: "t1", number: 1, title: "Menu kopi", scope_version: 2, candidate_id: "c1", integrated_sha: "1".repeat(40),
      uac: [{ id: "UAC-1", text: "Menu tampil" }, { id: "UAC-M", text: "Tampilan nyaman", mode: "manual" }], checklist: ["t1:UAC-M"] },
    { ticket_id: "t2", number: 2, title: "Keranjang", scope_version: 1, candidate_id: "c2", integrated_sha: "2".repeat(40),
      uac: [{ id: "UAC-1", text: "Keranjang tampil" }], checklist: [], affected_by_sync: true, overlap_files: ["index.html"] },
  ],
  checklist: ["t1:UAC-M"], target_artifact_id: "rt", target_digest: "sha256:" + "d".repeat(64), build_artifact_id: "rb",
  evidence_ids: ["rep", "suite", "log"], export: null, deployment: null, deployed: false, revision: 3, created_at: "2026-10-05T01:00:00Z", ...over });

test("a draft release needs its manual checklist, approval pins the exact target/evidence, and nothing says deployed", async ({ page }) => {
  const state = fresh();
  state.tickets = [ticket({ phase: "accepted" })];
  state.releases = [release()];
  state.artifacts = { rep: artifact("rep"), suite: artifact("suite"), log: artifact("log") };
  await mockApi(page, state);
  await page.goto("/#/p/p1");
  await page.getByRole("tab", { name: "Release" }).click();
  const card = page.locator('article[data-release="rel-aaaaaaaa11"]');
  await expect(card).toHaveAttribute("data-status", "draft");
  await expect(card.getByText("Disetujui")).toHaveCount(0);
  await expect(card.getByText("Di-deploy")).toHaveCount(0);
  await expect(card.getByText("terdampak sinkronisasi")).toBeVisible();
  await expect(card.getByRole("link", { name: "Laporan verifikasi gabungan" })).toHaveAttribute("href", /\/artifacts\/rep\/content$/);
  await expect(page.getByRole("button", { name: /Bekukan dan verifikasi release/ })).toBeDisabled();  // a draft is open
  const approve = card.getByRole("button", { name: "Setujui release ini" });
  await expect(approve).toBeDisabled();
  await card.getByLabel(/#1 UAC-M: Tampilan nyaman/).check();
  await approve.click();
  await card.getByRole("button", { name: "Ya, setujui target ini" }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].path).toBe("/releases/rel-aaaaaaaa11/decisions");
  expect(state.posts[0].body).toEqual({ expected_revision: 3, target_artifact_id: "rt", target_digest: "sha256:" + "d".repeat(64),
    evidence_ids: ["rep", "suite", "log"], manual_uac_ids: ["t1:UAC-M"] });
  expect(state.posts[0].key).toBeTruthy();
});

test("an approved release offers an explicit local export; the export result states nothing was pushed or deployed", async ({ page }) => {
  const state = fresh();
  state.tickets = [ticket({ phase: "accepted" })];
  state.releases = [release({ id: "rel-bbbbbbbb22", status: "approved" })];
  await mockApi(page, state);
  await page.goto("/#/p/p1");
  await page.getByRole("tab", { name: "Release" }).click();
  const card = page.locator('article[data-release="rel-bbbbbbbb22"]');
  await expect(card.getByText("belum diekspor, belum di-deploy")).toBeVisible();
  await card.getByRole("button", { name: "Ekspor lokal (patch + bundle)" }).click();
  await card.getByRole("button", { name: "Ya, ekspor" }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0]).toMatchObject({ path: "/releases/rel-bbbbbbbb22/export", body: { expected_revision: 3 } });

  state.releases = [release({ id: "rel-bbbbbbbb22", status: "exported", revision: 4, export: {
    formats: ["patch", "bundle"], tip: "a".repeat(40), base_sha: "b".repeat(40), branch: "release/rel-bbbb", ref_in_bundle: "refs/releases/x",
    patch_artifact_id: "pp", bundle_artifact_id: "bb", pushed: false, deployed: false, how_to_use: "git apply --index release.patch", exported_at: "2026-10-05T02:00:00Z" } })];
  await page.reload();
  await page.getByRole("tab", { name: "Release" }).click();
  const done = page.locator('article[data-release="rel-bbbbbbbb22"]');
  await expect(done.getByText("Diekspor lokal (belum di-push, belum di-deploy)")).toBeVisible();
  await expect(done.locator('[aria-label="Hasil ekspor"]')).toContainText("Pushed: tidak · Deployed: tidak");
  await expect(done.getByRole("link", { name: "Unduh patch" })).toHaveAttribute("href", /\/artifacts\/pp\/content$/);
  await expect(done.getByRole("button", { name: /Ekspor lokal/ })).toHaveCount(0);
});

test("a synchronised release pins technical diff review separately from its manual UAC", async ({ page }) => {
  const state = fresh();
  state.releases = [release({ technical_review_evidence_ids: ["drift-diff", "combined-diff"] })];
  await mockApi(page, state);
  await page.goto("/#/p/p1");
  await page.getByRole("tab", { name: "Release" }).click();
  const card = page.locator('article[data-release="rel-aaaaaaaa11"]');
  const approve = card.getByRole("button", { name: "Setujui release ini" });
  await card.getByLabel(/#1 UAC-M/).check();
  await expect(approve).toBeDisabled();
  await expect(card.getByRole("link", { name: "Diff perubahan sumber" })).toHaveAttribute("href", /drift-diff\/content$/);
  await expect(card.getByRole("link", { name: "Diff gabungan release" })).toHaveAttribute("href", /combined-diff\/content$/);
  await card.getByLabel(/Saya telah meninjau kedua diff/).check();
  await approve.click();
  await card.getByRole("button", { name: "Ya, setujui target ini" }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].body).toMatchObject({ reviewed_diff_ids: ["drift-diff", "combined-diff"], manual_uac_ids: ["t1:UAC-M"] });
});

test("a failed release shows no approval and an in-flight release job holds the freeze button", async ({ page }) => {
  const state = fresh();
  state.tickets = [ticket({ phase: "accepted" })];
  state.releases = [release({ status: "failed" })];
  state.runs = [run({ id: "rj", stage: "release", status: "running", role: "technical-lead", ticket_id: null })];
  await mockApi(page, state);
  await page.goto("/#/p/p1");
  await page.getByRole("tab", { name: "Release" }).click();
  await expect(page.locator("[data-release-run]")).toContainText("Integrasi tiket baru ditahan");
  await expect(page.getByRole("button", { name: "Setujui release ini" })).toHaveCount(0);
  await expect(page.getByText("release gagal tidak dapat disetujui")).toBeVisible();
  await expect(page.getByRole("button", { name: /Bekukan dan verifikasi release/ })).toBeDisabled();
});

test("office projects persisted roles, run state and message thread, while the board stays available", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (contextId: string, ...args: unknown[]) {
      if (["webgl", "webgl2", "experimental-webgl"].includes(contextId)) return null;
      return Reflect.apply(getContext, this, [contextId, ...args]) as RenderingContext | null;
    } as typeof HTMLCanvasElement.prototype.getContext;
  });
  const state = fresh();
  state.tickets = [ticket({ id: "t1", number: 7, title: "Menu kopi" })];
  state.detail = { ticket: state.tickets[0], versions: [], dependencies: [], approvals: [], candidates: [], messages: [], cursor: 5 };
  state.runs = [
    run({ id: "r-po", role: "po", ticket_id: null, stage: "chat", status: "running", fake: true, runtime: "structured:fake" }),
    run({ id: "r-dev", role: "developer", status: "running", stage: "implement", ticket_id: "t1" }),
    run({ id: "r-qa", role: "qa", status: "waiting_input", stage: "suite", ticket_id: "t1" }),
  ];
  state.messages = [{ id: "m-po", project_id: "p1", ticket_id: null, thread_id: "chat:p1", seq: 4,
    sender: "agent:po", recipient: null, kind: "message", body: "Saya mengusulkan tiga tiket untuk brief ini.",
    reply_to: null, metadata: {}, attachment_ids: [], created_at: "2026-10-05T01:00:00Z" }];
  await mockApi(page, state);
  await page.goto("/#/p/p1");
  await page.getByRole("tab", { name: "Kantor" }).click();

  await expect(page.getByRole("heading", { name: "Kantor tim" })).toBeVisible();
  await expect(page.locator(".office-fallback")).toContainText("Scene 3D tidak tersedia");
  await expect(page.locator(".office-fake")).toContainText("FAKE di proyek ini");
  await expect(page.locator(".office-roles").getByRole("button", { name: /Product Owner/ })).toContainText("Menyusun rencana");
  await expect(page.locator(".office-roles").getByRole("button", { name: /Developer/ })).toContainText("Mengembangkan");
  await expect(page.locator(".office-roles").getByRole("button", { name: /QA/ })).toContainText("Menunggu input");
  await expect(page.locator(".office-roles").getByRole("button")).toHaveCount(4);
  await expect(page.getByLabel("Scene kantor tiga dimensi")).toBeVisible();
  const motion = page.getByRole("button", { name: "Animasi nonaktif" });
  await expect(motion).toHaveAttribute("aria-pressed", "false");
  await motion.click();
  await expect(page.getByRole("button", { name: "Animasi aktif" })).toHaveAttribute("aria-pressed", "true");

  await page.locator(".office-roles").getByRole("button", { name: /Product Owner/ }).click();
  await expect(page.getByLabel("Konteks Product Owner")).toContainText("Saya mengusulkan tiga tiket untuk brief ini.");
  await expect(page.locator(".column").first()).toBeVisible();
  await page.locator(".office-roles").getByRole("button", { name: /Developer/ }).click();
  await expect(page.getByRole("heading", { name: "Menu kopi", level: 2 })).toBeVisible();
});

test("a WebGL office avatar opens the same role conversation shown by the accessible list", async ({ page }) => {
  const state = fresh();
  state.events = "office-update";
  state.runs = [run({ id: "r-qa", role: "qa", stage: "verify", status: "waiting_input" })];
  state.messages = [{ id: "m-po", project_id: "p1", ticket_id: null, thread_id: "chat:p1", seq: 4,
    sender: "agent:po", recipient: null, kind: "message", body: "Percakapan dari PO yang tersimpan.",
    reply_to: null, metadata: {}, attachment_ids: [], created_at: "2026-10-05T01:00:00Z" }];
  await mockApi(page, state);
  await page.goto("/#/p/p1");
  await page.getByRole("tab", { name: "Kantor" }).click();
  await expect(page.getByRole("heading", { name: "Kantor tim" })).toBeVisible();
  await expect(page.locator(".office-roles").getByRole("button", { name: /QA/ })).toContainText("Siaga");
  const canvas = page.locator(".office-scene canvas");
  if (await page.locator(".office-fallback").isVisible()) test.skip(true, "WebGL unavailable in this browser");
  await expect(canvas).toBeVisible();
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  await canvas.click({ position: { x: box!.width * 0.28, y: box!.height * 0.48 } });
  await expect(page.getByLabel("Konteks Product Owner")).toContainText("Percakapan dari PO yang tersimpan.");
});
