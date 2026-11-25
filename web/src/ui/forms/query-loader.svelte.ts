type QueryOptions<T> = {
  query: string;
  load: (query: string, signal: AbortSignal) => Promise<T>;
  debounceMs: number;
  /** Shorter queries, after trimming, pause loading. */
  minQueryLength?: number;
};

/**
 * Loads results for typed text, as `Autocomplete` and `Combobox` do: it waits for
 * `debounceMs` of quiet, and a new query aborts the request before it, so a slow stale
 * response never replaces a newer one. Create it while a component initializes; it reads
 * `options` in an effect, so every reactive value the getter reads restarts the load. The
 * getter returns `null` to pause, such as while the list is closed.
 *
 * `current` keeps the last result while the next one loads, so lists don't flicker, and is
 * cleared while paused. `status` is `loading` from the moment a request is sent, not during
 * the debounce, and `idle` while paused.
 */
export class QueryLoader<T> {
  current = $state<T>();
  status = $state<"idle" | "loading" | "ready" | "failed">("idle");

  constructor(options: () => QueryOptions<T> | null) {
    $effect(() => {
      const next = options();
      if (next === null || next.query.trim().length < (next.minQueryLength ?? 0)) {
        this.current = undefined;
        this.status = "idle";
        return;
      }
      const { query, load, debounceMs } = next;
      const controller = new AbortController();
      const timer = window.setTimeout(() => {
        this.status = "loading";
        load(query, controller.signal)
          .then((result) => {
            if (controller.signal.aborted) return;
            this.current = result;
            this.status = "ready";
          })
          .catch(() => {
            if (!controller.signal.aborted) this.status = "failed";
          });
      }, debounceMs);
      return () => {
        window.clearTimeout(timer);
        controller.abort();
      };
    });
  }
}
