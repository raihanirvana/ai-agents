import { test, expect } from "@playwright/test";

test("actual TS client refreshes expired cursor, restores chat and surfaces stale commands", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(async () => {
    const w = window as any;
    const module = await import(/* @vite-ignore */ "/client.js");
    w.client = new module.ApiClient("http://127.0.0.1:19841");
    await w.client.login("test-only-browser-code-0123456789");
    const result = await w.client.command("/projects", { name: "Client fixture" }, "client-project");
    w.pid = result.project.id;
    for (let i = 0; i < 6; i++) {
      await w.client.command(`/projects/${w.pid}/messages`, { expected_revision: 1, body: `Note ${i}`, task: "note" }, `client-note-${i}`);
    }
    w.events = [];
    w.snapshots = [];
    w.messages = [];
    w.errors = [];
    w.unsubscribe = w.client.watch(w.pid, 0, {
      event: (e: any) => w.events.push(e),
      snapshot: async (board: any, reason: string) => {
        w.messages = (await w.client.messages(w.pid)).messages;
        w.snapshots.push({ cursor: board.cursor, reason });
      },
      error: (e: any) => w.errors.push(String(e)),
    });
  });
  await expect.poll(() => page.evaluate(() => (window as any).snapshots.length)).toBe(1);
  expect(await page.evaluate(() => (window as any).snapshots[0].reason)).toBe("cursor_expired");
  expect(await page.evaluate(() => (window as any).messages.length)).toBe(6);
  await page.evaluate(async () => {
    const w = window as any;
    const body = { expected_revision: 1, body: "After snapshot", task: "note" };
    const path = `/projects/${w.pid}/messages`;
    await w.client.command(path, body, "after-snapshot");
    await w.client.command(path, body, "after-snapshot");
    const tickets = await w.client.command(`/projects/${w.pid}/tickets`, {
      title: "Coffee", uac: [{ id: "UAC-1", text: "Add coffee" }],
    }, "client-ticket");
    const t = tickets.ticket;
    await w.client.command(`/tickets/${t.id}/priority`, { expected_revision: t.revision, priority: 1 }, "client-priority");
    try {
      await w.client.command(`/tickets/${t.id}/priority`, { expected_revision: t.revision, priority: 2 }, "client-stale");
    } catch (e) { w.conflict = { status: (e as any).status, code: (e as any).detail.code }; }
  });
  await expect.poll(() => page.evaluate(() => (window as any).events.filter((e: any) => e.type === "message.created").length)).toBe(1);
  expect(await page.evaluate(() => (window as any).conflict)).toEqual({ status: 409, code: "revision_conflict" });
  expect(await page.evaluate(() => (window as any).errors)).toEqual([]);
  await page.evaluate(() => (window as any).unsubscribe());
});

test("actual TS client turns a non-JSON error response into a typed ApiError", async ({ page }) => {
  await page.goto("/");
  await page.route("**/projects", (route) => route.fulfill({
    status: 502, contentType: "text/html", body: "<h1>Bad Gateway</h1>",
    headers: { "access-control-allow-origin": "http://127.0.0.1:19842", "access-control-allow-credentials": "true" },
  }));
  const outcome = await page.evaluate(async () => {
    const module = await import(/* @vite-ignore */ "/client.js");
    const client = new module.ApiClient("http://127.0.0.1:19841");
    try { await client.projects(); return { thrown: false }; }
    catch (e) { return { thrown: true, name: (e as any).constructor.name, status: (e as any).status, code: (e as any).detail?.code }; }
  });
  expect(outcome).toEqual({ thrown: true, name: "ApiError", status: 502, code: "http_error" });
});
