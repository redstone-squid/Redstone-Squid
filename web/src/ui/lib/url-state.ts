import { isEqual } from "es-toolkit";
import { z } from "zod";

/**
 * A listing's query-string state. Give every field a `.catch(default)`, so an invalid or
 * missing value falls back instead of failing, and so parsing nothing yields the defaults:
 *
 *     const results = z.object({
 *       q: z.string().catch(""),
 *       sort: z.enum(["newest", "title"]).catch("newest"),
 *       tag: z.array(z.string()).catch([]),
 *     });
 */
export type UrlSchema = z.ZodObject<Record<string, z.ZodType>>;

/** How a change is recorded: `push` for query, filter, sort and page; `replace` for view, columns and expansion. */
export type HistoryMode = "push" | "replace";

function isList(field: z.ZodType): boolean {
  let inner: z.core.SomeType = field;
  while (
    inner instanceof z.ZodCatch ||
    inner instanceof z.ZodDefault ||
    inner instanceof z.ZodOptional
  ) {
    inner = inner.unwrap();
  }
  return inner instanceof z.ZodArray;
}

/** Read the schema's fields from a query string. Array fields collect a repeated parameter. */
export function readUrlState<S extends UrlSchema>(schema: S, params: URLSearchParams): z.infer<S> {
  const raw = Object.fromEntries(
    Object.entries(schema.shape).map(([key, field]) => [
      key,
      isList(field) ? params.getAll(key) : (params.get(key) ?? undefined),
    ]),
  );
  return schema.parse(raw);
}

/**
 * The query string for `state`, omitting every field at its default so URLs stay canonical.
 * Parameters the schema does not own are kept from `current`.
 */
export function writeUrlState<S extends UrlSchema>(
  schema: S,
  state: z.infer<S>,
  current = new URLSearchParams(),
): URLSearchParams {
  const defaults: Record<string, unknown> = schema.parse({});
  const params = new URLSearchParams(current);
  for (const [key, value] of Object.entries(state)) {
    params.delete(key);
    if (isEqual(value, defaults[key])) continue;
    for (const item of Array.isArray(value) ? value : [value]) params.append(key, String(item));
  }
  return params;
}

/**
 * Put `state` in the address bar without navigating. The path, including its locale prefix,
 * and the hash stay as they are.
 */
export function commitUrlState<S extends UrlSchema>(
  schema: S,
  state: z.infer<S>,
  mode: HistoryMode,
): void {
  const url = new URL(location.href);
  url.search = writeUrlState(schema, state, url.searchParams).toString();
  if (url.href === location.href) return;
  if (mode === "push") history.pushState(history.state, "", url);
  else history.replaceState(history.state, "", url);
}
