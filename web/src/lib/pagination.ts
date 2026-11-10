import type { PageQuery } from "./api";

/** Every query parameter that addresses a page, and so never belongs on a canonical URL. */
export const PAGE_PARAMS = ["offset", "after_id", "before_id"] as const;

function positiveInteger(value: string | null): number | undefined {
  if (value === null) return undefined;
  const parsed = Number(value.trim());
  if (!Number.isSafeInteger(parsed) || parsed < 1) return undefined;
  return parsed;
}

/**
 * Select one positive integer page address, preferring after_id, then before_id, then offset.
 *
 * Values use JavaScript numeric parsing. Unsafe, invalid, or nonpositive values are skipped; if none
 * remain, return the first page so a hand-edited link can still render.
 */
export function readPageParams(params: URLSearchParams): PageQuery {
  const afterId = positiveInteger(params.get("after_id"));
  if (afterId !== undefined) return { afterId };
  const beforeId = positiveInteger(params.get("before_id"));
  if (beforeId !== undefined) return { beforeId };
  // Offset 0 is the first page, which is addressed by naming no parameter at all.
  const offset = positiveInteger(params.get("offset"));
  return offset === undefined ? {} : { offset };
}

/** Read the offset of an offset-only collection, such as ranked search. */
export function readOffset(params: URLSearchParams): number | undefined {
  return positiveInteger(params.get("offset"));
}

/** Whether a request asked for anything other than the first page, which should not be indexed. */
export function isPaged(query: PageQuery): boolean {
  return query.afterId !== undefined || query.beforeId !== undefined || Boolean(query.offset);
}
