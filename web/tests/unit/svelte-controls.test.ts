import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import * as m from "@/locales/messages.gen";
import Controls from "@/workbench/Controls.svelte";

it("clears the saved status when the draft title changes", async () => {
  const user = userEvent.setup();
  render(Controls);

  await user.click(screen.getByRole("button", { name: m.workbenchNewDraft() }));
  const dialog = within(await screen.findByRole("dialog"));
  const title = dialog.getByRole("textbox", { name: m.workbenchBuildTitle() });
  const status = dialog.getByRole("status");
  await user.type(title, "Copper Bolt");
  await user.click(dialog.getByRole("button", { name: m.workbenchSaveDraft() }));
  expect(status).toHaveTextContent(m.workbenchExampleDraftSaved());

  await user.type(title, " revised");
  expect(status).toHaveTextContent(/^$/);
});

it("filters fixture suggestions without regard to case or surrounding space", async () => {
  const user = userEvent.setup();
  render(Controls);

  await user.type(
    screen.getByRole("combobox", { name: m.workbenchMinecraftVersion() }),
    "  BEDROCK ",
  );
  const suggestion = await screen.findByRole("option", { name: "Bedrock 1.21.50" });
  expect(suggestion).toBeVisible();
  expect(screen.getAllByRole("option")).toHaveLength(1);
});
