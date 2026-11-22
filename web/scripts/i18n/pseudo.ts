// Accented look-alikes keep pseudo text readable while making untranslated text obvious.
// Each accented letter is a single UTF-16 unit, so the two strings line up by index.
const PLAIN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
const ACCENTED = "åƀçđéƒĝĥîĵķļɱñöþǫŕšţûṽŵẋýžÅƁÇĐÉƑĜĤÎĴĶĻṀÑÖÞǪŔŠŢÛṼŴẊÝŽ";

/** Replace ASCII letters with accented look-alikes. */
export function accent(text: string): string {
  return text.replace(/[A-Za-z]/g, (letter) => ACCENTED.charAt(PLAIN.indexOf(letter)));
}

/**
 * Padding that brings pseudo text to about 140% of the English length. Pseudo messages are
 * `[` + text + padding + `]`, so clipped text loses its closing bracket and fixed-width layouts
 * overflow during review.
 */
export function padding(englishLength: number): string {
  return "·".repeat(Math.ceil(englishLength * 0.4));
}

/** The pseudo form of plain text. */
export function pseudoText(text: string): string {
  return `[${accent(text)}${padding(text.length)}]`;
}
