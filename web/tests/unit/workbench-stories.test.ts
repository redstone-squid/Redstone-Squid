import { globSync, readFileSync } from "node:fs";
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import type { Component } from "svelte";
import { describe, expect, it } from "vitest";
import * as m from "@/locales/messages.gen";
import DraftDialog from "@/workbench/DraftDialog.svelte";
import Foundations from "@/workbench/Foundations.svelte";
import { GROUPS, isGroup } from "@/workbench/groups";
import ActionsStories from "@/workbench/stories/ActionsStories.svelte";
import ContentStories from "@/workbench/stories/ContentStories.svelte";
import DataStories from "@/workbench/stories/DataStories.svelte";
import FeedbackStories from "@/workbench/stories/FeedbackStories.svelte";
import FormsStories from "@/workbench/stories/FormsStories.svelte";
import LayoutStories from "@/workbench/stories/LayoutStories.svelte";
import NavigationStories from "@/workbench/stories/NavigationStories.svelte";
import OverlaysStories from "@/workbench/stories/OverlaysStories.svelte";
import PatternsStories from "@/workbench/stories/PatternsStories.svelte";
import TableStories from "@/workbench/stories/TableStories.svelte";

// Building blocks of other components, with no use on their own.
const INTERNAL = new Set(["forms/FieldFrame", "forms/CharacterCount", "overlays/ModalFrame"]);

describe("workbench", () => {
  it("has a story for every src/ui component", () => {
    const components = globSync("src/ui/**/*.svelte")
      .map((path) => path.replace(/^src\/ui\//, "").replace(/\.svelte$/, ""))
      .filter((source) => !INTERNAL.has(source));
    const stories = globSync("src/workbench/stories/*.svelte")
      .map((path) => readFileSync(path, "utf8"))
      .join("\n");
    const covered = new Set(stories.matchAll(/source="([^"]+)"/g).map(([, source]) => source));
    expect(components.filter((source) => !covered.has(source))).toEqual([]);
  });

  it("has a stories component for every group", () => {
    const files = globSync("src/workbench/stories/*Stories.svelte").map((path) =>
      path
        .replace(/^.*\//, "")
        .replace(/Stories\.svelte$/, "")
        .toLowerCase(),
    );
    expect(files.toSorted()).toEqual(Object.keys(GROUPS).toSorted());
    expect(isGroup("forms")).toBe(true);
    expect(isGroup("media")).toBe(false);
    expect(isGroup(undefined)).toBe(false);
  });

  it.each<[string, Component]>([
    ["layout", LayoutStories],
    ["content", ContentStories],
    ["actions", ActionsStories],
    ["feedback", FeedbackStories],
    ["data", DataStories],
    ["navigation", NavigationStories],
    ["forms", FormsStories],
    ["table", TableStories],
    ["overlays", OverlaysStories],
    ["patterns", PatternsStories],
  ])("renders the %s stories", (_group, Stories) => {
    const { container } = render(Stories);
    expect(container.querySelectorAll("section[id]").length).toBeGreaterThan(0);
  });

  it("renders the foundations", () => {
    render(Foundations);
    expect(screen.getByRole("region", { name: m.workbenchFoundations() })).toHaveTextContent(
      "--line-strong",
    );
  });

  it("keeps interactive stories working after hydration", async () => {
    const user = userEvent.setup();
    render(FeedbackStories);
    const flush = screen.getByRole("button", { name: "Flush" });
    await user.click(flush);
    expect(flush).toHaveAttribute("aria-pressed", "false");
    await user.click(screen.getByRole("button", { name: "Remove flush" }));
    expect(screen.queryByRole("button", { name: "Remove flush" })).not.toBeInTheDocument();
  });

  it("clears the saved status when the draft title changes", async () => {
    const user = userEvent.setup();
    render(DraftDialog);
    await user.click(screen.getByRole("button", { name: m.workbenchNewDraft() }));
    const dialog = within(await screen.findByRole("dialog"));
    const title = dialog.getByRole("textbox", { name: m.workbenchBuildTitle() });
    const status = dialog.getByRole("status");
    await user.click(dialog.getByRole("button", { name: m.workbenchSaveDraft() }));
    expect(status).toHaveTextContent(m.workbenchCheckTheHighlightedField());
    await user.type(title, "Copper Bolt");
    await user.click(dialog.getByRole("button", { name: m.workbenchSaveDraft() }));
    expect(status).toHaveTextContent(m.workbenchExampleDraftSaved());
    await user.type(title, " revised");
    expect(status).toHaveTextContent(/^$/);
  });
});
