import { render, screen, cleanup, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Controls from "../../src/workbench/Controls.svelte";
import { translate, type Locale } from "../../src/lib/i18n";

afterEach(cleanup);
for (const locale of ["en", "zh-CN"] satisfies Locale[]) {
  describe(`Svelte workbench (${locale})`, () => {
    it("validates and saves a draft, then clears feedback on edit", async () => {
      const user = userEvent.setup();
      render(Controls, { locale });
      const trigger = screen.getByRole("button", {
        name: translate(locale, "workbench.new-draft"),
      });
      await user.click(trigger);
      const save = await screen.findByRole("button", {
        name: translate(locale, "workbench.save-draft"),
      });
      await user.click(save);
      const title = screen.getByRole("textbox", {
        name: translate(locale, "workbench.build-title"),
      });
      expect(title).toHaveAttribute("aria-invalid", "true");
      expect(title).toHaveAccessibleDescription(
        translate(locale, "workbench.enter-a-title-for-your-build"),
      );
      await user.type(title, "Copper Bolt");
      await user.click(save);
      expect(screen.getByRole("status")).toHaveTextContent(
        translate(locale, "workbench.example-draft-saved"),
      );
      await user.type(title, " revised");
      expect(screen.getByRole("status").textContent).toBe("");
      await user.click(
        screen.getByRole("button", { name: translate(locale, "workbench.close-dialog") }),
      );
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
        await user.click(
          screen.getByRole("button", { name: translate(locale, "workbench.browse-examples") }),
        );
        expect(scroll).toHaveBeenCalledWith({ behavior: "smooth" });
      } finally {
        target.remove();
      }
    });
  });
}
