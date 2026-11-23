import type { PageAnchor } from "@/generated/types.gen";

/** The query parameters that address one page of a listing. The API accepts at most one. */
export type PageAddress = { after_id?: number; before_id?: number; offset?: number };
const PAGE_PARAMS = ["after_id", "before_id", "offset"] as const;

function positiveInteger(value: string | null): number | undefined {
  if (value === null) return undefined;
  const parsed = Number(value.trim());
  return Number.isSafeInteger(parsed) && parsed >= 1 ? parsed : undefined;
}

/**
 * Read the page a URL addresses, preferring `after_id`, then `before_id`, then `offset`.
 * Invalid values are skipped, so a hand-edited link still renders a page.
 */
export function readPageAddress(params: URLSearchParams): PageAddress {
  for (const name of PAGE_PARAMS) {
    const value = positiveInteger(params.get(name));
    if (value !== undefined) return { [name]: value };
  }
  return {};
}

/** The URL of the page an API anchor points to, keeping the current URL's other parameters. */
export function pageHref(url: URL, anchor: PageAnchor): string {
  const target = new URL(url);
  for (const name of PAGE_PARAMS) target.searchParams.delete(name);
  for (const name of PAGE_PARAMS) {
    const value = anchor[name];
    if (value != null) {
      target.searchParams.set(name, String(value));
      break;
    }
  }
  return `${target.pathname}${target.search}`;
}
