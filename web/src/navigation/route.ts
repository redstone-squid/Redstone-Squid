import { localizePath } from "@/locales/locale";

type Value = string | number | readonly string[];
type Patch<State> = { [K in keyof State]?: State[K] | null };

export type FormDestination = {
  action: string;
  hidden: [string, string][];
  resetHref: string;
};

type Destination = { path: string; scope?: symbol };

function write(params: URLSearchParams, patch: Record<string, Value | null | undefined>) {
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      params.delete(key);
      for (const item of value) params.append(key, item);
    } else if (value === null) params.delete(key);
    else params.set(key, String(value));
  }
}

/** A feature binds its declared fields and resolved scope values to the rendered route. */
export class RouteNavigation<State extends Record<string, Value>> {
  constructor(
    private readonly url: URL,
    private readonly scope?: symbol,
    private readonly retained: Patch<State> = {},
  ) {}

  /** A fresh destination receives only the state shared with its scope. */
  link(destination: Destination, fragment = ""): string {
    const target = new URL(destination.path, this.url);
    if (this.scope !== undefined && this.scope === destination.scope)
      write(target.searchParams, this.retained);
    target.hash = fragment;
    return localizePath(target.pathname + target.search + target.hash);
  }

  private current(changes: Patch<State>): URL {
    const target = new URL(this.url);
    write(target.searchParams, this.retained);
    write(target.searchParams, changes);
    return target;
  }

  /** Keep unrelated entries, including repeated keys. Null removes a field. */
  update(changes: Patch<State>): string {
    const target = this.current(changes);
    return localizePath(target.pathname + target.search + target.hash);
  }

  /** Visible controls own fields. Fixed values survive both submission and reset. */
  form(fields: readonly (keyof State & string)[], fixed: Patch<State> = {}): FormDestination {
    const target = this.current(fixed);
    for (const field of fields) target.searchParams.delete(field);
    return {
      action: localizePath(target.pathname),
      hidden: [...target.searchParams],
      resetHref: localizePath(target.pathname + target.search + target.hash),
    };
  }
}
