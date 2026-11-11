import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import fixtures from "../../../contracts/fixtures/cli-operations.json";
import RuntimeSettings from "../../src/components/RuntimeSettings";
import { SubmissionApiError, type RuntimeSettingsApi } from "../../src/lib/submission-api";

const config = {
  apiBaseUrl: "https://api.catalogue.test",
  siteUrl: "https://catalogue.test",
  discordCommunityUrl: "https://discord.gg/redstone",
  botInviteUrl: "https://discord.com/oauth2/authorize",
};
const settings = fixtures.operations.runtime_settings_get.success.body;
function api(): RuntimeSettingsApi {
  return {
    get: vi.fn(() => Promise.resolve(settings)),
    update: vi.fn((patch) => Promise.resolve({ ...settings, ...patch } as typeof settings)),
    signInUrl: () => "https://api.catalogue.test/v1/auth/discord",
  };
}
describe("RuntimeSettings", () => {
  it("saves only the selected field without rounding Discord IDs", async () => {
    const client = api();
    render(<RuntimeSettings locale="en" config={config} api={client} />);
    await screen.findByRole("button", { name: "Save setting" });
    fireEvent.change(screen.getByLabelText("Setting"), { target: { value: "welcome_channel_id" } });
    expect(screen.getByLabelText("Value")).toHaveValue(settings.welcome_channel_id);
    fireEvent.change(screen.getByLabelText("Value"), { target: { value: "1356094722531393681" } });
    fireEvent.click(screen.getByRole("button", { name: "Save setting" }));
    await screen.findByText("Setting saved.");
    expect(client.update).toHaveBeenCalledWith(
      { welcome_channel_id: "1356094722531393681" },
      settings.revision,
    );
  });
  it("writes boolean values", async () => {
    const client = api();
    render(<RuntimeSettings locale="en" config={config} api={client} />);
    await screen.findByRole("button", { name: "Save setting" });
    fireEvent.change(screen.getByLabelText("Setting"), { target: { value: "welcome_enabled" } });
    fireEvent.change(screen.getByLabelText("Value"), { target: { value: "false" } });
    fireEvent.click(screen.getByRole("button", { name: "Save setting" }));
    await screen.findByText("Setting saved.");
    expect(client.update).toHaveBeenCalledWith({ welcome_enabled: false }, settings.revision);
  });
  it("offers sign-in when unauthenticated", async () => {
    const client = api();
    client.get = vi.fn(() => Promise.reject(new SubmissionApiError(401)));
    render(<RuntimeSettings locale="en" config={config} api={client} />);
    expect(await screen.findByRole("link", { name: "Sign in" })).toBeVisible();
    expect(screen.queryByLabelText("Setting")).not.toBeInTheDocument();
  });
  it("refuses an editor when permission is denied", async () => {
    const client = api();
    client.get = vi.fn(() => Promise.reject(new SubmissionApiError(403)));
    render(<RuntimeSettings locale="en" config={config} api={client} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Global administrator permission is required.",
    );
    expect(screen.queryByLabelText("Setting")).not.toBeInTheDocument();
  });
});

it("preserves the entered probability and reports a refused write", async () => {
  const client = api();
  client.update = vi.fn(() =>
    Promise.reject(
      new SubmissionApiError(422, {
        title: "Invalid setting",
        status: 422,
        detail: "Probability is outside its range.",
      }),
    ),
  );
  render(<RuntimeSettings locale="en" config={config} api={client} />);
  await screen.findByRole("button", { name: "Save setting" });
  fireEvent.change(screen.getByLabelText("Value"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save setting" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Probability is outside its range.");
  expect(client.update).toHaveBeenCalledWith({ welcome_forward_chance: 2 }, settings.revision);
  expect(screen.getByLabelText("Value")).toHaveValue(2);
  expect(screen.queryByText("Setting saved.")).not.toBeInTheDocument();
});

it("saves comma-separated channel IDs as exact strings", async () => {
  const client = api();
  render(<RuntimeSettings locale="en" config={config} api={client} />);
  await screen.findByRole("button", { name: "Save setting" });
  fireEvent.change(screen.getByLabelText("Setting"), {
    target: { value: "build_log_channel_ids" },
  });
  fireEvent.change(screen.getByLabelText("Value"), {
    target: { value: "1356094722531393681, 1356094722531393682" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save setting" }));
  await screen.findByText("Setting saved.");
  expect(client.update).toHaveBeenCalledWith(
    {
      build_log_channel_ids: ["1356094722531393681", "1356094722531393682"],
    },
    settings.revision,
  );
});

it("offers the same editor and success feedback in Chinese", async () => {
  const client = api();
  render(<RuntimeSettings locale="zh-CN" config={config} api={client} />);
  await screen.findByRole("button", { name: "保存" });
  fireEvent.change(screen.getByLabelText("值"), { target: { value: "0.25" } });
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  expect(await screen.findByRole("status")).toHaveTextContent("设置已保存。");
  expect(client.update).toHaveBeenCalledWith({ welcome_forward_chance: 0.25 }, settings.revision);
});
