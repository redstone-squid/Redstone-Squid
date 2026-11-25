import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import ConsentGate from "@/features/account/ConsentGate.svelte";
import SignInPanel from "@/features/account/SignInPanel.svelte";
import * as m from "@/locales/messages.gen";

const discord = { provider: { id: "discord" }, href: "/v1/auth/discord" };

describe("sign-in", () => {
  it("names known providers and falls back to the slug for new ones", () => {
    render(SignInPanel, {
      providers: [discord, { provider: { id: "github" }, href: "/v1/auth/github" }],
      authError: null,
    });
    expect(
      screen.getByRole("link", { name: m.signInContinue({ provider: "Discord" }) }),
    ).toHaveAttribute("href", "/v1/auth/discord");
    expect(
      screen.getByRole("link", { name: m.signInContinue({ provider: "github" }) }),
    ).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("still reports a failure whose code this build does not know", () => {
    render(SignInPanel, { providers: [discord], authError: "new_failure" });
    expect(screen.getByRole("alert")).toHaveTextContent(m.signInErrorGeneric());
  });

  it("explains that sign-in is off when no provider is configured", () => {
    render(SignInPanel, { providers: [], authError: "identity_in_use" });
    expect(screen.getByRole("alert")).toHaveTextContent(m.signInErrorIdentityInUse());
    expect(screen.getByText(m.signInUnavailable())).toBeVisible();
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("consent", () => {
  const notice = {
    version: "2026-08-18",
    locale: "en",
    title: "Privacy notice",
    body: "One.\n\n  Two.  ",
  };

  it("splits the notice into paragraphs and posts the version shown", () => {
    const { container } = render(ConsentGate, { notice, error: null });
    expect([...container.querySelectorAll("p")].map((p) => p.textContent)).toContain("Two.");
    expect(container.querySelector<HTMLInputElement>('input[name="version"]')?.value).toBe(
      "2026-08-18",
    );
    expect(screen.getByRole("button", { name: m.consentDecline() })).toHaveAttribute(
      "value",
      "decline",
    );
  });

  it("says why the last answer did not go through", () => {
    render(ConsentGate, { notice, error: "failed" });
    expect(screen.getByRole("alert")).toHaveTextContent(m.consentErrorFailed());
  });
});
