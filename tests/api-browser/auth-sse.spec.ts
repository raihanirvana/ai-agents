import { test, expect } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const API = "http://127.0.0.1:19841";
const CODE = "test-only-browser-code-0123456789";

test("real browser: host-only cookie, preview header capture, Origin/CSRF and SSE reconnect", async ({ page, context }, info) => {
  await page.goto("/");
  const login = await page.evaluate(async ({ api, code }) => {
    const r = await fetch(`${api}/auth/login`, { method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
    return r.json();
  }, { api: API, code: CODE });
  const csrf = login.csrf_token as string;
  const cookies = await context.cookies(API);
  expect(cookies).toHaveLength(1);
  expect(cookies[0].domain).toBe("127.0.0.1");
  expect(cookies[0].httpOnly).toBe(true);
  expect(cookies[0].sameSite).toBe("Strict");
  expect(await page.evaluate(() => document.cookie)).toBe("");
  const result = await page.evaluate(async ({ api, csrf }) => {
    const r = await fetch(`${api}/projects`, { method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, "Idempotency-Key": "browser-project" },
      body: JSON.stringify({ name: "Browser fixture" }) });
    return r.json();
  }, { api: API, csrf });
  const pid = result.project.id as string;
  // Capture the actual incoming headers at localhost, across a top-level navigation.
  await page.goto("http://localhost:19843/capture");
  const capture = JSON.parse(await page.locator("body").innerText());
  expect(capture).toEqual({ host: "localhost:19843", cookie_present: false,
    authorization_present: false, csrf_present: false });
  expect(await context.cookies("http://localhost:19843")).toEqual([]);
  const attacked = await page.evaluate(async ({ api, csrf }) => {
    try {
      await fetch(`${api}/projects`, { method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, "Idempotency-Key": "preview-attack" },
        body: JSON.stringify({ name: "Attack" }) });
      return "allowed";
    } catch { return "blocked"; }
  }, { api: API, csrf });
  expect(attacked).toBe("blocked");
  const previewRequests = await page.evaluate(async () => (await fetch("/api-capture")).json());
  expect(previewRequests.some((r: any) => r.method === "OPTIONS" && r.origin === "http://localhost:19843" && r.status === 403)).toBe(true);
  await page.goto("/");
  const snapshot = await page.evaluate(async ({ api, pid }) =>
    (await fetch(`${api}/projects/${pid}/tickets`, { credentials: "include" })).json(), { api: API, pid });
  await page.evaluate(({ api, pid, cursor }) => {
    const w = window as any;
    w.received = [];
    w.source = new EventSource(`${api}/projects/${pid}/events?cursor=${cursor}`, { withCredentials: true });
    w.source.addEventListener("state", (event: MessageEvent) => w.received.push(JSON.parse(event.data)));
  }, { api: API, pid, cursor: snapshot.cursor });
  await page.evaluate(async ({ api, pid, csrf }) => fetch(`${api}/projects/${pid}/messages`, {
    method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf,
      "Idempotency-Key": "browser-message" }, body: JSON.stringify({ expected_revision: 1, body: "Persist me", task: "note" }) }),
    { api: API, pid, csrf });
  await expect.poll(() => page.evaluate(() => (window as any).received.some((e: any) => e.type === "message.created"))).toBe(true);
  const cursor = await page.evaluate(() => {
    const w = window as any;
    w.source.close();
    return w.received.at(-1).cursor;
  });
  await page.reload();
  const persisted = await page.evaluate(async ({ api, pid, cursor }) => {
    const messages = await (await fetch(`${api}/projects/${pid}/messages`, { credentials: "include" })).json();
    const replay = await (await fetch(`${api}/projects/${pid}/events?follow=false`, { credentials: "include",
      headers: { "Last-Event-ID": String(cursor) } })).text();
    return { messages: messages.messages, replay };
  }, { api: API, pid, cursor });
  expect(persisted.messages.filter((m: any) => m.body === "Persist me")).toHaveLength(1);
  expect(persisted.replay).toBe("");
  // Force normal EOF to exercise native EventSource reconnect and its Last-Event-ID header.
  await page.evaluate(({ api, pid, cursor }) => {
    const w = window as any;
    w.reconnected = [];
    w.source = new EventSource(`${api}/projects/${pid}/events?follow=false&cursor=${cursor}`, { withCredentials: true });
    w.source.addEventListener("state", (e: MessageEvent) => w.reconnected.push(JSON.parse(e.data)));
  }, { api: API, pid, cursor });
  await page.evaluate(async ({ api, pid, csrf }) => fetch(`${api}/projects/${pid}/messages`, {
    method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf,
      "Idempotency-Key": "browser-message-2" }, body: JSON.stringify({ expected_revision: 1, body: "Reconnect", task: "note" }) }), { api: API, pid, csrf });
  await expect.poll(() => page.evaluate(() => (window as any).reconnected.length), { timeout: 10000 }).toBe(1);
  const reconnectCursor = await page.evaluate(() => (window as any).reconnected[0].cursor);
  await expect.poll(async () => {
    const records = await (await context.request.get("http://localhost:19843/api-capture")).json();
    return records.some((r: any) => r.path.endsWith("/events") && r.last_event_id === String(reconnectCursor) && r.status === 200);
  }, { timeout: 10000 }).toBe(true);
  expect(await page.evaluate(() => (window as any).reconnected.length)).toBe(1);
  await page.evaluate(() => (window as any).source.close());
  // A live stream closes promptly after session revocation.
  await page.evaluate(({ api, pid, cursor }) => {
    const w = window as any;
    w.expired = false;
    w.source = new EventSource(`${api}/projects/${pid}/events?cursor=${cursor}`, { withCredentials: true });
    w.source.addEventListener("session_expired", () => { w.expired = true; w.source.close(); });
  }, { api: API, pid, cursor: reconnectCursor });
  await expect.poll(() => page.evaluate(() => (window as any).source.readyState)).toBe(1);
  await page.evaluate(async ({ api, csrf }) => fetch(`${api}/auth/logout`, {
    method: "POST", credentials: "include", headers: { "X-CSRF-Token": csrf } }), { api: API, csrf });
  await expect.poll(() => page.evaluate(() => (window as any).expired)).toBe(true);
  const evidence = {
    test: "DEV-008 real Chromium HTTP auth/SSE", status: "passed", executed_at: new Date().toISOString(),
    browser_version: context.browser()?.version(),
    preview_header_capture: capture, cookie: { domain: cookies[0].domain, httpOnly: true, sameSite: "Strict", path: "/" },
    preview_attack_preflight_status: 403, sse_live: true, replay_after_cursor_empty: true,
    persistent_message_count: 1, native_last_event_id_reconnect: true, stream_revoked: true,
  };
  const text = JSON.stringify(evidence, null, 2) + "\n";
  const output = resolve("data/dev008/browser-results.json");
  mkdirSync(resolve("data/dev008"), { recursive: true });
  writeFileSync(output, text);
  await info.attach("dev008-browser-evidence", { contentType: "application/json", body: text });
});
