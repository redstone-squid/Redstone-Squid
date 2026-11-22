import { render, screen, cleanup, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import FormSpecimen from "../../src/workbench/FormSpecimen.svelte";
import Field from "../../src/ui/svelte/Field.svelte";
import Harness from "./fixtures/SvelteDialogHarness.svelte";

afterEach(cleanup);
describe("Svelte primitive contracts", () => {
  it("merges field classes and descriptions while preserving caller invalid semantics", () => {
    const help = document.createElement("p");
    help.id = "external-help";
    help.textContent = "External explanation";
    document.body.append(help);
    try {
      render(Field, {
        label: "First",
        class: "wide",
        "aria-describedby": "external-help",
        "aria-invalid": "grammar",
        error: "Check spelling",
      });
      render(Field, { label: "Second", hint: "Another name" });
      const first = screen.getByRole("textbox", { name: "First" });
      const second = screen.getByRole("textbox", { name: "Second" });
      expect(first).toHaveClass("sq-input", "wide");
      expect(first).toHaveAccessibleDescription("External explanation Check spelling");
      expect(first).toHaveAttribute("aria-invalid", "grammar");
      expect(first.id).not.toBe(second.id);
    } finally {
      help.remove();
    }
  });
  it("exposes invalid and disabled states in the workbench form", async () => {
    const { rerender } = render(FormSpecimen, { locale: "en", state: "invalid" });
    const category = screen.getByRole("combobox", { name: "Category" });
    expect(category).toHaveValue("");
    expect(category).toHaveAccessibleDescription("This field is required.");
    expect(screen.getByRole("radio", { name: "Java" })).toBeChecked();

    await rerender({ state: "disabled" });
    expect(category).toHaveValue("door");
    expect(category).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Java" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Builder notes" })).toBeDisabled();
  });
  it("composes a secondary trigger and closes through controlled state", async () => {
    const user = userEvent.setup();
    render(Harness);
    const trigger = screen.getByRole("button", { name: "Edit draft" });
    expect(trigger).toHaveClass("sq-button--secondary");
    await user.click(trigger);
    expect(await screen.findByRole("dialog")).toHaveClass("wide-dialog");
    await user.click(screen.getByRole("button", { name: "Finish" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
