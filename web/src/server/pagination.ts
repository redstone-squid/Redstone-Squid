import type { PageAnchor } from "@/generated/types.gen";
import { RouteNavigation } from "@/navigation/route";

const pageSize = 20;

/** Explicit offset zero selects numbered pagination on the first API request. */
export function readPageAddress(params: URLSearchParams) {
  const page = Number(params.get("page") ?? 1);
  const offset = (page - 1) * pageSize;
  return {
    offset: Number.isSafeInteger(page) && offset >= 0 && offset <= 10_000 ? offset : 0,
    page_size: pageSize,
  };
}

/** Keep filters and locale when following an offset-mode API anchor. */
export function pageHref(url: URL, anchor: PageAnchor): string {
  const page = (anchor.offset ?? 0) / pageSize + 1;
  const nav = new RouteNavigation<{ page: number; cursor: string; offset: number }>(url);
  return nav.update({ page: page === 1 ? null : page, cursor: null, offset: null });
}
