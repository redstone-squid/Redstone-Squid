import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ConsentGate from "../../src/components/ConsentGate";
import CliLink from "../../src/components/CliLink";
import MinecraftLink from "../../src/components/MinecraftLink";
import {
  SubmissionApiError,
  type CliLinkApi,
  type ConsentApi,
  type MinecraftLinkApi,
} from "../../src/lib/submission-api";
import type { RuntimeConfig } from "../../src/lib/config";

const config: RuntimeConfig = {
  apiBaseUrl: "https://api.catalogue.test",
  siteUrl: "https://catalogue.test",
  discordCommunityUrl: "https://discord.gg/redstone",
  botInviteUrl: "https://discord.com/oauth2/authorize",
};

const notice = {
  version: "2026-08-04",
  locale: "en",
  title: "Privacy notice",
  body: "First paragraph.\n\nSecond paragraph.",
};

function consentApi(overrides: Partial<ConsentApi> = {}): ConsentApi {
  return {
    notice: vi.fn(() => Promise.resolve(notice)),
    grant: vi.fn(() => Promise.resolve()),
    ...overrides,
  };
}

function consentRefusal(): SubmissionApiError {
  return new SubmissionApiError(400, {
    title: "Consent required",
    status: 400,
    code: "CONSENT_REQUIRED",
  });
}

