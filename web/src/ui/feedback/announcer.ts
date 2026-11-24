let timer: ReturnType<typeof setTimeout> | undefined;

/**
 * Reads `text` to screen reader users through the page's `Announcer`, such as a new result
 * count after a refetch. Use it only for messages with no visible home; a form's outcome
 * belongs in its own status region. It writes to the server-rendered region directly, so the
 * layout ships no JavaScript for it.
 */
export function announce(text: string): void {
  const region = document.querySelector("[data-announcer]");
  if (region === null) return;
  // Clear first and set the text a moment later, so repeating the same message is announced.
  clearTimeout(timer);
  region.textContent = "";
  timer = setTimeout(() => {
    region.textContent = text;
  }, 100);
}
