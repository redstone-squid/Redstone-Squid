import type { Page } from "@playwright/test";
import { localizePath } from "@/locales/locale";
import { GROUPS } from "@/workbench/groups";
import { misalignedRows } from "@tests/support/alignment";
import { expect, test } from "@tests/support/test";

// Layouts are fluid with per-component container queries rather than a few supported viewport
// sizes, so check every width a phone, tablet, or resized window can have, for overflow and
// for controls side by side that stop lining up when a label wraps. Steps of 24px are
// small enough that no breakpoint range is skipped.
const WIDTHS = Array.from({ length: (1600 - 320) / 24 + 1 }, (_, index) => 320 + index * 24);

// The pseudo-locale's longer text finds labels that cannot wrap; Chinese breaks between any
// two characters, so it only needs the foundations page.
const PAGES = [
  localizePath("/design-system", "zh-CN"),
  ...["/design-system", ...Object.keys(GROUPS).map((group) => `/design-system/${group}`)].map(
    (path) => localizePath(path, "en-XA"),
  ),
  localizePath("/design-system-trial", "en-XA"),
];

/**
 * Describes each element that sticks out of the page, or out of an ancestor that clips it
 * (overflow hidden or clip). Content inside a scrolling ancestor may be wider on purpose.
 */
async function overflowing(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const clipping = new Map<Element, "clip" | "scroll" | null>();
    function clipOf(element: Element): "clip" | "scroll" | null {
      let kind = clipping.get(element);
      if (kind === undefined) {
        const overflow = getComputedStyle(element).overflowX;
        kind =
          overflow === "visible"
            ? null
            : overflow === "auto" || overflow === "scroll"
              ? "scroll"
              : "clip";
        clipping.set(element, kind);
      }
      return kind;
    }
    function describe(element: Element): string {
      const classes = (element.getAttribute("class") ?? "").trim().split(/\s+/).slice(0, 4);
      const text = element.textContent.trim().slice(0, 40);
      return `<${[element.tagName.toLowerCase(), ...classes].join(".")}> "${text}"`;
    }

    const found: string[] = [];
    const pageEdge = document.documentElement.clientWidth;
    for (const element of document.body.querySelectorAll("*")) {
      // Visually hidden text lives in a 1px clipped box by design.
      if (element.closest(".sr-only, [hidden], dialog:not([open])") !== null) continue;
      const box = element.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) continue;
      let left = 0;
      let right = pageEdge;
      let scrolls = false;
      for (let parent = element.parentElement; parent !== null; parent = parent.parentElement) {
        const kind = clipOf(parent);
        if (kind === "scroll") scrolls = true;
        if (kind === "clip") ({ left, right } = parent.getBoundingClientRect());
        if (kind !== null) break;
      }
      // A zero-width clip shows nothing, as in LayerChart's offscreen text measurer.
      if (right - left < 1) continue;
      if (!scrolls && (box.left < left - 1 || box.right > right + 1)) found.push(describe(element));
    }
    return found;
  });
}

test.skip(({ isMobile }) => isMobile, "Desktop sweeps the mobile widths too");

for (const path of PAGES) {
  test(`reflows ${path} from 320px to 1600px`, async ({ page }) => {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    const failures: string[] = [];
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      // Measured plots redraw from a ResizeObserver, a frame after the layout changes.
      await page.evaluate(
        () =>
          new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve(null))),
          ),
      );
      // The innermost overflowing elements are usually the cause; the rest are their ancestors.
      const elements = (await overflowing(page)).slice(-3);
      failures.push(...elements.map((element) => `${width}px: ${element}`));
      failures.push(...(await misalignedRows(page)).map((row) => `${width}px: misaligned ${row}`));
    }
    expect(failures).toEqual([]);
  });
}
