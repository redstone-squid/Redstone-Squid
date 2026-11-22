/** Read a positive, safely representable offset. An absent or invalid offset selects the first page. */
export function readOffset(params: URLSearchParams): number | undefined {
  const value = params.get("offset");
  if (value === null) return undefined;
  const parsed = Number(value.trim());
  if (!Number.isSafeInteger(parsed) || parsed < 1) return undefined;
  return parsed;
}
