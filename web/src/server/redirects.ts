/**
 * Permanent redirects for routes that moved, as unprefixed paths.
 *
 * When a page moves, add `old path → new path` here instead of deleting the old URL; links to
 * it live on in chats, bookmarks, and search results. `:name` segments carry over, so
 * `"/build/:id": "/builds/:id"` redirects `/build/7` to `/builds/7`. Middleware answers with a
 * 308 in the request's locale and keeps the query string. Never remove an entry; tests check
 * that every target is a real route and that redirects do not chain.
 */
export const REDIRECTS: Readonly<Record<string, string>> = {
  "/design-system/media": "/design-system/data",
};

export type CompiledRedirect = { pattern: RegExp; target: string };

const SEGMENT = /:([a-z][a-zA-Z0-9]*)/g;

export function compileRedirects(table: Readonly<Record<string, string>>): CompiledRedirect[] {
  return Object.entries(table).map(([source, target]) => ({
    pattern: new RegExp(
      `^${source.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(SEGMENT, "(?<$1>[^/]+)")}/?$`,
    ),
    target,
  }));
}

const compiled = compileRedirects(REDIRECTS);

/** Return where an unprefixed path has moved, or undefined if it has not. */
export function findRedirect(
  path: string,
  table: readonly CompiledRedirect[] = compiled,
): string | undefined {
  for (const { pattern, target } of table) {
    const match = pattern.exec(path);
    if (match)
      return target.replace(SEGMENT, (_segment, name: string) => match.groups?.[name] ?? "");
  }
  return undefined;
}

/**
 * List problems with a redirect table, given the site's route patterns in the same `:name`
 * form: targets that are not routes, targets that redirect again, sources that shadow a route,
 * and targets that use a parameter their source lacks.
 */
export function redirectProblems(
  table: Readonly<Record<string, string>>,
  routes: readonly string[],
): string[] {
  const shape = (path: string) => path.replace(SEGMENT, ":");
  const routeShapes = new Set(routes.map(shape));
  // A literal target such as /design-system/data is served by a route like /design-system/:group.
  const routePatterns = compileRedirects(Object.fromEntries(routes.map((route) => [route, route])));
  const isRoute = (target: string) =>
    routeShapes.has(shape(target)) || routePatterns.some(({ pattern }) => pattern.test(target));
  const sources = new Set(Object.keys(table).map(shape));
  const parameters = (path: string) => [...path.matchAll(SEGMENT)].map((match) => match[1]);
  return Object.entries(table).flatMap(([source, target]) => [
    ...(isRoute(target) ? [] : [`${source}: target ${target} is not a route`]),
    ...(sources.has(shape(target)) ? [`${source}: target ${target} redirects again`] : []),
    ...(routeShapes.has(shape(source)) ? [`${source}: shadows an existing route`] : []),
    ...parameters(target)
      .filter((name) => !parameters(source).includes(name))
      .map((name) => `${source}: target uses :${String(name)}, which the source lacks`),
  ]);
}
