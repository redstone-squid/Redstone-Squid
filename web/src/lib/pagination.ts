import type { PageQuery } from "./api";

/** Every query parameter that addresses a page, and so never belongs on a canonical URL. */
export const PAGE_PARAMS = ["offset", "after_id", "before_id"] as const;

function positiveInteger(
  value: string | null,
  { minimum }: { minimum: number },
): number | undefined {
  if (value === null) return undefined;
  const parsed = Number(value.trim());
  if (!Number.isInteger(parsed) || parsed < minimum) return undefined;
  return parsed;
}

/**
 * Read a page address from a URL, ignoring anything malformed.
 *
 * A bad value means a hand-edited or truncated link, and the first page is a better answer there
 * than an error page.
 */
export function readPageParams(params: URLSearchParams): PageQuery {
  const afterId = positiveInteger(params.get("after_id"), { minimum: 1 });
  if (afterId !== undefined) return { afterId };
  const beforeId = positiveInteger(params.get("before_id"), { minimum: 1 });
  if (beforeId !== undefined) return { beforeId };
  // Offset 0 is the first page, which is addressed by naming no parameter at all.
  const offset = positiveInteger(params.get("offset"), { minimum: 1 });
  return offset === undefined ? {} : { offset };
}

/** Read the offset of an offset-only collection, such as ranked search. */
export function readOffset(params: URLSearchParams): number | undefined {
  return positiveInteger(params.get("offset"), { minimum: 1 });
}

/** Whether a request asked for anything other than the first page, which should not be indexed. */
export function isPaged(query: PageQuery): boolean {
  return query.afterId !== undefined || query.beforeId !== undefined || Boolean(query.offset);
}
