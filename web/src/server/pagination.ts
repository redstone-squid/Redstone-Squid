import type { PageAnchor } from "@/generated/types.gen";
import { RouteNavigation } from "@/navigation/route";

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

/** The localized link to the page an API anchor points to, keeping the URL's other parameters. */
export function pageHref(url: URL, anchor: PageAnchor): string {
  const changes = { after_id: null, before_id: null, offset: null } as Record<
    keyof PageAddress,
    number | null
  >;
  for (const name of PAGE_PARAMS) {
    if (anchor[name] != null) {
      changes[name] = anchor[name];
      break;
    }
  }
  return new RouteNavigation<Required<PageAddress>>(url).update(changes);
}
