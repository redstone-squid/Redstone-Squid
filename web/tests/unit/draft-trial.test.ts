import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import DraftTrial from "@/workbench/DraftTrial.svelte";
import * as m from "@/locales/messages.gen";

describe("Draft trial", () => {
  it("requires a title and a positive whole opening width for a door", async () => {
    const user = userEvent.setup();
    render(DraftTrial);
    const title = screen.getByRole("textbox", { name: m.workbenchBuildTitle() });
    const width = screen.getByRole("textbox", { name: m.trialWidth() });
    await user.type(width, "1.5");
    await user.click(screen.getByRole("button", { name: m.workbenchSaveDraft() }));
    expect(title).toBeInvalid();
    expect(title).toHaveAccessibleDescription(m.trialRequired());
    expect(width).toBeInvalid();
    expect(width).toHaveAccessibleDescription(m.trialPositive());
  });

  it("shows matching Minecraft versions as the visitor types", async () => {
    const user = userEvent.setup();
    render(DraftTrial);
    const version = screen.getByRole("combobox", { name: m.workbenchMinecraftVersion() });
    const search = screen.getByRole("searchbox", { name: m.trialFindVersion() });
    await user.type(search, "1.21.4");
    expect(
      within(version)
        .getAllByRole("option")
        .map((option) => option.getAttribute("value")),
    ).toEqual(["", "Java 1.21.4"]);
  });

  it("allows the visitor to correct a rejected title and save", async () => {
    const user = userEvent.setup();
    render(DraftTrial);
    const title = screen.getByRole("textbox", { name: m.workbenchBuildTitle() });
    const save = screen.getByRole("button", { name: m.workbenchSaveDraft() });
    const status = screen.getByRole("status");
    await user.type(title, "Reserved");
    await user.type(screen.getByRole("textbox", { name: m.trialWidth() }), "5");
    await user.selectOptions(
      screen.getByRole("combobox", { name: m.workbenchMinecraftVersion() }),
      "Java 1.21.4",
    );
    await user.click(save);
    await waitFor(() => expect(title).toHaveAccessibleDescription(m.trialReserved()));
    expect(title).toBeInvalid();
    await user.clear(title);
    await user.type(title, "Copper Bolt");
    expect(title).toBeValid();
    await user.click(save);
    await waitFor(() => expect(status).toHaveTextContent(m.trialSaved()));
    await user.type(title, " revised");
    expect(status).toHaveTextContent(m.trialReady());
  });

  it("blocks saving while a schematic uploads and shows the attachment when it finishes", async () => {
    const user = userEvent.setup();
    render(DraftTrial);
    const save = screen.getByRole("button", { name: m.workbenchSaveDraft() });
    const file = new File(["fixture"], "door.schem");
    await user.upload(screen.getByLabelText(m.trialUpload()), file);
    expect(screen.getByRole("progressbar")).toBeVisible();
    expect(save).toBeDisabled();
    expect(await screen.findByText(m.trialAttachment({ filename: file.name }))).toBeVisible();
    expect(save).toBeEnabled();
  });
  it("retains edits across a failed save and retries without requiring a hidden field", async () => {
    const user = userEvent.setup();
    render(DraftTrial, { scenario: "retry" });
    const title = screen.getByRole("textbox", { name: m.workbenchBuildTitle() });
    const status = screen.getByRole("status");
    await user.type(title, "Utility build");
    await user.selectOptions(screen.getByRole("combobox", { name: m.trialCategory() }), "utility");
    expect(screen.queryByRole("textbox", { name: m.trialWidth() })).not.toBeInTheDocument();
    await user.selectOptions(
      screen.getByRole("combobox", { name: m.workbenchMinecraftVersion() }),
      "Java 1.21.4",
    );
    await user.click(screen.getByRole("button", { name: m.workbenchSaveDraft() }));
    await user.click(await screen.findByRole("button", { name: m.trialRetry() }));
    await waitFor(() => expect(status).toHaveTextContent(m.trialSaved()));
    expect(title).toHaveValue("Utility build");
  });
  it("retries a failed upload with the same file in the upload-retry scenario", async () => {
    const user = userEvent.setup();
    render(DraftTrial, { scenario: "upload-retry" });
    const file = new File(["fixture"], "retry.schem");
    await user.upload(screen.getByLabelText(m.trialUpload()), file);
    expect(await screen.findByRole("alert")).toHaveTextContent(m.trialUploadFailure());
    await user.click(screen.getByRole("button", { name: m.trialRetryUpload() }));
    expect(await screen.findByText(m.trialAttachment({ filename: file.name }))).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("re-enables saving when the visitor cancels an upload", async () => {
    const user = userEvent.setup();
    render(DraftTrial);
    const save = screen.getByRole("button", { name: m.workbenchSaveDraft() });
    const file = new File(["fixture"], "door.schem");
    await user.upload(screen.getByLabelText(m.trialUpload()), file);
    expect(save).toBeDisabled();
    await user.click(screen.getByRole("button", { name: m.trialCancel() }));
    expect(save).toBeEnabled();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByText(m.trialAttachment({ filename: file.name }))).not.toBeInTheDocument();
  });
});
