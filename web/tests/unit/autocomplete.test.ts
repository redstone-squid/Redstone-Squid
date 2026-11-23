import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Autocomplete from "@/ui/forms/Autocomplete.svelte";
import type { Suggestions } from "@/ui/forms/Autocomplete.svelte";

afterEach(() => {
  vi.useRealTimers();
});

const versions = ["Java 1.20", "Java 1.21", "Java 1.21.4"];

function setup(
  load: (value: string, cursor: number | null, signal: AbortSignal) => Promise<Suggestions> = (
    value,
  ) =>
    Promise.resolve({
      items: versions
        .filter((version) => version.toLowerCase().includes(value.toLowerCase()))
        .map((version) => ({ value: version, label: version })),
    }),
  props: Partial<{ multiline: boolean; minLength: number }> = {},
) {
  const user = userEvent.setup();
  render(Autocomplete, {
    name: "version",
    label: "Version",
    suggestionsLabel: "Suggestions",
    errorLabel: "Suggestions are unavailable.",
    load,
    ...props,
  });
  return { user, input: screen.getByRole("combobox", { name: "Version" }) };
}

describe("Autocomplete", () => {
  it("waits for the minimum length before loading", async () => {
    vi.useFakeTimers();
    const load = vi.fn(() => Promise.resolve({ items: [] }));
    const { input } = setup(load);
    await fireEvent.focus(input);
    await fireEvent.input(input, { target: { value: "J" } });
    await vi.runOnlyPendingTimersAsync();
    expect(load).not.toHaveBeenCalled();
    await fireEvent.input(input, { target: { value: "Ja" } });
    await vi.runOnlyPendingTimersAsync();
    expect(load).toHaveBeenCalledWith("Ja", 2, expect.any(AbortSignal));
  });

  it("moves through options with the arrow keys, wraps, and chooses with Enter", async () => {
    const { user, input } = setup();
    await user.type(input, "java");
    expect(await screen.findAllByRole("option")).toHaveLength(3);
    expect(input).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("option", { name: "Java 1.21.4" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(input.getAttribute("aria-activedescendant")).toBe(
      screen.getByRole("option", { name: "Java 1.21" }).id,
    );
    await user.keyboard("{Enter}");
    expect(input).toHaveValue("Java 1.21");
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(input).toHaveFocus();
  });

  it("chooses an option by pointer and dismisses the list with Escape", async () => {
    const { user, input } = setup();
    await user.type(input, "1.2");
    await user.click(await screen.findByRole("option", { name: "Java 1.20" }));
    expect(input).toHaveValue("Java 1.20");
    await user.type(input, "{Backspace}");
    await screen.findAllByRole("option");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("replaces only the span the loader names", async () => {
    const { user } = setup(
      (value) =>
        Promise.resolve({
          items: [{ value: "creator:", label: "creator:", description: "Filter by creator" }],
          replacement: { start: value.lastIndexOf(" ") + 1, end: value.length },
        }),
      { multiline: true },
    );
    const input = screen.getByRole("combobox", { name: "Version" });
    expect(input.tagName).toBe("TEXTAREA");
    await user.type(input, "door cre");
    expect(await screen.findByText("Filter by creator")).toBeInTheDocument();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(input).toHaveValue("door creator:");
  });

  it("ignores a replacement span outside the current value", async () => {
    const { user, input } = setup(() =>
      Promise.resolve({
        items: [{ value: "Java 1.21", label: "Java 1.21" }],
        replacement: { start: 0, end: 99 },
      }),
    );
    await user.type(input, "jav");
    await user.click(await screen.findByRole("option"));
    expect(input).toHaveValue("Java 1.21");
  });

  it("reports a failed load and aborts the request it replaces", async () => {
    const signals: AbortSignal[] = [];
    const { user, input } = setup((_value, _cursor, signal) => {
      signals.push(signal);
      return Promise.reject(new Error("offline"));
    });
    await user.type(input, "java");
    expect(await screen.findByRole("status")).toHaveTextContent("Suggestions are unavailable.");
    await user.type(input, " 1");
    await waitFor(() => expect(signals).toHaveLength(2));
    expect(signals[0]?.aborted).toBe(true);
  });

  it("waits for IME composition to finish before loading", async () => {
    vi.useFakeTimers();
    const load = vi.fn(() => Promise.resolve({ items: [] }));
    const { input } = setup(load);
    await fireEvent.focus(input);
    await fireEvent.compositionStart(input);
    await fireEvent.input(input, { target: { value: "红石" } });
    await vi.runOnlyPendingTimersAsync();
    expect(load).not.toHaveBeenCalled();
    await fireEvent.compositionEnd(input, { target: { value: "红石" } });
    await vi.runOnlyPendingTimersAsync();
    expect(load).toHaveBeenCalledWith("红石", 2, expect.any(AbortSignal));
  });
});
