import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import MinecraftLink from "../../src/components/MinecraftLink";
import { SubmissionApiError, type MinecraftLinkApi } from "../../src/lib/submission-api";
import type { RuntimeConfig } from "../../src/lib/config";

const config: RuntimeConfig = {
  apiBaseUrl: "https://api.catalogue.test",
  siteUrl: "https://catalogue.test",
  discordCommunityUrl: "https://discord.gg/redstone",
  botInviteUrl: "https://discord.com/oauth2/authorize",
};

const approval = {
  id: "55555555-5555-4555-8555-555555555555",
  java_uuid: "66666666-6666-4666-8666-666666666666",
  origin: "paper" as const,
  approved_at: "2026-08-11T00:00:00Z",
};

function api(overrides: Partial<MinecraftLinkApi> = {}): MinecraftLinkApi {
  return {
    approve: vi.fn(() => Promise.resolve(approval)),
    signInUrl: vi.fn(
      (returnTo) =>
        `https://api.catalogue.test/v1/auth/discord?redirect_to=${encodeURIComponent(returnTo)}`,
    ),
    ...overrides,
  };
}

describe("MinecraftLink", () => {
  it("prefills only a short user code and never displays a device token", () => {
    const valid = render(
      <MinecraftLink locale="en" config={config} api={api()} initialCode="abcd-efgh" />,
    );
    expect(screen.getByLabelText("User code")).toHaveValue("ABCD-EFGH");
    valid.unmount();
    render(
      <MinecraftLink
        locale="en"
        config={config}
        api={api()}
        initialCode="sqpt_55555555555555555555555555555555_secret"
      />,
    );
    expect(screen.getByLabelText("User code")).toHaveValue("");
    expect(screen.queryByText(/sqpt_/)).not.toBeInTheDocument();
  });

  it("approves the normalized user code and shows only non-secret confirmation", async () => {
    const linkApi = api();
    render(<MinecraftLink locale="en" config={config} api={linkApi} />);
    fireEvent.change(screen.getByLabelText("User code"), { target: { value: "abcd-efgh" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve this player" }));
    expect(linkApi.approve).toHaveBeenCalledWith("ABCD-EFGH");
    expect(await screen.findByRole("heading", { name: "Player approved" })).toBeVisible();
    expect(screen.getByText(new RegExp(approval.java_uuid))).toHaveTextContent("Paper");
    expect(document.body.textContent).not.toMatch(/device_code|access token/i);
    fireEvent.click(screen.getByRole("button", { name: "Approve another code" }));
    expect(screen.getByLabelText("User code")).toHaveValue("");
  });

  it("validates locally and supports keyboard approval", async () => {
    const linkApi = api();
    render(<MinecraftLink locale="zh-CN" config={config} api={linkApi} />);
    fireEvent.change(screen.getByLabelText("用户代码"), { target: { value: "bad" } });
    fireEvent.click(screen.getByRole("button", { name: "批准此玩家" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("8–32");
    expect(linkApi.approve).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("用户代码"), { target: { value: "ABCD-EFGH" } });
    fireEvent.keyDown(screen.getByLabelText("用户代码"), { key: "Enter" });
    expect(await screen.findByRole("heading", { name: "玩家已获批准" })).toBeVisible();
  });

  it("offers a Discord return link on authentication and explains consent or availability errors", async () => {
    const auth = render(
      <MinecraftLink
        locale="en"
        config={config}
        api={api({ approve: vi.fn(() => Promise.reject(new SubmissionApiError(401))) })}
        initialCode="sqpt_55555555555555555555555555555555_secret"
      />,
    );
    fireEvent.change(screen.getByLabelText("User code"), { target: { value: "ABCD-EFGH" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve this player" }));
    const signIn = await screen.findByRole("link", { name: "Sign in with Discord" });
    expect(signIn).toHaveAttribute("href", expect.stringContaining("redirect_to="));
    expect(signIn.getAttribute("href")).toContain(encodeURIComponent("code=ABCD-EFGH"));
    expect(signIn.getAttribute("href")).not.toContain("sqpt_");
    auth.unmount();

    const consent = render(
      <MinecraftLink
        locale="en"
        config={config}
        api={api({
          approve: vi.fn(() =>
            Promise.reject(
              new SubmissionApiError(400, {
                title: "Consent",
                status: 400,
                code: "CONSENT_REQUIRED",
              }),
            ),
          ),
        })}
        initialCode="ABCD-EFGH"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve this player" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("privacy notice");
    consent.unmount();

    render(
      <MinecraftLink
        locale="en"
        config={config}
        api={api({ approve: vi.fn(() => Promise.reject(new SubmissionApiError(503))) })}
        initialCode="ABCD-EFGH"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve this player" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
  });

  it("preserves a safe server-authored request error", async () => {
    render(
      <MinecraftLink
        locale="en"
        config={config}
        api={api({
          approve: vi.fn(() =>
            Promise.reject(
              new SubmissionApiError(400, {
                title: "Code expired",
                status: 400,
                detail: "Start linking again in Minecraft.",
              }),
            ),
          ),
        })}
        initialCode="ABCD-EFGH"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve this player" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Start linking again in Minecraft.");
  });
});
