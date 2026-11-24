import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { createRawSnippet } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChoiceGroup from "@/ui/forms/ChoiceGroup.svelte";
import CodeEntry from "@/ui/forms/CodeEntry.svelte";
import Combobox from "@/ui/forms/Combobox.svelte";
import ErrorSummary from "@/ui/forms/ErrorSummary.svelte";
import FileDrop from "@/ui/forms/FileDrop.svelte";
import FormActions from "@/ui/forms/FormActions.svelte";
import RangeField from "@/ui/forms/RangeField.svelte";
import SaveStatus from "@/ui/feedback/SaveStatus.svelte";
import SelectField from "@/ui/forms/SelectField.svelte";
import Switch from "@/ui/forms/Switch.svelte";
import TextareaField from "@/ui/forms/TextareaField.svelte";
import TextField from "@/ui/forms/TextField.svelte";
import UnitField from "@/ui/forms/UnitField.svelte";
import ListHarness from "./fixtures/ListFieldHarness.svelte";

afterEach(() => {
  vi.useRealTimers();
});

const snippet = (html: string) => createRawSnippet(() => ({ render: () => html }));

describe("text fields", () => {
  it("shows errors as described text, not alerts", () => {
    render(TextField, { label: "Title", error: "Enter a title.", hint: "Shown on the card." });
    const input = screen.getByRole("textbox", { name: "Title" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Shown on the card. Enter a title.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps a hidden label as the name and describes the input with its unit", () => {
    render(TextField, { label: "Width", labelVisibility: "hidden", suffix: "blocks" });
    expect(screen.getByRole("textbox", { name: "Width" })).toHaveAccessibleDescription("blocks");
  });

  it("clears through its button, fires input, and returns focus", async () => {
    const user = userEvent.setup();
    const oninput = vi.fn();
    render(TextField, {
      label: "Search",
      value: "door",
      clearable: true,
      clearLabel: "Clear search",
      oninput,
    });
    await user.click(screen.getByRole("button", { name: "Clear search" }));
    const input = screen.getByRole("textbox", { name: "Search" });
    expect(input).toHaveValue("");
    expect(input).toHaveFocus();
    expect(oninput).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Clear search" })).not.toBeInTheDocument();
  });

  it("announces the count only near the limit", async () => {
    const user = userEvent.setup();
    render(TextareaField, { label: "Notes", maxlength: 10, counter: true });
    const status = screen.getByRole("status");
    await user.type(screen.getByRole("textbox", { name: "Notes" }), "12345678");
    expect(status).toHaveTextContent(/^$/);
    await user.keyboard("9");
    expect(status).toHaveTextContent("9/10");
  });

  it("renders select options after a placeholder", () => {
    render(SelectField, {
      label: "Category",
      placeholder: "Choose",
      options: [{ value: "door", label: "Door" }],
    });
    const select = screen.getByRole("combobox", { name: "Category" });
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Choose", "Door"]);
  });
});

describe("Switch", () => {
  it("toggles, and ignores clicks while pending", async () => {
    const user = userEvent.setup();
    const { rerender } = render(Switch, { label: "Hide profile" });
    const control = screen.getByRole("switch", { name: "Hide profile" });
    await user.click(control);
    expect(control).toBeChecked();
    await rerender({ label: "Hide profile", pending: true });
    await user.click(control);
    expect(control).toBeChecked();
    expect(control).toHaveAttribute("aria-busy", "true");
  });
});

describe("ChoiceGroup", () => {
  const options = [
    { value: "flush", label: "Flush", description: "No visible gaps." },
    { value: "silent", label: "Silent" },
    { value: "seamless", label: "Seamless" },
  ];

  it("names the group and describes each option", () => {
    render(ChoiceGroup, { label: "Edition", name: "edition", options, error: "Choose one." });
    const group = screen.getByRole("group", { name: "Edition" });
    expect(group).toHaveAccessibleDescription("Choose one.");
    expect(within(group).getByRole("radio", { name: "Flush" })).toHaveAccessibleDescription(
      "No visible gaps.",
    );
  });

  it("keeps checkbox values in option order", async () => {
    const user = userEvent.setup();
    const onchange = vi.fn();
    render(ChoiceGroup, { type: "checkbox", label: "Features", name: "f", options, onchange });
    await user.click(screen.getByRole("checkbox", { name: "Seamless" }));
    await user.click(screen.getByRole("checkbox", { name: "Flush" }));
    expect(onchange).toHaveBeenLastCalledWith(["flush", "seamless"]);
    await user.click(screen.getByRole("checkbox", { name: "Flush" }));
    expect(onchange).toHaveBeenLastCalledWith(["seamless"]);
  });
});

describe("RangeField", () => {
  it("labels both bounds inside one named group", () => {
    render(RangeField, {
      label: "Width",
      separator: "to",
      min: { name: "min", value: "3", label: "Minimum width" },
      max: { name: "max", value: "", label: "Maximum width" },
    });
    const group = screen.getByRole("group", { name: "Width" });
    expect(within(group).getByRole("textbox", { name: "Minimum width" })).toHaveValue("3");
    expect(within(group).getByRole("textbox", { name: "Maximum width" })).toHaveValue("");
  });
});

describe("UnitField", () => {
  const units = [
    { id: "gt", label: "gt", factor: 1 },
    { id: "s", label: "s", factor: 20 },
  ];

  it("keeps the canonical value and shows conversions as the unit changes", async () => {
    const user = userEvent.setup();
    render(UnitField, {
      label: "Delay",
      name: "delay",
      unitName: "unit",
      units,
      labels: { unit: "Unit" },
      value: 25,
    });
    const input = screen.getByRole("textbox", { name: "Delay" });
    expect(input).toHaveAccessibleDescription("= 1.25 s");
    await user.selectOptions(screen.getByRole("combobox", { name: "Unit" }), "s");
    expect(input).toHaveValue("1.25");
    expect(input).toHaveAccessibleDescription("= 25 gt");
    await user.clear(input);
    await user.type(input, "0,5");
    expect(input).toHaveAccessibleDescription("= 10 gt");
  });
});

describe("CodeEntry", () => {
  it("groups a pasted code, strips separators and submits when complete", async () => {
    const user = userEvent.setup();
    const form = document.createElement("form");
    const submit = vi.fn((event: Event) => event.preventDefault());
    form.addEventListener("submit", submit);
    document.body.append(form);
    try {
      render(CodeEntry, {
        target: form,
        props: { label: "Code", name: "code", length: 8, groups: [4, 4], autoSubmit: true },
      });
      const input = screen.getByRole("textbox", { name: "Code" });
      expect(input).toHaveAttribute("autocomplete", "one-time-code");
      await user.click(input);
      await user.paste("ab cd-ef");
      expect(input).toHaveValue("ABCD-EF");
      expect(submit).not.toHaveBeenCalled();
      await user.paste("g!h9");
      expect(input).toHaveValue("ABCD-EFGH");
      await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    } finally {
      form.remove();
    }
  });
});

describe("ErrorSummary", () => {
  it("focuses the first invalid field and lets links focus later fields", async () => {
    const user = userEvent.setup();
    const field = document.createElement("input");
    field.id = "title";
    const width = document.createElement("input");
    width.id = "width";
    document.body.append(field, width);
    try {
      render(ErrorSummary, {
        title: "Fix 3 problems",
        errors: [
          { fieldId: "title", message: "Enter a title." },
          { fieldId: "title", message: "Use a descriptive title." },
          { fieldId: "width", message: "Enter a width." },
        ],
      });
      const summary = screen.getByRole("alert", { name: "Fix 3 problems" });
      expect(field).toHaveFocus();
      expect(within(summary).getAllByRole("link")).toHaveLength(3);
      await user.click(within(summary).getByRole("link", { name: "Enter a width." }));
      expect(width).toHaveFocus();
    } finally {
      field.remove();
      width.remove();
    }
  });

  it("renders nothing without errors", () => {
    render(ErrorSummary, { title: "Problems", errors: [] });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("FormActions", () => {
  it("puts the primary action last in reading order on wide screens", () => {
    render(FormActions, {
      note: "Publishing is final",
      primary: snippet("<button>Publish</button>"),
      secondary: snippet("<button>Save draft</button>"),
      destructive: snippet("<button>Discard</button>"),
    });
    expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Discard",
      "Save draft",
      "Publish",
    ]);
    expect(screen.getByText("Publishing is final")).toBeInTheDocument();
  });
});

describe("SaveStatus", () => {
  const labels = {
    idle: "Idle",
    dirty: "Unsaved",
    saving: "Saving",
    saved: "Saved",
    offline: "Offline",
    error: "Failed",
    conflict: "Conflict",
    retry: "Retry",
  };

  it("announces arrivals at saved or error at most every 5 seconds", async () => {
    vi.useFakeTimers();
    const onRetry = vi.fn();
    const { rerender } = render(SaveStatus, { state: "saving", labels, onRetry });
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/^$/);
    await rerender({ state: "saved", labels, onRetry });
    expect(status).toHaveTextContent("Saved");
    await rerender({ state: "error", labels, onRetry });
    expect(status).toHaveTextContent("Saved");
    await vi.advanceTimersByTimeAsync(5000);
    expect(status).toHaveTextContent("Failed");
    await fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe("FileDrop", () => {
  const labels = { drop: "Drop here", browse: "Browse" };
  const schematic = new File(["x"], "door.litematic");
  const image = new File(["x"], "door.png", { type: "image/png" });

  it("passes chosen files and lets the same file be chosen again", async () => {
    const user = userEvent.setup();
    const onFiles = vi.fn();
    render(FileDrop, { label: "Schematic", labels, onFiles });
    const input = screen.getByLabelText<HTMLInputElement>("Schematic");
    await user.upload(input, schematic);
    expect(onFiles).toHaveBeenCalledWith([schematic], []);
    expect(input.value).toBe("");
  });

  it("filters dropped files by type and size", async () => {
    const onFiles = vi.fn();
    render(FileDrop, {
      label: "Schematics",
      labels,
      accept: ".litematic",
      multiple: true,
      maxBytes: 10,
      onFiles,
    });
    const zone = screen.getByText("Drop here");
    await fireEvent.drop(zone, { dataTransfer: { files: [schematic, image] } });
    expect(onFiles).toHaveBeenCalledWith([schematic], [image]);
  });
});

describe("Combobox", () => {
  const labels = {
    toggle: "Show options",
    noResults: "No matches",
    loading: "Loading",
    error: "Unavailable",
    remove: (label: string) => `Remove ${label}`,
    create: (text: string) => `Add ${text}`,
  };
  const options = ["1.20", "1.21", "1.21.4"].map((value) => ({ value, label: value }));

  it("filters options by typing and chooses with the keyboard", async () => {
    const user = userEvent.setup();
    const onchange = vi.fn();
    render(Combobox, { label: "Version", name: "version", options, labels, onchange });
    const input = screen.getByRole("combobox", { name: "Version" });
    await user.type(input, "21.");
    expect(await screen.findAllByRole("option", { hidden: true })).toHaveLength(1);
    await user.keyboard("{ArrowDown}{Enter}");
    expect(onchange).toHaveBeenLastCalledWith("1.21.4");
    await waitFor(() => expect(input).toHaveValue("1.21.4"));
  });

  it("shows chosen values as removable chips and offers to add new ones", async () => {
    const user = userEvent.setup();
    const onchange = vi.fn();
    render(Combobox, {
      label: "Tags",
      name: "tags",
      options: [{ value: "flush", label: "Flush" }],
      value: ["flush"],
      multiple: true,
      allowCreate: true,
      labels,
      onchange,
    });
    const input = screen.getByRole("combobox", { name: "Tags" });
    await user.type(input, "tiny");
    await user.click(await screen.findByText("Add tiny"));
    expect(onchange).toHaveBeenLastCalledWith(["flush", "tiny"]);
    expect(await screen.findByRole("button", { name: "Remove tiny" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove Flush" }));
    expect(onchange).toHaveBeenLastCalledWith(["tiny"]);
    await user.click(input);
    await user.keyboard("{Backspace}");
    expect(onchange).toHaveBeenLastCalledWith([]);
  });

  it("loads options from the server and aborts stale requests", async () => {
    const user = userEvent.setup();
    const signals: AbortSignal[] = [];
    const load = vi.fn((query: string, signal: AbortSignal) => {
      signals.push(signal);
      return Promise.resolve([{ value: "b1", label: `Copper ${query}` }]);
    });
    render(Combobox, { label: "Build", name: "build", load, labels, debounceMs: 0 });
    const input = screen.getByRole("combobox", { name: "Build" });
    await user.type(input, "ab");
    expect(await screen.findByText("Copper ab")).toBeInTheDocument();
    expect(signals.slice(0, -1).every((signal) => signal.aborted)).toBe(true);
  });
});

describe("ListField", () => {
  it("moves focus to a new row, and after removal to the next row or the add button", async () => {
    const user = userEvent.setup();
    render(ListHarness);
    await user.click(screen.getByRole("button", { name: "Add link" }));
    const second = screen.getByRole("group", { name: "Link 2" });
    expect(within(second).getByRole("textbox")).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Remove Link 1" }));
    expect(screen.getByRole("status")).toHaveTextContent("Link 1 removed");
    expect(
      within(screen.getByRole("group", { name: "Link 1" })).getByRole("textbox"),
    ).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Remove Link 1" }));
    expect(screen.getByRole("button", { name: "Add link" })).toHaveFocus();
  });

  it("reorders rows and keeps focus on the move control", async () => {
    const user = userEvent.setup();
    render(ListHarness, { initial: ["a.example", "b.example"] });
    await user.click(screen.getByRole("button", { name: "Move Link 1 down" }));
    const textboxes = screen.getAllByRole("textbox");
    expect(textboxes.map((box) => (box as HTMLInputElement).value)).toEqual([
      "b.example",
      "a.example",
    ]);
    expect(screen.getByRole("button", { name: "Move Link 2 up" })).toHaveFocus();
  });
});
