import type { PageAnchor } from "@/generated/types.gen";
import { RouteNavigation } from "@/navigation/route";

/** The query parameters that address one page of a listing. The API accepts at most one. */
export type PageAddress = { cursor?: string; offset?: number };

/**
 * Read the page a URL addresses, preferring `cursor` over `offset`.
 * Invalid offsets are skipped, so a hand-edited link still renders a page. Cursors are opaque;
 * the API answers a damaged one with `INVALID_PAGE_CURSOR`.
 */
export function readPageAddress(params: URLSearchParams, pageSize = 20): PageAddress {
  const cursor = params.get("cursor")?.trim() ?? "";
  if (cursor !== "") return { cursor };
  const rawOffset = params.get("offset")?.trim();
  const page = Number(params.get("page"));
  if (!rawOffset && (!Number.isSafeInteger(page) || page < 1)) return {};
  const offset = rawOffset ? Number(rawOffset) : (page - 1) * pageSize;
  return Number.isSafeInteger(offset) && offset >= 0 && offset <= 10_000 ? { offset } : {};
}

/** The localized link to the page an API anchor points to, keeping the URL's other parameters. */
export function pageHref(url: URL, anchor: PageAnchor): string {
  type Address = { cursor: string; offset: number; page: number };
  const nav = new RouteNavigation<Address>(url);
  return nav.update({ page: null, cursor: anchor.cursor ?? null, offset: anchor.offset ?? null });
}
