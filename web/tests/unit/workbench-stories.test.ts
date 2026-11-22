import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { createRawSnippet } from "svelte";
import { afterEach, expect, it } from "vitest";
import * as m from "@/locales/messages.gen";
import BrowsingPreview from "@/workbench/BrowsingPreview.svelte";
import ButtonStory from "@/workbench/ButtonStory.svelte";
import FieldStory from "@/workbench/FieldStory.svelte";
import LayoutStory from "@/workbench/LayoutStory.svelte";
import ThemeToggle from "@/workbench/ThemeToggle.svelte";
import { buttonStyles } from "@/ui/variants";

afterEach(() => {
  history.replaceState(null, "", "/");
  delete document.documentElement.dataset.theme;
  document
    .querySelectorAll("[data-workbench-media], [data-workbench-trial]")
    .forEach((link) => link.remove());
});

it("restores all previews on history traversal and updates the existing history entry", async () => {
  const user = userEvent.setup();
  const initialSearch = "?theme=unknown&variant=unknown&keep=1";
  history.replaceState({ fixture: true }, "", `/design-system${initialSearch}#fields`);
  const trialNav = document.createElement("a");
  trialNav.href = "/design-system-trial?theme=light";
  trialNav.dataset.workbenchTrial = "";
  const mediaNav = document.createElement("a");
  mediaNav.href = "/design-system/media";
  mediaNav.dataset.workbenchMedia = "";
  document.body.append(trialNav, mediaNav);
  render(ThemeToggle, { theme: "light" });
  render(ButtonStory, { search: initialSearch });
  render(FieldStory, { search: initialSearch });
  const layout = render(LayoutStory, { search: initialSearch });
  const first = createRawSnippet(() => ({ render: () => "<p>First sample record</p>" }));
  const next = createRawSnippet(() => ({ render: () => "<p>Second sample record</p>" }));
  render(BrowsingPreview, { search: initialSearch, first, next });

  const trial = screen.getByRole("link", { name: m.trialTitle() });
  const category = screen.getByRole("combobox", { name: m.trialCategory() });
  expect(trial).toHaveClass(...buttonStyles({ variant: "primary" }).split(" "));
  expect(screen.getByText("First sample record")).toBeVisible();
  expect(category).toBeEnabled();

  const historyLength = history.length;
  await user.selectOptions(screen.getByRole("combobox", { name: m.workbenchVariant() }), "quiet");
  await user.selectOptions(screen.getByRole("combobox", { name: m.workbenchSize() }), "small");
  await user.click(screen.getByRole("button", { name: m.workbenchDarkTheme() }));
  await user.click(screen.getByRole("button", { name: m.workbenchDisabled() }));
  await user.click(screen.getByRole("button", { name: m.workbenchStack() }));
  await user.click(screen.getByRole("button", { name: m.workbenchCompact() }));
  await user.click(screen.getByRole("button", { name: m.commonLoadMore() }));

  expect(history).toHaveProperty("length", historyLength);
  expect(history.state).toEqual({ fixture: true });
  expect(location.hash).toBe("#fields");
  expect(new URLSearchParams(location.search).get("keep")).toBe("1");
  expect(new URL(trialNav.href).search).toBe("?theme=dark");
  expect(new URL(mediaNav.href).searchParams.get("offset")).toBe("1");
  expect(trial).toHaveClass(...buttonStyles({ variant: "quiet", size: "small" }).split(" "));
  expect(screen.getByText("Second sample record")).toBeVisible();
  expect(category).toBeDisabled();
  await user.click(screen.getByRole("button", { name: m.commonPrevious() }));
  expect(new URLSearchParams(location.search).has("offset")).toBe(false);

  history.replaceState(null, "", "/design-system?theme=light&layout=row&fieldState=invalid");
  window.dispatchEvent(new PopStateEvent("popstate"));
  expect(await screen.findByRole("button", { name: m.workbenchDarkTheme() })).toBeVisible();
  expect(category).toHaveAccessibleDescription(m.trialRequired());
  expect(layout.container.querySelector("code")).toHaveTextContent(
    '<Layout pattern="row" gap="normal">',
  );
  expect(screen.getByText("First sample record")).toBeVisible();
});
