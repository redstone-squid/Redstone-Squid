import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SearchComposer from "../../src/features/search/SearchComposer.svelte";
import Autocomplete from "../../src/ui/svelte/Autocomplete.svelte";
import { loadSuggestions } from "../../src/features/search/suggestions";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const suggestions = {
  items: [
    { value: "door", label: "Door", description: "A build" },
    { value: "extender", label: "Extender", description: null },
  ],
};
const props = {
  name: "keywords",
  label: "Keywords",
  suggestionsLabel: "Suggestions",
  errorLabel: "Unavailable",
};

describe("production search composer", () => {
  it("submits named guided fields and retains both drafts and sort across modes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [] }))));
    const user = userEvent.setup();
    const { container } = render(SearchComposer, { locale: "en", action: "/builds" });
    await user.type(screen.getByLabelText("Creator"), 'Bob "Builder"');
    await user.selectOptions(screen.getByLabelText("Category"), "door");
    await user.click(screen.getByText("More filters"));
    for (const [label, value] of [
      ["Maximum width", "5"],
      ["Maximum height", "6"],
      ["Maximum depth", "7"],
      ["Maximum opening time", "8gt"],
      ["Maximum closing time", "2rt"],
      ["Type or pattern", "flush"],
      ["Tag", "fast"],
    ]) {
      await fireEvent.input(screen.getByLabelText(label ?? ""), { target: { value } });
    }
    await user.selectOptions(screen.getByLabelText("Sort"), "width");
    const form = container.querySelector("form");
    if (!form) throw new Error("Missing search form");
    const data = new FormData(form);
    expect(data.get("creator")).toBe('Bob "Builder"');
    expect(data.get("maxOpening")).toBe("8gt");
    expect(data.get("sort")).toBe("width");
    expect(data.has("q")).toBe(false);
    expect(container.querySelector('input[name="mode"]')).toHaveValue("guided");
    await user.click(screen.getByRole("link", { name: "Advanced" }));
    await user.type(screen.getByLabelText("Advanced query"), "kind:utility");
    await user.click(screen.getByRole("link", { name: "Guided" }));
    expect(screen.getByLabelText("Creator")).toHaveValue('Bob "Builder"');
    expect(screen.getByLabelText("Sort")).toHaveValue("width");
    await user.click(screen.getByRole("link", { name: "Advanced" }));
    expect(screen.getByLabelText("Advanced query")).toHaveValue("kind:utility");
    expect(container.querySelector("form")).toHaveAttribute("method", "get");
  });
  it("provides localized mode links that work without hydration", () => {
    render(SearchComposer, {
      locale: "zh-CN",
      action: "/zh-cn/search",
      initialQuery: "kind:door",
      initialGuided: { creator: "红石猫" },
      initialSort: "width",
    });
    const href = screen.getByRole("link", { name: "高级" }).getAttribute("href");
    expect(href).toContain("/zh-cn/search?");
    const params = new URL(href ?? "", "https://example.test").searchParams;
    expect(params.get("q")).toBe("kind:door");
    expect(params.get("creator")).toBe("红石猫");
    expect(params.get("sort")).toBe("width");
    expect(screen.getByRole("link", { name: "搜索语法帮助" })).toHaveAttribute(
      "href",
      "/zh-cn/search/help",
    );
  });
});

