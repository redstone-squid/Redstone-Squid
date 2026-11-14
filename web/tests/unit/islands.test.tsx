import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import MediaGallery from "../../src/components/MediaGallery";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
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
