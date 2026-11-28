import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { createRawSnippet } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import CopyButton from "@/ui/actions/CopyButton.svelte";
import Announcer from "@/ui/feedback/Announcer.svelte";
import { announce } from "@/ui/feedback/announcer";
import SegmentedControl from "@/ui/navigation/SegmentedControl.svelte";
import Tabs from "@/ui/navigation/Tabs.svelte";
import Menu from "@/ui/overlays/Menu.svelte";
import Popover from "@/ui/overlays/Popover.svelte";
import DialogHarness from "./fixtures/DialogHarness.svelte";
import TooltipHarness from "./fixtures/TooltipHarness.svelte";

afterEach(() => {
  vi.useRealTimers();
});

const text = (value: string) => createRawSnippet(() => ({ render: () => `<p>${value}</p>` }));

describe("Dialog and Sheet", () => {
  it.each([false, true])(
    "focuses the first field, traps Tab, and returns focus on Escape (sheet: %s)",
    async (sheet) => {
      const user = userEvent.setup();
      render(DialogHarness, { sheet });
      const trigger = screen.getByRole("button", { name: "Rename" });
      await user.click(trigger);
      const dialog = await screen.findByRole("dialog", { name: "Rename draft" });
      expect(dialog).toHaveAccessibleDescription("Pick a short title");
      const field = within(dialog).getByRole("textbox", { name: "Title" });
      await waitFor(() => expect(field).toHaveFocus());

      for (let step = 0; step < 4; step++) await user.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);

      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(trigger).toHaveFocus();
    },
  );

  it("focuses initialFocus, and the dialog itself when there is no field", async () => {
    const user = userEvent.setup();
    const { unmount } = render(DialogHarness, { initialFocus: "[data-done]" });
    await user.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Done" })).toHaveFocus());
    unmount();

    render(DialogHarness, { withField: false });
    await user.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveFocus());
  });

  it("ignores Escape and the close button while not dismissible", async () => {
    const user = userEvent.setup();
    render(DialogHarness, { dismissible: false });
    await user.click(screen.getByRole("button", { name: "Rename" }));
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("returns focus to returnFocus when given", async () => {
    const user = userEvent.setup();
    render(DialogHarness, { returnFocus: () => document.querySelector<HTMLElement>("#elsewhere") });
    await user.click(screen.getByRole("button", { name: "Rename" }));
    await user.click(await screen.findByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Elsewhere" })).toHaveFocus());
  });
});

describe("Menu", () => {
  it("moves through items with arrow keys, selects with Enter, and returns focus", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(Menu, {
      label: "Build actions",
      trigger: { label: "Actions" },
      items: [
        { label: "Edit", onSelect },
        { kind: "separator" },
        { label: "Open", href: "/builds/1" },
        { label: "Archive", action: "/builds/1/archive", danger: true },
      ],
    });
    const trigger = screen.getByRole("button", { name: "Actions" });
    trigger.focus();
    await user.keyboard("{Enter}");
    const menu = await screen.findByRole("menu", { hidden: true });
    expect(menu).toHaveAttribute("aria-label", "Build actions");
    expect(
      within(menu)
        .getAllByRole("menuitem", { hidden: true })
        .map((item) => item.textContent.trim()),
    ).toEqual(["Edit", "Open", "Archive"]);
    expect(within(menu).getByText("Open").closest("a")).toHaveAttribute("href", "/builds/1");
    await waitFor(() =>
      expect(within(menu).getByText("Edit").closest("[role=menuitem]")).toHaveFocus(),
    );
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledOnce();
    await waitFor(() =>
      expect(screen.queryByRole("menu", { hidden: true })).not.toBeInTheDocument(),
    );
    expect(trigger).toHaveFocus();

    await user.keyboard("{Enter}");
    await screen.findByRole("menu", { hidden: true });
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("menu", { hidden: true })).not.toBeInTheDocument(),
    );
    expect(trigger).toHaveFocus();
  });

  it("toggles checkbox items without closing", async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(Menu, {
      label: "Columns",
      trigger: { label: "Columns" },
      items: [{ kind: "checkbox", label: "Creator", checked: true, onCheckedChange }],
    });
    await user.click(screen.getByRole("button", { name: "Columns" }));
    const item = await screen.findByRole("menuitemcheckbox", { hidden: true });
    expect(item).toHaveAttribute("aria-checked", "true");
    await user.click(item);
    expect(onCheckedChange).toHaveBeenCalledWith(false);
    expect(screen.getByRole("menu", { hidden: true })).toBeInTheDocument();
  });

  it("renders a native popover of links and forms with nojs", () => {
    render(Menu, {
      nojs: true,
      label: "Language",
      trigger: { label: "English", name: "English · Language", lang: "en" },
      items: [
        { label: "English", href: "/", lang: "en", current: true },
        { label: "简体中文", description: "Simplified Chinese", href: "/zh-cn", lang: "zh-CN" },
        { kind: "separator" },
        { label: "Sign out", action: "/session/delete" },
      ],
    });
    const trigger = screen.getByRole("button", { name: "English · Language" });
    // The popover follows its button, so browsers add no implicit aria-details.
    const list = trigger.nextElementSibling as HTMLElement;
    expect(trigger).toHaveAttribute("popovertarget", list.id);
    expect(list).toHaveAttribute("aria-label", "Language");
    expect(list).toHaveAttribute("popover", "auto");
    const link = within(list).getByText("English").closest("a");
    expect(link).toHaveAttribute("aria-current", "true");
    expect(link).toHaveAttribute("hreflang", "en");
    // Only the language's own name is in that language; its description is in the page's.
    expect(within(list).getByText("简体中文")).toHaveAttribute("lang", "zh-CN");
    expect(within(list).getByText("Simplified Chinese")).not.toHaveAttribute("lang");
    const signOut = within(list).getByText("Sign out");
    expect(signOut.closest("form")).toHaveAttribute("action", "/session/delete");
  });
});

