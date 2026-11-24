/**
 * Focus an element that may not be interactive, such as a step or list heading.
 * It gains `tabindex="-1"` so a script can focus it while Tab still skips it.
 */
export function focusTarget(element: HTMLElement): void {
  if (!element.hasAttribute("tabindex") && element.tabIndex < 0) element.tabIndex = -1;
  element.focus();
}

/**
 * Move focus after removing an item, so it never falls back to the page.
 *
 * @param controls - Each remaining item's primary control, in order, after the removal.
 * @param index - Where the removed item was.
 * @param fallback - Used when no item is left, usually the list heading or its add button.
 */
export function focusAfterRemoval(
  controls: readonly (HTMLElement | null | undefined)[],
  index: number,
  fallback: HTMLElement | null | undefined,
): void {
  const target = controls[index] ?? controls[index - 1] ?? fallback;
  if (target) focusTarget(target);
}
