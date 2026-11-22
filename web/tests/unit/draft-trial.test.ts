import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import DraftTrial from "@/workbench/DraftTrial.svelte";

describe("Draft trial", () => {
  it("requires a title and a positive whole opening width for a door", async () => {
    const user = userEvent.setup();
    render(DraftTrial);
    await user.type(screen.getByLabelText("Opening width"), "1.5");
    await user.click(screen.getByRole("button", { name: "Save draft" }));
    expect(screen.getByLabelText("Build title")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Opening width")).toHaveAccessibleDescription(
      "Enter a positive whole number.",
    );
  });

  it("shows matching Minecraft versions as the visitor types", async () => {
    const user = userEvent.setup();
    render(DraftTrial);
    await user.type(screen.getByLabelText("Find a version"), "1.21.4");
    expect(screen.getByRole("option", { name: "Java 1.21.4" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Bedrock 1.21.50" })).not.toBeInTheDocument();
  });

  it("allows the visitor to correct a rejected title and save", async () => {
    const user = userEvent.setup();
    render(DraftTrial);
    const title = screen.getByLabelText("Build title");
    const save = screen.getByRole("button", { name: "Save draft" });
    await user.type(title, "Reserved");
    await user.type(screen.getByLabelText("Opening width"), "5");
    await user.selectOptions(screen.getByLabelText("Minecraft version"), "Java 1.21.4");
    await user.click(save);
    expect(await screen.findByText("This title is already taken.")).toBeVisible();
    await user.clear(title);
    await user.type(title, "Copper Bolt");
    await user.click(save);
    expect(await screen.findByText("Draft saved.")).toBeVisible();
    await user.type(title, " revised");
    expect(screen.getByRole("status")).toHaveTextContent("Ready to edit.");
  });

  it("blocks saving while a schematic uploads and shows the attachment when it finishes", async () => {
    const user = userEvent.setup();
    render(DraftTrial);
    const save = screen.getByRole("button", { name: "Save draft" });
    await user.upload(
      screen.getByLabelText("Attach a schematic"),
      new File(["fixture"], "door.schem"),
    );
    expect(screen.getByRole("progressbar")).toBeVisible();
    expect(save).toBeDisabled();
    expect(await screen.findByText("Attached: door.schem")).toBeVisible();
    expect(save).toBeEnabled();
  });
  it("retains edits across a failed save and retries without requiring a hidden field", async () => {
    const user = userEvent.setup();
    render(DraftTrial, { scenario: "retry" });
    await user.type(screen.getByLabelText("Build title"), "Utility build");
    await user.selectOptions(screen.getByLabelText("Category"), "utility");
    expect(screen.queryByLabelText("Opening width")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Minecraft version"), "Java 1.21.4");
    await user.click(screen.getByRole("button", { name: "Save draft" }));
    await user.click(await screen.findByRole("button", { name: "Retry save" }));
    expect(await screen.findByText("Draft saved.")).toBeVisible();
    expect(screen.getByLabelText("Build title")).toHaveValue("Utility build");
  });
  it("retries the selected file after an upload failure", async () => {
    const user = userEvent.setup();
    render(DraftTrial, { scenario: "upload-retry" });
    await user.upload(
      screen.getByLabelText("Attach a schematic"),
      new File(["fixture"], "retry.schem"),
    );
    await user.click(await screen.findByRole("button", { name: "Retry upload" }));
    expect(await screen.findByText("Attached: retry.schem")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("cancels an upload and disposes pending work on unmount", async () => {
    const user = userEvent.setup();
    const view = render(DraftTrial);
    const upload = screen.getByLabelText("Attach a schematic");
    await user.upload(upload, new File(["fixture"], "first.schem"));
    await user.click(screen.getByRole("button", { name: "Cancel operation" }));
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByText("Attached: first.schem")).not.toBeInTheDocument();
    await user.upload(upload, new File(["fixture"], "second.schem"));
    const abort = vi.spyOn(AbortController.prototype, "abort");
    try {
      view.unmount();
      expect(abort).toHaveBeenCalled();
    } finally {
      abort.mockRestore();
    }
  });
});