describe("Popover", () => {
  it("opens a named panel and returns focus to the trigger when closed", async () => {
    const user = userEvent.setup();
    render(Popover, {
      label: "What pending means",
      closeLabel: "Close",
      trigger: { label: "Help" },
      children: text("A moderator checks the evidence."),
    });
    const trigger = screen.getByRole("button", { name: "Help" });
    await user.click(trigger);
    const panel = await screen.findByRole("dialog", { hidden: true });
    expect(panel).toHaveAttribute("aria-label", "What pending means");
    expect(panel).toHaveTextContent("A moderator checks the evidence.");
    await user.click(within(panel).getByRole("button", { hidden: true }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { hidden: true })).not.toBeInTheDocument(),
    );
    expect(trigger).toHaveFocus();
  });
});

describe("Tooltip", () => {
  it("shows its content when the trigger gets keyboard focus and hides on Escape", async () => {
    const user = userEvent.setup();
    render(TooltipHarness);
    await user.tab();
    const trigger = screen.getByRole("button", { name: "Edit build" });
    expect(await screen.findByRole("tooltip", { hidden: true })).toHaveTextContent("Edit build");
    // The tooltip repeats the name, so it must not be read again as a description.
    expect(trigger).not.toHaveAttribute("aria-describedby");
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("tooltip", { hidden: true })).not.toBeInTheDocument(),
    );
  });
});

describe("Tabs panel mode", () => {
  it("selects with arrow keys, skips disabled tabs, and shows one panel", async () => {
    const user = userEvent.setup();
    render(Tabs, {
      mode: "panel",
      label: "Preview",
      items: [
        { id: "render", label: "Render" },
        { id: "video", label: "Video", disabled: true },
        { id: "schematic", label: "Schematic" },
      ],
      panel: createRawSnippet((tab: () => { label: string }) => ({
        render: () => `<p>${tab().label} panel</p>`,
      })),
    });
    const render_ = screen.getByRole("tab", { name: "Render" });
    expect(render_).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Render panel");
    await user.click(render_);
    await user.keyboard("{ArrowRight}");
    const schematic = screen.getByRole("tab", { name: "Schematic" });
    expect(schematic).toHaveFocus();
    expect(schematic).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Schematic panel");
  });
});

describe("SegmentedControl radio mode", () => {
  it("is a native radio group that arrow keys move through", async () => {
    const user = userEvent.setup();
    const onchange = vi.fn();
    render(SegmentedControl, {
      mode: "radio",
      label: "Unit",
      name: "unit",
      value: "ticks",
      onchange,
      items: [
        { label: "Ticks", value: "ticks" },
        { label: "Seconds", value: "seconds" },
      ],
    });
    const group = screen.getByRole("group", { name: "Unit" });
    const ticks = within(group).getByRole("radio", { name: "Ticks" });
    expect(ticks).toBeChecked();
    expect(ticks).toHaveAttribute("name", "unit");
    await user.click(ticks);
    await user.keyboard("{ArrowRight}");
    expect(within(group).getByRole("radio", { name: "Seconds" })).toBeChecked();
    expect(onchange).toHaveBeenLastCalledWith("seconds");
  });
});

describe("CopyButton", () => {
  it("copies, announces the outcome, and resets after two seconds", async () => {
    const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
    vi.useFakeTimers();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    render(CopyButton, {
      value: "mc.example.net",
      label: "Copy address",
      copiedLabel: "Copied",
      failedLabel: "Copy failed",
    });
    const button = screen.getByRole("button", { name: "Copy address" });
    await user.click(button);
    expect(writeText).toHaveBeenCalledWith("mc.example.net");
    expect(screen.getByRole("status")).toHaveTextContent("Copied");
    expect(button).toHaveAccessibleName("Copy address");
    vi.advanceTimersByTime(2000);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/^$/));

    writeText.mockRejectedValueOnce(new Error("denied"));
    await user.click(button);
    expect(screen.getByRole("status")).toHaveTextContent("Copy failed");
  });
});

describe("Announcer", () => {
  it("reads each message, including a repeat of the last one", async () => {
    vi.useFakeTimers();
    render(Announcer);
    const region = screen.getByRole("status");
    announce("48 builds");
    vi.advanceTimersByTime(100);
    await waitFor(() => expect(region).toHaveTextContent("48 builds"));
    announce("48 builds");
    await waitFor(() => expect(region).toHaveTextContent(/^$/));
    vi.advanceTimersByTime(100);
    await waitFor(() => expect(region).toHaveTextContent("48 builds"));
  });
});
