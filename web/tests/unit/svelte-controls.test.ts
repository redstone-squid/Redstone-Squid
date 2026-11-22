import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Controls from "@/workbench/Controls.svelte";
import type { Locale } from "@/locales/locale";
import * as m from "@/locales/messages.gen";

for (const locale of ["en", "zh-CN"] satisfies Locale[]) {
  describe(`Svelte workbench (${locale})`, () => {
    it("validates and saves a draft, then clears feedback on edit", async () => {
      const user = userEvent.setup();
      document.documentElement.lang = locale;
      render(Controls);
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
      expect(title).toBeInvalid();
      expect(title).toHaveAccessibleDescription(m.workbenchEnterATitleForYourBuild(locale));
      await user.type(title, "Copper Bolt");
      await user.click(save);
      expect(screen.getByRole("status")).toHaveTextContent(m.workbenchExampleDraftSaved(locale));
      await user.type(title, " revised");
      expect(screen.getByRole("status")).toBeEmptyDOMElement();
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
        document.documentElement.lang = locale;
        render(Controls);
        await user.click(screen.getByRole("button", { name: m.workbenchBrowseExamples(locale) }));
        expect(scroll).toHaveBeenCalledWith({ behavior: "smooth" });
      } finally {
        target.remove();
      }
    });
  });
}
