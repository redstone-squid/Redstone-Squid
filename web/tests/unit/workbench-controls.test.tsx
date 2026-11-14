import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Controls } from "../../src/features/workbench/Controls";
import { translate, type Locale } from "../../src/lib/i18n";

for (const locale of ["en", "zh-CN"] satisfies Locale[]) {
  describe(`workbench workflow (${locale})`, () => {
    it("validates a draft, saves an example, and clears saved feedback after editing", async () => {
      const user = userEvent.setup();
      render(<Controls locale={locale} />);
      const trigger = screen.getByRole("button", {
        name: translate(locale, "workbench.new-draft"),
      });
      await user.click(trigger);
      const save = screen.getByRole("button", { name: translate(locale, "workbench.save-draft") });
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
      await user.type(title, " 5×5");
      expect(screen.getByRole("status")).toBeEmptyDOMElement();
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    });

    it("navigates to the catalogue examples without submitting the dialog", async () => {
      const user = userEvent.setup();
      const scroll = vi.fn();
      render(
        <>
          <Controls locale={locale} />
          <section id="patterns" aria-label="Examples" />
        </>,
      );
      const examples = screen.getByRole("region", { name: "Examples" });
      examples.scrollIntoView = scroll;
      await user.click(
        screen.getByRole("button", { name: translate(locale, "workbench.browse-examples") }),
      );
      expect(scroll).toHaveBeenCalledWith({ behavior: "smooth" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
}
