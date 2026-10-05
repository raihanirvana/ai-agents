import { type Page } from "@playwright/test";
import type { Artifact, Board, Candidate, Message, Project, Run, Ticket, TicketDetail } from "../../contracts/api/types";

/**
 * Display states that the fake-provider fixture cannot reach on demand (quota wait, user questions, evidence that
 * went missing, UAT identity). The API is replaced by contract-shaped fixtures: these tests prove how the GUI
 * renders and what it sends, not that a backend produces those states (DEV-008 and the domain suites do).
 */
export const API = "http://127.0.0.1:19850";
export const CORS = {
  "access-control-allow-origin": "http://127.0.0.1:19851", "access-control-allow-credentials": "true",
  "access-control-allow-headers": "content-type,x-csrf-token,idempotency-key", "access-control-allow-methods": "GET,POST,OPTIONS",
};

export const project: Project = { id: "p1", name: "Mock proyek", mode: "new", brief: "Brief", brief_version: 1, revision: 4, onboarding: "pending", accepted_tip: null };
export const ticket = (over: Partial<Ticket>): Ticket => ({
  id: "t1", project_id: "p1", number: 1, title: "Menu kopi", phase: "development", revision: 7, scope_version: 2,
  priority: 0, blocker: null, repair_cycles: 0, repair_limit: 3, ...over });
export const run = (over: Partial<Run>): Run => ({
  id: "r1", project_id: "p1", ticket_id: "t1", scope_version: 2, revision: 3, generation: 1, role: "developer",
  runtime: "hermes", fake: false, lane: "execution", stage: "implement", status: "running", attempt: 1, usage: { model_calls: 2 },
  attempt_usage: {}, limits: { model_calls: 6 }, result: null, request_id: null, context_artifact_id: null, available_at: null, ...over });

export interface State {
  tickets: Ticket[]; runs: Run[]; messages: Message[]; detail: TicketDetail | null;
  artifacts: { [id: string]: Artifact }; runInput: { [id: string]: Message | null };
  posts: { path: string; body: unknown; key: string | undefined }[]; events: "open" | "abort";
}
export const fresh = (): State => ({ tickets: [], runs: [], messages: [], detail: null, artifacts: {}, runInput: {}, posts: [], events: "abort" });

export async function mockApi(page: Page, state: State) {
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

export const artifact = (id: string, over: Partial<Artifact> = {}): Artifact => ({
  id, project_id: "p1", kind: "evidence", storage: "file", checksum: "c".repeat(64), size_bytes: 10, availability: "available",
  unavailable_reason: null, metadata: {}, ...over });