describe("autocomplete lifecycle", () => {
  it("debounces, wraps keyboard selection, restores focus, and dismisses on blur", async () => {
    const load = vi.fn().mockResolvedValue(suggestions);
    const user = userEvent.setup();
    render(Autocomplete, { ...props, load });
    const input = screen.getByLabelText("Keywords");
    await user.type(input, "d");
    expect(load).not.toHaveBeenCalled();
    await user.type(input, "o");
    await screen.findByRole("option", { name: "Door" });
    await user.keyboard("{ArrowUp}{ArrowDown}{Enter}");
    expect(input).toHaveValue("door");
    expect(input).toHaveFocus();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await user.type(input, "s");
    await screen.findByRole("listbox");
    await user.tab();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
  it("ignores a superseded response and an in-flight response after Escape", async () => {
    const resolvers: ((value: typeof suggestions) => void)[] = [];
    const load = vi
      .fn()
      .mockImplementation(() => new Promise((resolve) => resolvers.push(resolve)));
    const user = userEvent.setup();
    render(Autocomplete, { ...props, load });
    const input = screen.getByLabelText("Keywords");
    await user.type(input, "do");
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    const signal = load.mock.calls[0]?.[2] as AbortSignal;
    await user.type(input, "or");
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    expect(signal.aborted).toBe(true);
    resolvers[0]?.(suggestions);
    await user.keyboard("{Escape}");
    resolvers[1]?.(suggestions);
    await waitFor(() => expect((load.mock.calls[1]?.[2] as AbortSignal).aborted).toBe(true));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
  it("splices advanced completions and restores the caret after inserted text", async () => {
    const load = vi.fn().mockResolvedValue({
      items: [{ value: "creator:", label: "Creator" }],
      replacement: { start: 10, end: 12 },
    });
    const user = userEvent.setup();
    render(Autocomplete, {
      ...props,
      multiline: true,
      minLength: 0,
      value: "kind:door cr tag:x",
      load,
    });
    const input = screen.getByLabelText<HTMLTextAreaElement>("Keywords");
    await user.click(input);
    await user.click(await screen.findByRole("option", { name: "Creator" }));
    expect(input).toHaveValue("kind:door creator: tag:x");
    expect(input.selectionStart).toBe(18);
    expect(input).toHaveFocus();
  });
  it("does not select or request suggestions during IME composition", async () => {
    const load = vi.fn().mockResolvedValue(suggestions);
    const user = userEvent.setup();
    render(Autocomplete, { ...props, load });
    const input = screen.getByLabelText("Keywords");
    await user.type(input, "do");
    await screen.findByRole("listbox");
    await fireEvent.compositionStart(input);
    await fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(input).toHaveValue("do");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await fireEvent.compositionEnd(input, { data: "门" });
    await screen.findByRole("listbox");
  });
  it("shows a recoverable failure and aborts requests on unmount", async () => {
    const load = vi.fn().mockRejectedValue(new Error("offline"));
    const user = userEvent.setup();
    const { unmount } = render(Autocomplete, { ...props, load });
    await user.type(screen.getByLabelText("Keywords"), "door");
    expect(await screen.findByRole("status")).toHaveTextContent("Unavailable");
    const signal = load.mock.calls[0]?.[2] as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
  });
});

it("uses the localized proxy and only sends cursor offsets for advanced queries", async () => {
  const fetchMock = vi
    .fn()
    .mockImplementation(() => Promise.resolve(new Response(JSON.stringify(suggestions))));
  vi.stubGlobal("fetch", fetchMock);
  const signal = new AbortController().signal;
  await loadSuggestions("zh-CN", "search_query", "门", 1, signal);
  expect(fetchMock.mock.calls[0]?.[0]).toBe("/zh-cn/api/suggest/search_query?q=%E9%97%A8&cursor=1");
  await loadSuggestions("en", "creators", "Bob", 1, signal);
  expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/suggest/creators?q=Bob");
  fetchMock.mockResolvedValueOnce(new Response("", { status: 503 }));
  await expect(loadSuggestions("en", "creators", "Bob", null, signal)).rejects.toThrow("503");
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: null })));
  expect(await loadSuggestions("en", "creators", "Bob", null, signal)).toMatchObject({ items: [] });
});

it("leaves long advanced queries editable without sending an invalid suggestion request", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  expect(
    await loadSuggestions("en", "search_query", "x".repeat(201), 201, new AbortController().signal),
  ).toEqual({ items: [], replacement: null });
  expect(fetchMock).not.toHaveBeenCalled();
});
