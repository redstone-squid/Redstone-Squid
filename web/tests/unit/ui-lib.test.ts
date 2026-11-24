import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { focusAfterRemoval, focusTarget } from "@/ui/lib/focus";
import { pageQueryClient } from "@/ui/lib/query-client";
import { commitUrlState, readUrlState, writeUrlState } from "@/ui/lib/url-state";

const listing = z.object({
  q: z.string().catch(""),
  sort: z.enum(["newest", "title"]).catch("newest"),
  offset: z.coerce.number().int().min(0).catch(0),
  tag: z.array(z.string()).catch([]),
});

describe("URL state", () => {
  afterEach(() => history.replaceState(null, "", "/"));

  it("falls back to the default for an invalid value instead of failing the page", () => {
    const state = readUrlState(listing, new URLSearchParams("sort=fastest&offset=-3&q=piston"));
    expect(state).toEqual({ q: "piston", sort: "newest", offset: 0, tag: [] });
  });

  it("collects a repeated parameter into an array field", () => {
    expect(readUrlState(listing, new URLSearchParams("tag=a&tag=b")).tag).toEqual(["a", "b"]);
  });

  it("omits defaults and keeps parameters the schema does not own", () => {
    const params = writeUrlState(
      listing,
      { q: "door", sort: "newest", offset: 0, tag: ["a", "b"] },
      new URLSearchParams("view=table&sort=title"),
    );
    expect(params.toString()).toBe("view=table&q=door&tag=a&tag=b");
  });

  it("pushes filter changes so Back works, keeps the locale prefix, and skips no-op writes", () => {
    history.replaceState(null, "", "/zh-cn/search?q=door#results");
    const push = vi.spyOn(history, "pushState");
    commitUrlState(listing, { q: "door", sort: "title", offset: 0, tag: [] }, "push");
    expect(location.pathname + location.search + location.hash).toBe(
      "/zh-cn/search?q=door&sort=title#results",
    );
    commitUrlState(listing, { q: "door", sort: "title", offset: 0, tag: [] }, "push");
    expect(push).toHaveBeenCalledTimes(1);
    push.mockRestore();
  });

  it("replaces the entry for view changes, adding no history", () => {
    const before = history.length;
    commitUrlState(listing, { q: "x", sort: "newest", offset: 0, tag: [] }, "replace");
    expect(history).toHaveLength(before);
    expect(location.search).toBe("?q=x");
  });
});

describe("focus", () => {
  function list(count: number): HTMLButtonElement[] {
    const heading = document.createElement("h2");
    const buttons = Array.from({ length: count }, () => document.createElement("button"));
    document.body.replaceChildren(heading, ...buttons);
    return buttons;
  }

  it.each([
    ["the next item", 3, 1, 1],
    ["the previous item when the last was removed", 2, 2, 1],
  ])("moves to %s after a removal", (_case, remaining, index, expected) => {
    const controls = list(remaining);
    focusAfterRemoval(controls, index, document.querySelector("h2"));
    expect(document.activeElement).toBe(controls[expected]);
  });

  it("moves to the heading when the list is empty, without adding it to the tab order", () => {
    list(0);
    const heading = document.querySelector("h2");
    focusAfterRemoval([], 0, heading);
    expect(document.activeElement).toBe(heading);
    expect(heading?.tabIndex).toBe(-1);
  });

  it("keeps a focusable target's own tab order", () => {
    const [button] = list(1);
    if (!button) throw new Error("no button");
    focusTarget(button);
    expect(button.hasAttribute("tabindex")).toBe(false);
  });
});

describe("page query client", () => {
  it("shares one client between a page's islands", () => {
    expect(pageQueryClient()).toBe(pageQueryClient());
  });

  it("retries a server failure but not a client error, and never a write", () => {
    const options = pageQueryClient().getDefaultOptions();
    const retry = options.queries?.retry;
    if (typeof retry !== "function") throw new TypeError("retry is not a policy");
    expect(retry(0, Object.assign(new Error("down"), { status: 503 }))).toBe(true);
    expect(retry(0, Object.assign(new Error("missing"), { status: 404 }))).toBe(false);
    expect(retry(2, new TypeError("network"))).toBe(false);
    expect(options.mutations?.retry).toBe(false);
  });
});
