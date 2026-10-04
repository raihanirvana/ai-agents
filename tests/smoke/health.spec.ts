import { expect, test } from "@playwright/test";

test("status follows backend failure and recovery without reloading", async ({ page }) => {
  let available = true;
  await page.route("http://127.0.0.1:19831/health", async (route) => {
    if (available) await route.fulfill({ json: { status: "ok" } });
    else await route.abort("connectionrefused");
  });
  await page.goto("/");
  await expect(page.getByRole("status")).toHaveText("Backend terhubung");
  available = false;
  await expect(page.getByRole("status")).toHaveText("Backend tidak tersedia", { timeout: 7000 });
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  available = true;
  await expect(page.getByRole("status")).toHaveText("Backend terhubung", { timeout: 7000 });
});

test("delayed startup survives StrictMode cleanup", async ({ page }) => {
  await page.route("http://127.0.0.1:19831/health", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 400));
    await route.fulfill({ json: { status: "ok" } });
  });
  await page.goto("/");
  await expect(page.getByRole("status")).toHaveText("Backend terhubung");
});

for (const response of [
  { status: 200, json: { status: "error" } },
  { status: 503, json: { status: "ok" } },
  { status: 200, json: null },
]) {
  test(`invalid health response ${JSON.stringify(response)}`, async ({ page }) => {
    await page.route("http://127.0.0.1:19831/health", (route) => route.fulfill(response));
    await page.goto("/");
    await expect(page.getByRole("status")).toHaveText("Backend tidak tersedia");
  });
}
