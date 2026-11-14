import { render as renderReact, screen, cleanup as cleanupReact } from "@testing-library/react";
import { render as renderSvelte, cleanup as cleanupSvelte } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { DraftTrial } from "../../src/features/workbench/DraftTrial";
import SvelteDraftTrial from "../../src/features/workbench/DraftTrial.svelte";

afterEach(() => {
  cleanupReact();
  cleanupSvelte();
});
for (const framework of ["react", "svelte"] as const) {
  const mount = (retryFirstSave = false) =>
    framework === "react"
      ? renderReact(<DraftTrial locale="en" retryFirstSave={retryFirstSave} />)
      : renderSvelte(SvelteDraftTrial, { locale: "en", retryFirstSave });
  describe(`${framework} draft trial`, () => {
    it("validates conditional fields, filters versions, uploads and recovers from server validation", async () => {
      const user = userEvent.setup();
      mount();
      await user.click(screen.getByRole("button", { name: "Save draft" }));
      expect(screen.getByLabelText("Build title")).toHaveAttribute("aria-invalid", "true");
      await user.type(screen.getByLabelText("Build title"), "Reserved");
      await user.type(screen.getByLabelText("Opening width"), "1.5");
      await user.type(screen.getByLabelText("Find a version"), "1.21.4");
      expect(screen.queryByRole("option", { name: "Bedrock 1.21.50" })).not.toBeInTheDocument();
      await user.selectOptions(screen.getByLabelText("Minecraft version"), "Java 1.21.4");
      await user.click(screen.getByRole("button", { name: "Save draft" }));
      expect(screen.getByLabelText("Opening width")).toHaveAccessibleDescription(
        "Enter a positive whole number.",
      );
      await user.clear(screen.getByLabelText("Opening width"));
      await user.type(screen.getByLabelText("Opening width"), "5");
      await user.click(screen.getByRole("button", { name: "Save draft" }));
      expect(await screen.findByText("This title is already taken.")).toBeVisible();
      await user.clear(screen.getByLabelText("Build title"));
      await user.type(screen.getByLabelText("Build title"), "Copper Bolt");
      await user.upload(
        screen.getByLabelText("Attach a schematic"),
        new File(["fixture"], "door.schem"),
      );
      expect(screen.getByRole("progressbar")).toBeVisible();
      expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
      expect(await screen.findByText("Attached: door.schem")).toBeVisible();
      await user.click(screen.getByRole("button", { name: "Save draft" }));
      expect(await screen.findByText("Draft saved.")).toBeVisible();
      await user.type(screen.getByLabelText("Build title"), " revised");
      expect(screen.getByRole("status")).toHaveTextContent("Ready to edit.");
    });
    it("retains edits across a failed save and retries without requiring a hidden field", async () => {
      const user = userEvent.setup();
      mount(true);
      await user.type(screen.getByLabelText("Build title"), "Utility build");
      await user.selectOptions(screen.getByLabelText("Category"), "utility");
      expect(screen.queryByLabelText("Opening width")).not.toBeInTheDocument();
      await user.selectOptions(screen.getByLabelText("Minecraft version"), "Java 1.21.4");
      await user.click(screen.getByRole("button", { name: "Save draft" }));
      await user.click(await screen.findByRole("button", { name: "Retry save" }));
      expect(await screen.findByText("Draft saved.")).toBeVisible();
      expect(screen.getByLabelText("Build title")).toHaveValue("Utility build");
    });
    it("cancels an upload and disposes pending work on unmount", async () => {
      const user = userEvent.setup();
      const view = mount();
      const upload = screen.getByLabelText("Attach a schematic");
      await user.upload(upload, new File(["fixture"], "first.schem"));
      await user.click(screen.getByRole("button", { name: "Cancel operation" }));
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
      expect(screen.queryByText("Attached: first.schem")).not.toBeInTheDocument();
      await user.upload(upload, new File(["fixture"], "second.schem"));
      view.unmount();
    });
  });
}
