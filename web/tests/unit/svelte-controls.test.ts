import { render, screen, cleanup, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Controls from "../../src/workbench/Controls.svelte";
import type { Locale } from "../../src/locales/locale";
import * as m from "../../src/locales/messages";

afterEach(cleanup);
for (const locale of ["en", "zh-CN"] satisfies Locale[]) {
  describe(`Svelte workbench (${locale})`, () => {
    it("validates and saves a draft, then clears feedback on edit", async () => {
      const user = userEvent.setup();
      render(Controls, { locale });
      const trigger = screen.getByRole("button", {
        name: m.workbenchNewDraft(locale),
      });
      await user.click(trigger);
      const save = await screen.findByRole("button", {
        name: m.workbenchSaveDraft(locale),
      });
      await user.click(save);
      const title = screen.getByRole("textbox", {
        name: m.workbenchBuildTitle(locale),
      });
      expect(title).toHaveAttribute("aria-invalid", "true");
      expect(title).toHaveAccessibleDescription(m.workbenchEnterATitleForYourBuild(locale));
      await user.type(title, "Copper Bolt");
      await user.click(save);
      expect(screen.getByRole("status")).toHaveTextContent(m.workbenchExampleDraftSaved(locale));
      await user.type(title, " revised");
      expect(screen.getByRole("status").textContent).toBe("");
      await user.click(screen.getByRole("button", { name: m.workbenchCloseDialog(locale) }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });
    it("scrolls to the example catalogue", async () => {
      const target = document.createElement("section");
      target.id = "patterns";
      const scroll = vi.fn();
      target.scrollIntoView = scroll;
      document.body.append(target);
      try {
        const user = userEvent.setup();
        render(Controls, { locale });
        await user.click(screen.getByRole("button", { name: m.workbenchBrowseExamples(locale) }));
        expect(scroll).toHaveBeenCalledWith({ behavior: "smooth" });
      } finally {
        target.remove();
      }
    });
  });
}
