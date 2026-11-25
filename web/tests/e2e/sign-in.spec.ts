import type { BrowserContext } from "@playwright/test";
import * as m from "@/locales/messages.gen";
import { API_PORT } from "@tests/support/servers";
import { expect, test } from "@tests/support/test";

// These flows navigate several times in a row. Without reduced motion the pages cross-fade,
// so axe can sample half-faded text and a quick click aborts the transition with a console error.
test.use({ reducedMotion: "reduce" });

/** Sign the browser in as one of the fixture API's named sessions. */
async function signInAs(context: BrowserContext, session: "member" | "pending" | "stale") {
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

  test("starts sign-in from the header and returns through consent", async ({ page }) => {
    await page.goto("/zh-cn/records?page_size=3");
    await page.getByRole("link", { name: m.navSignIn("zh-CN") }).click();
    await expect(page).toHaveURL(
      `/zh-cn/sign-in?redirect_to=${encodeURIComponent("/zh-cn/records?page_size=3")}`,
    );
    const provider = page.getByRole("link", {
      name: m.signInContinue({ provider: "Discord" }, "zh-CN"),
    });
    const consent = `/zh-cn/consent?redirect_to=${encodeURIComponent("/zh-cn/records?page_size=3")}`;
    await expect(provider).toHaveAttribute(
      "href",
      `http://127.0.0.2:${API_PORT}/v1/auth/discord?redirect_to=${encodeURIComponent(consent)}`,
    );
  });

  test("shows the callback's error and keeps the retry destination", async ({ page }) => {
    await page.goto("/sign-in?auth_error=cancelled&redirect_to=/builds");
    await expect(page.getByRole("alert")).toHaveText(m.signInErrorCancelled("en"));
    await expect(
      page.getByRole("link", { name: m.signInContinue({ provider: "Discord" }, "en") }),
    ).toHaveAttribute("href", /redirect_to=%2Fconsent%3Fredirect_to%3D%252Fbuilds$/);
  });

  test("refuses to send the visitor to another site after sign-in", async ({ page }) => {
    await page.goto("/sign-in?redirect_to=//evil.example/");
    await expect(
      page.getByRole("link", { name: m.signInContinue({ provider: "Discord" }, "en") }),
    ).toHaveAttribute("href", /redirect_to=%2Fconsent%3Fredirect_to%3D%252F$/);
  });

  test("accepts the notice and continues to the page that asked", async ({ page, context }) => {
    await signInAs(context, "pending");
    await page.goto("/records");
    await expect(page.getByText(m.consentPendingBanner("en"))).toBeVisible();
    await page
      .getByRole("link", { name: m.navReviewConsent("en") })
      .first()
      .click();
    await expect(page.getByRole("heading", { level: 1, name: "Privacy notice" })).toBeVisible();
    await page.getByRole("button", { name: m.consentAccept("en") }).click();
    await expect(page).toHaveURL("/records");
  });

  test("asks again when the notice changed while reading", async ({ page, context }) => {
    await signInAs(context, "stale");
    await page.goto("/consent?redirect_to=/builds");
    await page.getByRole("button", { name: m.consentAccept("en") }).click();
    await expect(page.getByRole("alert")).toHaveText(m.consentErrorStale("en"));
    await expect(page).toHaveURL("/consent?redirect_to=/builds");
  });

  test("declining signs the visitor out", async ({ page, context }) => {
    await signInAs(context, "pending");
    await page.goto("/consent?redirect_to=/builds");
    await page.getByRole("button", { name: m.consentDecline("en") }).click();
    await expect(page).toHaveURL("/builds");
    await expect(page.getByRole("link", { name: m.navSignIn("en") })).toBeVisible();
    expect((await context.cookies()).map(({ name }) => name)).not.toContain("__Host-squid_session");
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

test("sign-in, consent and the account menu pass axe", async ({ page, context }) => {
  await page.goto("/sign-in?auth_error=provider_unavailable");
  await expect(page).toPassAxe();
  await signInAs(context, "pending");
  await page.goto("/consent?redirect_to=/builds");
  await expect(page).toPassAxe();
  await page.goto("/builds");
  await page.getByRole("button", { name: "New Member" }).click();
  await expect(page.getByRole("button", { name: m.navSignOut("en") })).toBeVisible();
  await expect(page).toPassAxe();
});

test("a signed-in visitor skips sign-in and consent", async ({ request }) => {
  const cookies = { Cookie: "__Host-squid_session=member; squid_csrf=fixture-csrf" };
  const signIn = await request.get("/sign-in?redirect_to=/builds", {
    headers: cookies,
    maxRedirects: 0,
  });
  expect(signIn.status()).toBe(302);
  expect(signIn.headers().location).toBe("/builds");
  expect(signIn.headers()["cache-control"]).toBe("private, no-store");
  const consent = await request.get("/consent?redirect_to=/builds", {
    headers: cookies,
    maxRedirects: 0,
  });
  expect(consent.headers().location).toBe("/builds");
});

test("an anonymous visitor is sent from consent to sign-in", async ({ request }) => {
  const response = await request.get("/zh-cn/consent?redirect_to=/zh-cn/builds", {
    maxRedirects: 0,
  });
  expect(response.headers().location).toBe(
    `/zh-cn/sign-in?redirect_to=${encodeURIComponent("/zh-cn/builds")}`,
  );
});
