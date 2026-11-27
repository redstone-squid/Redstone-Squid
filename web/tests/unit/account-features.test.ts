import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
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
