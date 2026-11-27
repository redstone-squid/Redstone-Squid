import type { PageAnchor } from "@/generated/types.gen";
import { RouteNavigation } from "@/navigation/route";

/** The query parameters that address one page of a listing. The API accepts at most one. */
export type PageAddress = { cursor?: string; offset?: number };

/**
 * Read the page a URL addresses, preferring `cursor` over `offset`.
 * Invalid offsets are skipped, so a hand-edited link still renders a page. Cursors are opaque;
 * the API answers a damaged one with `INVALID_PAGE_CURSOR`.
 */
export function readPageAddress(params: URLSearchParams): PageAddress {
  const cursor = params.get("cursor")?.trim() ?? "";
  if (cursor !== "") return { cursor };
  const offset = Number(params.get("offset")?.trim() ?? Number.NaN);
  return Number.isSafeInteger(offset) && offset >= 1 ? { offset } : {};
}

/** The localized link to the page an API anchor points to, keeping the URL's other parameters. */
export function pageHref(url: URL, anchor: PageAnchor): string {
  type Address = { cursor: string; offset: number };
  const nav = new RouteNavigation<Address>(url);
  return nav.update({ cursor: anchor.cursor ?? null, offset: anchor.offset ?? null });
}
