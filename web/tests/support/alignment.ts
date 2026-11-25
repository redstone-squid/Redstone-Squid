import type { Page } from "@playwright/test";

/**
 * Finds rows whose controls start at different heights, such as a labelled field beside one
 * whose label wraps, or a submit button that sits level with a field's label instead of its
 * box. A row is the children of one flex or grid container that share a line. Only children
 * holding a field (input, select, textarea, combobox) or a single button are compared, so a
 * card's corner tools beside its fields pass. Workbench specimens (`data-specimen`) are
 * independent and never compared with each other.
 *
 * Returns one line per misaligned row, naming its container and each child's offset.
 */
export async function misalignedRows(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const FIELD =
      'input:not([type="hidden"],[type="checkbox"],[type="radio"]),select,textarea,[role="combobox"]';
    const visible = (element: Element) => {
      const box = element.getBoundingClientRect();
      return box.width > 2 && box.height > 2 && getComputedStyle(element).visibility !== "hidden";
    };
    // A control inside a bordered box (a prefix, a chip) starts where the box does.
    const boxTop = (control: Element, item: Element) => {
      let top = control.getBoundingClientRect().top;
      for (let node = control.parentElement; node && node !== item; node = node.parentElement) {
        const box = node.getBoundingClientRect();
        if (box.height > 120) break;
        if (Number.parseFloat(getComputedStyle(node).borderTopWidth) > 0) top = box.top;
      }
      return top;
    };
    const controlTop = (item: Element): number | undefined => {
      if (item.matches("[data-specimen]")) return undefined;
      const field = [...item.querySelectorAll(FIELD)].find(visible);
      if (field) return boxTop(field, item);
      const buttons = item.matches("button, a[href]")
        ? [item]
        : [...item.querySelectorAll("button")].filter(visible);
      const [button] = buttons;
      return buttons.length === 1 && button !== undefined && visible(button)
        ? button.getBoundingClientRect().top
        : undefined;
    };
    const describe = (element: Element) => {
      const classes = (element.getAttribute("class") ?? "").trim().split(/\s+/).slice(0, 4);
      const section = element.closest("section[id]")?.id;
      return `<${[element.tagName.toLowerCase(), ...classes].join(".")}>${section === undefined ? "" : ` in #${section}`}`;
    };

    const problems: string[] = [];
    for (const container of document.querySelectorAll("body *")) {
      if (!/flex|grid/.test(getComputedStyle(container).display)) continue;
      const lines = new Map<number, Element[]>();
      for (const child of container.children) {
        if (!visible(child)) continue;
        const top = Math.round(child.getBoundingClientRect().top / 4);
        lines.set(top, [...(lines.get(top) ?? []), child]);
      }
      for (const line of lines.values()) {
        const tops = line.map((item) => controlTop(item)).filter((top) => top !== undefined);
        const hasField = line.some(
          (item) => !item.matches("[data-specimen]") && item.querySelector(FIELD) !== null,
        );
        if (!hasField || tops.length < 2) continue;
        const first = Math.min(...tops);
        if (Math.max(...tops) - first > 1.5)
          problems.push(
            `${describe(container)}: ${tops.map((top) => Math.round(top - first)).join("px, ")}px`,
          );
      }
    }
    return problems;
  });
}