describe("ConsentGate", () => {
  it("shows the notice it is about to record acceptance of", async () => {
    render(<ConsentGate locale="en" config={config} api={consentApi()} onAccepted={vi.fn()} />);

    expect(await screen.findByText("Privacy notice")).toBeInTheDocument();
    expect(screen.getByText("First paragraph.")).toBeInTheDocument();
    expect(screen.getByText("Second paragraph.")).toBeInTheDocument();
    expect(screen.getByText("Version 2026-08-04")).toBeInTheDocument();
  });

  it("records the version that was displayed, not whatever is current", async () => {
    const api = consentApi();
    const onAccepted = vi.fn();
    render(<ConsentGate locale="en" config={config} api={api} onAccepted={onAccepted} />);

    await userEvent.click(await screen.findByRole("button", { name: "Accept and continue" }));

    await waitFor(() => expect(api.grant).toHaveBeenCalledWith("2026-08-04"));
    expect(onAccepted).toHaveBeenCalledTimes(1);
  });

  it("does not continue when the grant is refused", async () => {
    const api = consentApi({ grant: vi.fn(() => Promise.reject(new SubmissionApiError(503))) });
    const onAccepted = vi.fn();
    render(<ConsentGate locale="en" config={config} api={api} onAccepted={onAccepted} />);

    await userEvent.click(await screen.findByRole("button", { name: "Accept and continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your acceptance could not be recorded.",
    );
    expect(onAccepted).not.toHaveBeenCalled();
  });

  it("reports a notice that changed while it was being read", async () => {
    const stale = new SubmissionApiError(409, {
      title: "Privacy notice out of date",
      status: 409,
      code: "CONSENT_VERSION_STALE",
    });
    const api = consentApi({ grant: vi.fn(() => Promise.reject(stale)) });
    render(<ConsentGate locale="en" config={config} api={api} onAccepted={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: "Accept and continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The notice changed");
  });

  it("says so when the notice itself cannot be loaded", async () => {
    const api = consentApi({ notice: vi.fn(() => Promise.reject(new SubmissionApiError(503))) });
    render(<ConsentGate locale="en" config={config} api={api} onAccepted={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The privacy notice could not be loaded.",
    );
  });

  it("lets the reader decline without accepting", async () => {
    const api = consentApi();
    const onCancel = vi.fn();
    render(
      <ConsentGate
        locale="en"
        config={config}
        api={api}
        onAccepted={vi.fn()}
        onCancel={onCancel}
      />,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Not now" }));

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(api.grant).not.toHaveBeenCalled();
  });

  it("re-reads the notice when the version it displayed went stale", async () => {
    const replacement = { ...notice, version: "2026-08-18", body: "Newer text." };
    const stale = new SubmissionApiError(409, {
      title: "Privacy notice out of date",
      status: 409,
      code: "CONSENT_VERSION_STALE",
    });
    const api = consentApi({
      notice: vi.fn().mockResolvedValueOnce(notice).mockResolvedValueOnce(replacement),
      grant: vi.fn(() => Promise.reject(stale)),
    });
    render(<ConsentGate locale="en" config={config} api={api} onAccepted={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: "Accept and continue" }));

    // The reader is shown the new text rather than being asked to re-accept the old one.
    expect(await screen.findByText("Newer text.")).toBeInTheDocument();
    expect(screen.getByText("Version 2026-08-18")).toBeInTheDocument();
  });

  it("is localized", async () => {
    render(<ConsentGate locale="zh-CN" config={config} api={consentApi()} onAccepted={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "接受并继续" })).toBeInTheDocument();
  });
});

describe("consent recovery", () => {
  it("lets the reader back out of the gate without approving", async () => {
    const approve = vi.fn<CliLinkApi["approve"]>().mockRejectedValue(consentRefusal());
    const linkApi: CliLinkApi = {
      preview: vi.fn(() =>
        Promise.resolve({
          id: "55555555-5555-4555-8555-555555555555",
          client_instance_id: "66666666-6666-4666-8666-666666666666",
          label: "Alice's workstation",
          public_key_fingerprint: "1234-5678-90AB-CDEF-1234",
          created_at: "2026-08-11T00:00:00Z",
          expires_at: "2026-08-11T00:10:00Z",
          approved_at: null,
        }),
      ),
      approve,
      signInUrl: vi.fn(() => "https://api.catalogue.test/v1/auth/discord"),
    };

    render(
      <CliLink
        locale="en"
        config={config}
        initialCode="ABCD-EFGH"
        api={linkApi}
        consentApi={consentApi()}
      />,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Review this device" }));
    await userEvent.click(await screen.findByRole("button", { name: "Approve this CLI device" }));
    await userEvent.click(await screen.findByRole("button", { name: "Not now" }));

    await waitFor(() =>
      expect(screen.queryByText("Accept the privacy notice")).not.toBeInTheDocument(),
    );
    expect(approve).toHaveBeenCalledTimes(1);
  });

  it("retries the refused action exactly once after the notice is accepted", async () => {
    const approve = vi
      .fn<CliLinkApi["approve"]>()
      .mockRejectedValueOnce(consentRefusal())
      .mockResolvedValueOnce({
        id: "55555555-5555-4555-8555-555555555555",
        client_instance_id: "66666666-6666-4666-8666-666666666666",
        label: "Alice's workstation",
        public_key_fingerprint: "1234-5678-90AB-CDEF-1234",
        created_at: "2026-08-11T00:00:00Z",
        expires_at: "2026-08-11T00:10:00Z",
        approved_at: "2026-08-11T00:01:00Z",
      });
    const linkApi: CliLinkApi = {
      preview: vi.fn(() =>
        Promise.resolve({
          id: "55555555-5555-4555-8555-555555555555",
          client_instance_id: "66666666-6666-4666-8666-666666666666",
          label: "Alice's workstation",
          public_key_fingerprint: "1234-5678-90AB-CDEF-1234",
          created_at: "2026-08-11T00:00:00Z",
          expires_at: "2026-08-11T00:10:00Z",
          approved_at: null,
        }),
      ),
      approve,
      signInUrl: vi.fn(() => "https://api.catalogue.test/v1/auth/discord"),
    };

    render(
      <CliLink
        locale="en"
        config={config}
        initialCode="ABCD-EFGH"
        api={linkApi}
        consentApi={consentApi()}
      />,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Review this device" }));
    await userEvent.click(await screen.findByRole("button", { name: "Approve this CLI device" }));

    // The refusal is recoverable in place rather than a dead end.
    expect(await screen.findByText("Accept the privacy notice")).toBeInTheDocument();

    await userEvent.click(await screen.findByRole("button", { name: "Accept and continue" }));

    expect(await screen.findByText("CLI device approved")).toBeInTheDocument();
    expect(approve).toHaveBeenCalledTimes(2);
  });

  it("recovers a refused Minecraft player approval in place", async () => {
    const approve = vi
      .fn<MinecraftLinkApi["approve"]>()
      .mockRejectedValueOnce(consentRefusal())
      .mockResolvedValueOnce({
        id: "77777777-7777-4777-8777-777777777777",
        java_uuid: "11111111-1111-4111-8111-111111111111",
        origin: "paper",
        approved_at: "2026-08-11T00:01:00Z",
      });
    const linkApi: MinecraftLinkApi = {
      approve,
      signInUrl: vi.fn(() => "https://api.catalogue.test/v1/auth/discord"),
    };

    render(
      <MinecraftLink
        locale="en"
        config={config}
        initialCode="ABCDEFGH"
        api={linkApi}
        consentApi={consentApi()}
      />,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Approve this player" }));
    expect(await screen.findByText("Accept the privacy notice")).toBeInTheDocument();

    await userEvent.click(await screen.findByRole("button", { name: "Accept and continue" }));

    expect(await screen.findByText("Player approved")).toBeInTheDocument();
    expect(approve).toHaveBeenCalledTimes(2);
  });
});
