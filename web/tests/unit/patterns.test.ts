import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { createRawSnippet } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import ConfirmDialog from "@/ui/patterns/ConfirmDialog.svelte";
import FilterPanel from "@/ui/patterns/FilterPanel.svelte";
import FlashNotice from "@/ui/patterns/FlashNotice.svelte";
import NavBar from "@/ui/patterns/NavBar.svelte";
import OneTimeSecret from "@/ui/patterns/OneTimeSecret.svelte";
import ResultsView from "@/ui/patterns/ResultsView.svelte";
import StepFlow from "@/ui/patterns/StepFlow.svelte";
import MediaHarness from "./fixtures/MediaHarness.svelte";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const html = (markup: string) => createRawSnippet(() => ({ render: () => markup }));

describe("NavBar", () => {
  it("collapses its links behind a button that controls the same list", () => {
    render(NavBar, {
      label: "Main navigation",
      menuLabel: "Menu",
      home: { label: "Home", href: "/" },
      links: [
        { label: "Builds", href: "/builds", current: true },
        { label: "Records", href: "/records" },
      ],
    });
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    const list = within(nav).getByRole("list", { hidden: true });
    expect(within(nav).getByRole("button", { name: "Menu" })).toHaveAttribute(
      "popovertarget",
      list.id,
    );
    expect(within(list).getByRole("link", { name: "Builds", hidden: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});

describe("FilterPanel", () => {
  it("auto-applies after the last change with empty fields left out", async () => {
    vi.useFakeTimers();
    const assign = vi.fn();
    vi.stubGlobal("location", { assign });
    render(FilterPanel, {
      action: "/builds",
      title: "Filters",
      applyLabel: "Apply",
      resetHref: "/builds",
      resetLabel: "Reset",
      hidden: { q: "door", view: "" },
      autoApply: true,
      children: html(
        "<select name='category'><option value=''>Any</option><option value='door'>Door</option></select>",
      ),
    });
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
    const select = screen.getByRole("combobox");
    await fireEvent.change(select, { target: { value: "door" } });
    vi.advanceTimersByTime(599);
    expect(assign).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(assign).toHaveBeenCalledWith("/builds?q=door&category=door");
  });
});

describe("ResultsView", () => {
  it("shows skeletons only after 200 ms and marks the region busy", () => {
    vi.useFakeTimers();
    const { container } = render(ResultsView, {
      view: "cards",
      status: "loading",
      busyLabel: "Loading builds",
    });
    const region = screen.getByRole("region", { name: "Loading builds" });
    expect(region).toHaveAttribute("aria-busy", "true");
    expect(container.querySelector(".animate-pulse")).toBeNull();
    vi.advanceTimersByTime(200);
    return waitFor(() => expect(container.querySelector(".animate-pulse")).not.toBeNull());
  });
});

describe("ConfirmDialog", () => {
  const base = {
    title: "Revoke Laptop?",
    confirmLabel: "Revoke",
    cancelLabel: "Cancel",
    closeLabel: "Close",
    trigger: { label: "Revoke device" },
  };

  it("starts on Cancel for danger, and stays open while confirming and after a failure", async () => {
    const user = userEvent.setup();
    let fail: ((reason: Error) => void) | undefined;
    const onConfirm = vi.fn(() => new Promise<void>((_, reject) => (fail = reject)));
    const { rerender } = render(ConfirmDialog, { ...base, onConfirm });
    await user.click(screen.getByRole("button", { name: "Revoke device" }));
    const dialog = await screen.findByRole("dialog", { name: "Revoke Laptop?" });
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus(),
    );

    await user.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fail?.(new Error("conflict"));
    await rerender({ error: "It changed. Reload and try again." });
    expect(await screen.findByRole("alert")).toHaveTextContent("It changed.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("checks typed confirmation, then closes and focuses afterConfirmFocus", async () => {
    const user = userEvent.setup();
    const target = document.createElement("button");
    document.body.append(target);
    const onConfirm = vi.fn(() => Promise.resolve());
    render(ConfirmDialog, {
      ...base,
      requireText: { label: "Type Laptop", expected: "Laptop", error: "Type the name exactly." },
      afterConfirmFocus: () => target,
      onConfirm,
    });
    await user.click(screen.getByRole("button", { name: "Revoke device" }));
    const field = await screen.findByRole("textbox", { name: "Type Laptop" });
    await waitFor(() => expect(field).toHaveFocus());
    await user.click(screen.getByRole("button", { name: "Revoke" }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(field).toHaveAccessibleDescription("Type the name exactly.");

    await user.type(field, "Laptop{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(target).toHaveFocus();
    target.remove();
  });
});

describe("MediaUploader", () => {
  it("moves focus to Undo after removing and back to Remove after undoing", async () => {
    const user = userEvent.setup();
    render(MediaHarness);
    await user.click(screen.getByRole("button", { name: "Remove front.png" }));
    const undo = screen.getByRole("button", { name: "Undo" });
    expect(undo).toHaveFocus();
    expect(undo).toHaveAccessibleDescription("front.png");
    await user.click(undo);
    expect(screen.getByRole("button", { name: "Remove front.png" })).toHaveFocus();
  });

  it("announces one summary a second after files finish", async () => {
    vi.useFakeTimers();
    render(MediaHarness);
    await fireEvent.click(screen.getByRole("button", { name: "Finish side" }));
    const status = screen.getAllByRole("status").at(-1);
    expect(status).toHaveTextContent(/^$/);
    vi.advanceTimersByTime(1000);
    await waitFor(() => expect(status).toHaveTextContent("2 ready, 0 failed"));
  });
});

describe("OneTimeSecret", () => {
  it("asks before leaving until the secret is acknowledged", async () => {
    const user = userEvent.setup();
    render(OneTimeSecret, {
      value: "secret",
      label: "Secret",
      warning: "Save it now.",
      copyLabels: { label: "Copy", copied: "Copied", failed: "Failed" },
      acknowledgeLabel: "I saved it",
    });
    const leave = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    expect(leave()).toBe(true);
    await user.click(screen.getByRole("button", { name: "I saved it" }));
    expect(leave()).toBe(false);
  });
});

describe("StepFlow", () => {
  it("focuses the step heading when the step changes, not on first render", async () => {
    const props = {
      label: "Merge",
      labels: { pending: "Not started", current: "Current", done: "Done", error: "Failed" },
      steps: [
        { id: "code", label: "Enter the code", status: "done" as const },
        { id: "review", label: "Review", status: "pending" as const },
      ],
    };
    const { rerender } = render(StepFlow, { ...props, current: "code" });
    expect(screen.getByRole("heading", { name: "Enter the code" })).not.toHaveFocus();
    await rerender({ ...props, current: "review" });
    const heading = screen.getByRole("heading", { name: "Review" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent("Review");
  });
});

describe("FlashNotice", () => {
  it("repeats its message into an empty status region once hydrated", async () => {
    vi.useFakeTimers();
    render(FlashNotice, { message: "Profile saved." });
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/^$/);
    vi.advanceTimersByTime(100);
    await waitFor(() => expect(status).toHaveTextContent("Profile saved."));
  });
});
