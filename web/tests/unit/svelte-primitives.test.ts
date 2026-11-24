import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { createRawSnippet } from "svelte";
import { describe, expect, it, vi } from "vitest";
import Button from "@/ui/actions/Button.svelte";
import Heading from "@/ui/content/Heading.svelte";
import Link from "@/ui/content/Link.svelte";
import Avatar from "@/ui/data/Avatar.svelte";
import Card from "@/ui/data/Card.svelte";
import Diff from "@/ui/data/Diff.svelte";
import Image from "@/ui/data/Image.svelte";
import ListItem from "@/ui/data/ListItem.svelte";
import Chip from "@/ui/feedback/Chip.svelte";
import Notice from "@/ui/feedback/Notice.svelte";
import Progress from "@/ui/feedback/Progress.svelte";
import TextField from "@/ui/forms/TextField.svelte";
import Layout from "@/ui/layout/Layout.svelte";
import Breadcrumbs from "@/ui/navigation/Breadcrumbs.svelte";
import Pagination from "@/ui/navigation/Pagination.svelte";
import SegmentedControl from "@/ui/navigation/SegmentedControl.svelte";
import Tabs from "@/ui/navigation/Tabs.svelte";
import Section from "@/ui/patterns/Section.svelte";
import "@/ui/image-fallback";
import Harness from "./fixtures/SvelteDialogHarness.svelte";

const text = (value: string) => createRawSnippet(() => ({ render: () => `<span>${value}</span>` }));
const labels = { nav: "Pages", previous: "Previous", next: "Next" };

describe("Button", () => {
  it("renders a link, and a disabled link without an href", () => {
    render(Button, { href: "/submit", children: text("Submit") });
    render(Button, { href: "/next", disabled: true, children: text("Next") });
    expect(screen.getByRole("link", { name: "Submit" })).toHaveAttribute("href", "/submit");
    const disabled = screen.getByRole("link", { name: "Next" });
    expect(disabled).not.toHaveAttribute("href");
    expect(disabled).toHaveAttribute("aria-disabled", "true");
  });

  it("ignores clicks and form submission while pending and announces it", async () => {
    const user = userEvent.setup();
    const onclick = vi.fn();
    const { rerender } = render(Button, {
      onclick,
      pendingLabel: "Saving",
      children: text("Save"),
    });
    const button = screen.getByRole("button", { name: "Save" });
    await user.click(button);
    expect(onclick).toHaveBeenCalledOnce();
    expect(screen.getByRole("status")).toHaveTextContent(/^$/);

    await rerender({ onclick, pending: true, pendingLabel: "Saving", children: text("Save") });
    await user.click(button);
    expect(onclick).toHaveBeenCalledOnce();
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Saving");
  });

  it("names an icon-only button by its label", () => {
    render(Button, { iconOnly: true, label: "Close" });
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });
});

