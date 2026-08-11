import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CliLink from "../../src/components/CliLink";
import { SubmissionApiError, type CliLinkApi } from "../../src/lib/submission-api";
import type { RuntimeConfig } from "../../src/lib/config";

const config: RuntimeConfig = {
  apiBaseUrl: "https://api.catalogue.test",
  siteUrl: "https://catalogue.test",
  discordCommunityUrl: "https://discord.gg/redstone",
  botInviteUrl: "https://discord.com/oauth2/authorize",
};

const preview = {
  id: "55555555-5555-4555-8555-555555555555",
  client_instance_id: "66666666-6666-4666-8666-666666666666",
  label: "Alice's workstation",
  public_key_fingerprint: "1234-5678-90AB-CDEF-1234",
  created_at: "2026-08-11T00:00:00Z",
  expires_at: "2026-08-11T00:10:00Z",
  approved_at: null,
};

function api(overrides: Partial<CliLinkApi> = {}): CliLinkApi {
  return {
    preview: vi.fn(() => Promise.resolve(preview)),
    approve: vi.fn(() => Promise.resolve({ ...preview, approved_at: "2026-08-11T00:01:00Z" })),
    signInUrl: vi.fn(
      (returnTo) =>
        `https://api.catalogue.test/v1/auth/discord?redirect_to=${encodeURIComponent(returnTo)}`,
    ),
    ...overrides,
  };
}

describe("CliLink", () => {
  beforeEach(() => {
    sessionStorage.clear();
    window.history.replaceState(null, "", "/cli/link");
  });

  it("consumes the URL fragment once and requires fingerprint review before approval", async () => {
    window.history.replaceState(null, "", "/cli/link#code=abcd-efgh");
    const linkApi = api();
    render(<CliLink locale="en" config={config} api={linkApi} />);

    await waitFor(() => expect(screen.getByLabelText("User code")).toHaveValue("ABCD-EFGH"));
    expect(window.location.hash).toBe("");
    expect(sessionStorage.getItem("squid.cli.enrollment_code")).toBe("ABCD-EFGH");

    fireEvent.click(screen.getByRole("button", { name: "Review this device" }));
    expect(await screen.findByText(preview.label)).toBeVisible();
    expect(screen.getByText(preview.public_key_fingerprint)).toBeVisible();
    expect(linkApi.preview).toHaveBeenCalledWith("ABCD-EFGH");

    fireEvent.click(screen.getByRole("button", { name: "Approve this CLI device" }));
    expect(await screen.findByRole("heading", { name: "CLI device approved" })).toBeVisible();
    expect(linkApi.approve).toHaveBeenCalledWith("ABCD-EFGH");
    expect(sessionStorage.getItem("squid.cli.enrollment_code")).toBeNull();
  });

  it("keeps the code in session storage instead of the Discord return URL", async () => {
    const linkApi = api({
      preview: vi.fn(() => Promise.reject(new SubmissionApiError(401))),
    });
    render(<CliLink locale="en" config={config} api={linkApi} initialCode="ABCD-EFGH" />);

    fireEvent.click(screen.getByRole("button", { name: "Review this device" }));
    const signIn = await screen.findByRole("link", { name: "Sign in with Discord" });
    const href = signIn.getAttribute("href") ?? "";

    expect(href).toContain(encodeURIComponent("https://catalogue.test/cli/link"));
    expect(href).not.toContain("ABCD-EFGH");
    expect(sessionStorage.getItem("squid.cli.enrollment_code")).toBe("ABCD-EFGH");
  });

  it("validates codes locally and never sends pasted tokens", async () => {
    const linkApi = api();
    render(<CliLink locale="zh-CN" config={config} api={linkApi} />);
    fireEvent.change(screen.getByLabelText("用户代码"), {
      target: { value: "squid_cli_v1_secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "检查此设备" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("8–32");
    expect(linkApi.preview).not.toHaveBeenCalled();
  });
});
