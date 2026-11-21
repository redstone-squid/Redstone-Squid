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
      const patch = request.postDataJSON() as { welcome_channel_id: string };
      if (patch.welcome_channel_id === "invalid") {
        await route.fulfill({
          status: 422,
          headers,
          json: { title: "Invalid setting", status: 422, detail: "Enter a Discord channel ID." },
        });
        return;
      }
      expect(patch).toEqual({ welcome_channel_id: "1356094722531393681" });
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
  const value = page.getByLabel("Value", { exact: true });
  const save = page.getByRole("button", { name: "Save setting" });
  await value.fill("invalid");
  await save.click();
  await expect(value).toHaveAccessibleDescription("Enter a Discord channel ID.");
  await expect(value).toHaveAttribute("aria-invalid", "true");
  await expect(value).toHaveValue("invalid");
  await value.fill("1356094722531393681");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await save.click();
  await expect(page.getByRole("status")).toHaveText("Setting saved.");
  await expect(page.getByLabel("Value", { exact: true })).toHaveValue("1356094722531393681");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});
