import { expect, test } from "@playwright/test";
import fixtures from "../../../contracts/fixtures/cli-operations.json" with { type: "json" };

test("global settings preserve Discord IDs through browser editing", async ({ page }) => {
  const settings = fixtures.operations.runtime_settings_get.success.body;
  const headers = {
    "Access-Control-Allow-Origin": "http://127.0.0.1:4321",
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "content-type, csrf-token, idempotency-key, accept-language",
    "Access-Control-Allow-Methods": "GET, PATCH, OPTIONS",
  };
  await page.route("**/v1/auth/csrf", (route) =>
    route.fulfill({ json: { csrf_token: "test-csrf" }, headers }),
  );
  await page.route("**/v1/settings/runtime", async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
    } else if (request.method() === "PATCH") {
      expect(request.headers()["csrf-token"]).toBe("test-csrf");
      expect(request.headers()["idempotency-key"]).toBeTruthy();
      expect(request.postDataJSON()).toEqual({ welcome_channel_id: "1356094722531393681" });
      await route.fulfill({
        json: { ...settings, welcome_channel_id: "1356094722531393681" },
        headers,
      });
    } else {
      await route.fulfill({ json: settings, headers });
    }
  });
  await page.goto("/admin/settings");
  await page.getByLabel("Setting", { exact: true }).selectOption("welcome_channel_id");
  await page.getByLabel("Value", { exact: true }).fill("1356094722531393681");
  await page.getByRole("button", { name: "Save setting" }).click();
  await expect(page.getByRole("status")).toHaveText("Setting saved.");
  await expect(page.getByLabel("Value", { exact: true })).toHaveValue("1356094722531393681");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});
