let message = $state("");
let timer: ReturnType<typeof setTimeout> | undefined;

/** The text `Announcer` shows. */
export const announcement = {
  get message(): string {
    return message;
  },
};

/**
 * Reads `text` to screen reader users through the page's `Announcer`, such as a new result
 * count after a refetch. Use it only for messages with no visible home; a form's outcome
 * belongs in its own status region.
 */
export function announce(text: string): void {
  // Clear first and set the text a moment later, so repeating the same message is announced.
  clearTimeout(timer);
  message = "";
  timer = setTimeout(() => {
    message = text;
  }, 100);
}
