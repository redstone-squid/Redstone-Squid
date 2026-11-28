import type { BrowserContext } from "@playwright/test";
import * as m from "@/locales/messages.gen";
import { API_PORT } from "@tests/support/servers";
import { expect, test } from "@tests/support/test";

// These flows navigate several times in a row. Without reduced motion the pages cross-fade,
// so axe can sample half-faded text and a quick click aborts the transition with a console error.
test.use({ reducedMotion: "reduce" });

/** Sign the browser in as one of the fixture API's named sessions. */
async function signInAs(context: BrowserContext, session: "member" | "pending" | "stale") {
  // Playwright's Linux WebKit never sends Secure cookies over HTTP, even to 127.0.0.1, so the
  // session can't reach the test servers. Production is HTTPS.
  test.skip(
    context.browser()?.browserType().name() === "webkit",
    "WebKit drops Secure cookies over HTTP",
  );
  await context.addCookies([
    {
      name: "__Host-squid_session",
      value: session,
      domain: "127.0.0.1",
      path: "/",
      secure: true,
      httpOnly: true,
    },
    { name: "squid_csrf", value: "fixture-csrf", domain: "127.0.0.1", path: "/", secure: true },
  ]);
}

test.describe("with JavaScript disabled", () => {
  test.use({ javaScriptEnabled: false });

  test("starts sign-in from the header and keeps the localized destination", async ({ page }) => {
    await page.goto("/zh-cn/records?page_size=3");
    await page.getByRole("link", { name: m.navSignIn("zh-CN") }).click();
    await expect(page).toHaveURL(
      `/zh-cn/sign-in?redirect_to=${encodeURIComponent("/zh-cn/records?page_size=3")}`,
    );
    const provider = page.getByRole("link", {
      name: m.signInContinue({ provider: "Discord" }, "zh-CN"),
    });
    const destination = "/zh-cn/records?page_size=3";
    await expect(provider).toHaveAttribute(
      "href",
      `http://127.0.0.2:${API_PORT}/v1/auth/discord?redirect_to=${encodeURIComponent(destination)}`,
    );
  });

  test("shows the callback's error and keeps the retry destination", async ({ page }) => {
    await page.goto("/sign-in?auth_error=cancelled&redirect_to=/builds");
    await expect(page.getByRole("alert")).toHaveText(m.signInErrorCancelled("en"));
    await expect(
      page.getByRole("link", { name: m.signInContinue({ provider: "Discord" }, "en") }),
    ).toHaveAttribute("href", /redirect_to=%2Fbuilds$/);
  });

  test("refuses to send the visitor to another site after sign-in", async ({ page }) => {
    await page.goto("/sign-in?redirect_to=//evil.example/");
    await expect(
      page.getByRole("link", { name: m.signInContinue({ provider: "Discord" }, "en") }),
    ).toHaveAttribute("href", /redirect_to=%2F$/);
  });

  test("links the policies before sign-in", async ({ page }) => {
    await page.goto("/zh-cn/sign-in");
    await expect(page.getByRole("link", { name: m.termsTitle("zh-CN") })).toHaveAttribute(
      "href",
      "/zh-cn/terms",
    );
    await expect(page.getByRole("link", { name: m.privacyTitle("zh-CN") })).toHaveAttribute(
      "href",
      "/zh-cn/privacy",
    );
  });

  test("opens a prediction for the reviewed revision", async ({ page, context }) => {
    await signInAs(context, "member");
    await page.goto("/account/submissions");
    await page.getByRole("link", { name: "Piston relay" }).click();
    const [prediction] = await Promise.all([
      context.waitForEvent("page"),
      page.getByRole("link", { name: m.predictionTitle("en") }).click(),
    ]);
    await expect(prediction.getByRole("heading", { name: "First 2×2 door" })).toBeVisible();
    await expect(prediction.getByText(/Undecided/)).toBeVisible();
    await expect(prediction.getByText(/Missing facts: completion/)).toBeVisible();
  });

  test("opens a rejected submission and a separate credited build", async ({ page, context }) => {
    await signInAs(context, "member");
    await page.goto("/account/submissions");
    await page.getByRole("link", { name: "Piston relay" }).click();
    await expect(page.getByText("Missing timing evidence")).toBeVisible();
    await expect(page.getByRole("link", { name: m.submissionViewPublic("en") })).toHaveCount(0);
    await page.goto("/account/builds");
    await page.getByRole("link", { name: "Copper Bolt" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Copper Bolt");
  });

  test("can enable and disable indexing without JavaScript", async ({ page, context }) => {
    await signInAs(context, "member");
    await page.goto("/settings");
    const toggle = page.getByRole("checkbox", { name: m.indexingTitle("en") });
    await toggle.check();
    await page.getByRole("button", { name: m.indexingSave("en") }).click();
    await expect(toggle).toBeChecked();
    await toggle.uncheck();
    await page.getByRole("button", { name: m.indexingSave("en") }).click();
    await expect(toggle).not.toBeChecked();
  });

  test("signs out from the account menu", async ({ page, context }) => {
    await signInAs(context, "member");
    await page.goto("/zh-cn/builds");
    await page.getByRole("button", { name: "Copper Fan" }).click();
    await page.getByRole("button", { name: m.navSignOut("zh-CN") }).click();
    await expect(page).toHaveURL("/zh-cn/builds");
    await expect(page.getByRole("link", { name: m.navSignIn("zh-CN") })).toBeVisible();
  });
});

test("polls pending finalization using the status read during rendering", async ({
  page,
  context,
}) => {
  await signInAs(context, "member");
  // The production gateway serves /v1 on the site's origin. The fixture runs on its own port.
  await page.route("**/v1/submissions/drafts/active/status", async (route) => {
    const response = await route.fetch({
      url: `http://127.0.0.1:${API_PORT}/v1/submissions/drafts/active/status`,
    });
    await route.fulfill({ response });
  });
  const poll = page.waitForRequest(
    (request) =>
      request.url().endsWith("/v1/submissions/drafts/active/status") && request.method() === "GET",
  );
  await page.goto("/submit/active/status");
  expect((await poll).headers()["if-none-match"]).toBe('"pending"');
  await expect(page.getByRole("heading", { name: m.submissionProcessing("en") })).toBeVisible();
});

test("sign-in, terms and the account menu pass axe", async ({ page, context }) => {
  await page.goto("/sign-in?auth_error=provider_unavailable");
  await expect(page).toPassAxe();
  await signInAs(context, "pending");
  await page.goto("/terms");
  await expect(page).toPassAxe();
  await page.goto("/builds");
  await page.getByRole("button", { name: "New Member" }).click();
  await expect(page.getByRole("button", { name: m.navSignOut("en") })).toBeVisible();
  await expect(page).toPassAxe();
});

test("a signed-in visitor skips sign-in", async ({ request }) => {
  const cookies = { Cookie: "__Host-squid_session=member; squid_csrf=fixture-csrf" };
  const signIn = await request.get("/sign-in?redirect_to=/builds", {
    headers: cookies,
    maxRedirects: 0,
  });
  expect(signIn.status()).toBe(302);
  expect(signIn.headers().location).toBe("/builds");
  expect(signIn.headers()["cache-control"]).toBe("private, no-store");
});
