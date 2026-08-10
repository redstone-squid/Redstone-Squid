import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import MediaGallery from "../../src/components/MediaGallery";
import SearchComposer from "../../src/components/SearchComposer";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("SearchComposer", () => {
  it("composes guided state into a safe q parameter and exposes no status filter", async () => {
    const user = userEvent.setup();
    const { container } = render(<SearchComposer locale="en" action="/builds" />);
    await user.type(screen.getByLabelText("Creator"), 'Bob "Builder"');
    await user.selectOptions(screen.getByLabelText("Category"), "door");
    await user.type(screen.getByLabelText("Maximum width"), "5");
    expect(container.querySelector<HTMLInputElement>('input[name="q"]')).toHaveValue(
      'creator:"Bob \\"Builder\\"" kind:"door" width<=5',
    );
    expect(screen.queryByLabelText(/status/i)).not.toBeInTheDocument();
    expect(container.querySelector("form")).toHaveAttribute("action", "/builds");
  });

  it("keeps every guided field and sort selection URL-addressable", () => {
    const { container } = render(<SearchComposer locale="en" action="/builds" />);
    const values = [
      ["Keywords", "fast"],
      ["Version", "Java 1.21"],
      ["Type or pattern", "flush"],
      ["Maximum height", "6"],
      ["Maximum depth", "7"],
      ["Maximum opening time", "8gt"],
      ["Maximum closing time", "9gt"],
      ["Tag", "seamless"],
    ] as const;
    for (const [label, value] of values) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "width" } });
    expect(container.querySelector<HTMLInputElement>('input[name="q"]')?.value).toContain(
      'version:"Java 1.21" type:"flush"',
    );
    expect(container.querySelector('select[name="sort"]')).toHaveValue("width");
  });

  it("toggles to the raw query syntax without losing the initial query", async () => {
    const user = userEvent.setup();
    render(<SearchComposer locale="zh-CN" action="/zh-cn/search" initialQuery="tag:seamless" />);
    await user.click(screen.getByRole("button", { name: "高级" }));
    expect(screen.getByLabelText("高级查询")).toHaveValue("tag:seamless");
    expect(screen.getByRole("link", { name: "搜索语法帮助" })).toHaveAttribute(
      "href",
      "/zh-cn/search/help",
    );
    fireEvent.change(screen.getByLabelText("高级查询"), { target: { value: "kind:door" } });
    expect(screen.getByLabelText("高级查询")).toHaveValue("kind:door");
  });

  it("debounces abortable same-origin suggestions and supports keyboard selection", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ suggestions: ["door", "door type"] }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<SearchComposer locale="en" action="/search" />);
    const input = screen.getByLabelText("Keywords");
    fireEvent.change(input, { target: { value: "do" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
      await Promise.resolve();
    });
    expect(screen.getByRole("option", { name: "door" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
    const request = fetchMock.mock.calls[0]?.[0];
    const requestUrl = request instanceof Request ? request.url : request?.toString();
    expect(requestUrl).toContain("/api/suggest?q=do");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowUp" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(input).toHaveValue("door");
  });

  it("lets advanced-search users click and dismiss suggestions", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ suggestions: ["tag:seamless"] }), {
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );
    render(
      <SearchComposer locale="en" action="/search" initialMode="advanced" initialQuery="ta" />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
      await Promise.resolve();
    });
    act(() => screen.getByRole("option", { name: "tag:seamless" }).click());
    expect(screen.getByLabelText("Advanced query")).toHaveValue("tag:seamless");

    fireEvent.change(screen.getByLabelText("Advanced query"), { target: { value: "tag" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
      await Promise.resolve();
    });
    fireEvent.keyDown(screen.getByLabelText("Advanced query"), { key: "Escape" });
    expect(screen.queryByRole("option", { name: "tag:seamless" })).not.toBeInTheDocument();
  });

  it("ignores aborted suggestion requests", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockRejectedValue(new DOMException("Aborted", "AbortError")),
    );
    render(<SearchComposer locale="en" action="/search" initialGuided={{ keywords: "do" }} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
      await Promise.resolve();
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("announces a suggestion failure", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response("", { status: 503 })),
    );
    render(<SearchComposer locale="en" action="/search" />);
    fireEvent.change(screen.getByLabelText("Keywords"), { target: { value: "door" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toHaveTextContent("temporarily unavailable");
  });
});

describe("MediaGallery", () => {
  const items = [
    { kind: "render" as const, url: "https://media.example/one.png", label: "Render 1" },
    { kind: "image" as const, url: "https://media.example/two.png", label: "Image 1" },
  ];

  it("renders useful HTML before interaction and advances with controls", async () => {
    const user = userEvent.setup();
    render(<MediaGallery items={items} locale="en" title="Compact Door" />);
    expect(screen.getByAltText("Compact Door — Render 1")).toBeVisible();
    expect(screen.getByText("Item 1 of 2")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next media" }));
    expect(screen.getByAltText("Compact Door — Image 1")).toBeVisible();
    expect(screen.getByText("Item 2 of 2")).toBeInTheDocument();
  });

  it("supports arrow, Home, and End keys from its keyboard controls", () => {
    render(<MediaGallery items={items} locale="en" title="Compact Door" />);
    const previous = screen.getByRole("button", { name: "Previous media" });
    fireEvent.keyDown(previous, { key: "ArrowRight" });
    expect(screen.getByText("Item 2 of 2")).toBeInTheDocument();
    fireEvent.keyDown(previous, { key: "Home" });
    expect(screen.getByText("Item 1 of 2")).toBeInTheDocument();
    fireEvent.keyDown(previous, { key: "End" });
    expect(screen.getByText("Item 2 of 2")).toBeInTheDocument();
    fireEvent.click(previous);
    expect(screen.getByText("Item 1 of 2")).toBeInTheDocument();
    const next = screen.getByRole("button", { name: "Next media" });
    fireEvent.keyDown(next, { key: "ArrowLeft" });
    expect(screen.getByText("Item 2 of 2")).toBeInTheDocument();
    fireEvent.keyDown(next, { key: "Home" });
    expect(screen.getByText("Item 1 of 2")).toBeInTheDocument();
    fireEvent.keyDown(next, { key: "End" });
    expect(screen.getByText("Item 2 of 2")).toBeInTheDocument();
  });

  it("reveals the branded fallback when remote media breaks", () => {
    render(<MediaGallery items={items.slice(0, 1)} locale="zh-CN" title="门" />);
    const image = screen.getByRole("img");
    fireEvent.error(image);
    expect(image).not.toBeVisible();
    expect(screen.getByText("图片不可用")).toBeVisible();
  });

  it("renders nothing for an empty collection", () => {
    const { container } = render(<MediaGallery items={[]} locale="en" title="Empty" />);
    expect(container).toBeEmptyDOMElement();
  });
});