describe("Card", () => {
  it("links the whole card through its title at the given heading level", () => {
    render(Card, { title: "Copper Bolt", href: "/builds/1", headingLevel: 2, eyebrow: "Door" });
    const heading = screen.getByRole("heading", { level: 2, name: "Copper Bolt" });
    expect(within(heading).getByRole("link")).toHaveAttribute("href", "/builds/1");
    expect(screen.getByText("Door")).toBeInTheDocument();
  });

  it("renders a plain surface without a link", () => {
    render(Card, { title: "Notes" });
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("Chip", () => {
  it("removes through a link without JavaScript, or a button inside an island", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    render(Chip, { removeLabel: "Remove 1.21", removeHref: "/search", children: text("1.21") });
    render(Chip, { removeLabel: "Remove flush", onRemove, children: text("flush") });
    expect(screen.getByRole("link", { name: "Remove 1.21" })).toHaveAttribute("href", "/search");
    await user.click(screen.getByRole("button", { name: "Remove flush" }));
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it("exposes a toggle's state", () => {
    render(Chip, { pressed: true, onclick: () => undefined, children: text("Flush") });
    expect(screen.getByRole("button", { name: "Flush" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("images", () => {
  it("shows the fallback when there is no image or it fails to load", async () => {
    render(Image, { src: null, alt: "", width: 640, height: 400, fallbackLabel: "No preview" });
    render(Image, {
      src: "/broken.png",
      alt: "Door",
      width: 640,
      height: 400,
      fallbackLabel: "Unavailable",
    });
    expect(screen.getByText("No preview")).toBeInTheDocument();
    const image = screen.getByRole("img", { name: "Door" });
    await fireEvent.error(image);
    expect(image).not.toBeVisible();
  });

  it("sizes lazy images from their layout and priority images from the fallback", () => {
    const sources = { srcset: "/a-640.png 640w, /a-1280.png 1280w", sizes: "20rem" };
    const props = { src: "/a-640.png", width: 640, height: 400, fallbackLabel: "", sources };
    render(Image, { ...props, alt: "Lazy" });
    render(Image, { ...props, alt: "Priority", priority: true });
    const lazy = screen.getByRole("img", { name: "Lazy" });
    expect(lazy).toHaveAttribute("loading", "lazy");
    expect(lazy).toHaveAttribute("sizes", "auto, 20rem");
    const priority = screen.getByRole("img", { name: "Priority" });
    expect(priority).toHaveAttribute("fetchpriority", "high");
    expect(priority).toHaveAttribute("sizes", "20rem");
  });

  it("falls back to initials and is decorative unless given alt text", async () => {
    render(Avatar, { name: "Space Builder", src: "/broken.png" });
    render(Avatar, { name: "红石猫", alt: "红石猫" });
    expect(screen.getByText("SB")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "红石猫" })).toHaveTextContent("红");
    const picture = document.querySelector("img[data-fallback]");
    if (!(picture instanceof HTMLImageElement)) throw new TypeError("Avatar rendered no image");
    await fireEvent.error(picture);
    expect(picture.hidden).toBe(true);
  });

  it("hides failed images on statically rendered pages", () => {
    const fallback = document.createElement("img");
    fallback.dataset.fallback = "";
    const other = document.createElement("img");
    document.body.append(fallback, other);
    other.dispatchEvent(new Event("error"));
    fallback.dispatchEvent(new Event("error"));
    expect(fallback.hidden).toBe(true);
    expect(other.hidden).toBe(false);
  });
});

describe("navigation", () => {
  it("omits pagination for one page and disables missing directions", () => {
    render(Pagination, { labels });
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    render(Pagination, { nextHref: "/records?after_id=4", range: "1–20 of 30", labels });
    const nav = screen.getByRole("navigation", { name: "Pages" });
    expect(within(nav).getByRole("link", { name: "Previous" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(within(nav).getByRole("link", { name: "Next" })).toHaveAttribute("rel", "next");
    expect(nav).toHaveTextContent("1–20 of 30");
  });

  it("numbers pages and marks the current one", () => {
    render(Pagination, {
      prevHref: "/a",
      labels,
      range: "21–40 of 60",
      pages: [
        { label: "1", href: "/a", name: "Page 1" },
        { label: "2", href: "/b", name: "Page 2", current: true },
      ],
    });
    expect(screen.getByRole("link", { name: "Page 2" })).toHaveAttribute("aria-current", "page");
  });

  it("marks the current tab, segment, and breadcrumb", () => {
    render(Tabs, {
      label: "Scope",
      items: [
        { label: "All", href: "?scope=all", current: true, count: "3" },
        { label: "Builds", href: "?scope=builds" },
      ],
    });
    render(SegmentedControl, {
      label: "View",
      items: [
        { label: "Cards", href: "?view=cards" },
        { label: "Table", href: "?view=table", current: true },
      ],
    });
    render(Breadcrumbs, {
      label: "Breadcrumb",
      items: [{ label: "Builds", href: "/builds" }, { label: "Copper" }],
    });
    expect(screen.getByRole("link", { name: "All 3" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Table" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByText("Copper")).toHaveAttribute("aria-current", "page");
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Builds" })).toHaveAttribute("href", "/builds");
  });
});

describe("content and data", () => {
  it("groups an eyebrow with its heading and keeps the level separate from size", () => {
    render(Heading, { level: 4, size: "display", eyebrow: "Fastest", children: text("Iris") });
    const heading = screen.getByRole("heading", { level: 4, name: "Iris" });
    expect(heading.closest("hgroup")).toHaveTextContent("Fastest");
  });

  it("marks external links", () => {
    render(Link, {
      href: "https://example.com",
      externalLabel: "external",
      children: text("Docs"),
    });
    // jsdom's name computation drops the space a browser keeps between the two inline parts.
    const link = screen.getByRole("link", { name: /^Docs ?\(external\)$/ });
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("names each kind of change in a diff and hides unchanged rows by default", () => {
    render(Diff, {
      caption: "Changes",
      labels: {
        field: "Field",
        before: "Before",
        after: "After",
        empty: "Empty",
        added: "Added",
        removed: "Removed",
        changed: "Changed",
        unchanged: "Unchanged",
      },
      rows: [
        { label: "Opening", before: "9 gt", after: "8 gt", kind: "changed" },
        { label: "Video", before: null, after: "link", kind: "added" },
        { label: "Width", before: "5", after: "5", kind: "unchanged" },
      ],
    });
    const table = screen.getByRole("table", { name: "Changes" });
    expect(within(table).getByRole("rowheader", { name: "Opening Changed" })).toBeInTheDocument();
    expect(within(table).getByText("Empty")).toBeInTheDocument();
    expect(within(table).queryByText("Width")).not.toBeInTheDocument();
  });

  it("announces unread rows before their title", () => {
    render(ListItem, { title: "Record broken", href: "/n/1", unread: true, unreadLabel: "Unread" });
    expect(screen.getByRole("listitem")).toHaveTextContent("Unread: Record broken");
    expect(screen.getByRole("link", { name: "Record broken" })).toHaveAttribute("href", "/n/1");
  });

  it("reads a meter's value as text", () => {
    render(Progress, {
      label: "Votes",
      kind: "meter",
      value: 3,
      max: 5,
      valueLabel: "3 of 5 votes",
      thresholds: [{ value: 4, label: "Approve at 4" }],
    });
    const meter = screen.getByRole("meter", { name: "Votes" });
    expect(meter).toHaveAttribute("aria-valuetext", "3 of 5 votes");
    expect(meter).toHaveAttribute("aria-valuenow", "3");
    expect(screen.getByText("Approve at 4")).toBeInTheDocument();
  });

  it("labels a section by its heading", () => {
    render(Section, { title: "Holders", children: text("Cards") });
    expect(screen.getByRole("region", { name: "Holders" })).toHaveTextContent("Cards");
  });

  it("renders a list layout as a list", () => {
    render(Layout, { as: "ul", pattern: "grid", min: "card-wide" });
    expect(screen.getByRole("list")).toHaveClass("list-none");
  });

  it("announces notices only when given a live role", () => {
    render(Notice, { tone: "danger", title: "Upload failed", role: "alert" });
    render(Notice, { title: "Static" });
    expect(screen.getByRole("alert")).toHaveTextContent("Upload failed");
    expect(screen.queryAllByRole("status")).toHaveLength(0);
  });
});

describe("forms and overlays", () => {
  it("merges field descriptions while preserving caller invalid semantics", () => {
    const help = document.createElement("p");
    help.id = "external-help";
    help.textContent = "External explanation";
    document.body.append(help);
    try {
      render(TextField, {
        label: "First",
        "aria-describedby": "external-help",
        "aria-invalid": "grammar",
        error: "Check spelling",
      });
      render(TextField, { label: "Second", hint: "Another name" });
      const first = screen.getByRole("textbox", { name: "First" });
      const second = screen.getByRole("textbox", { name: "Second" });
      expect(first).toHaveAccessibleDescription("External explanation Check spelling");
      expect(first).toHaveAttribute("aria-invalid", "grammar");
      expect(first.id).not.toBe(second.id);
    } finally {
      help.remove();
    }
  });

  it("opens from its trigger and closes through controlled state", async () => {
    const user = userEvent.setup();
    render(Harness);
    await user.click(screen.getByRole("button", { name: "Edit draft" }));
    expect(await screen.findByRole("dialog")).toHaveClass("wide-dialog");
    await user.click(screen.getByRole("button", { name: "Finish" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
