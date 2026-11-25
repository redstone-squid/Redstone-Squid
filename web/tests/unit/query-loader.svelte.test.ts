import { flushSync } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryLoader } from "@/ui/forms/query-loader.svelte";

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

/** A loader driven by a reactive query, as a component would drive it from its input. */
function setup(load: (query: string, signal: AbortSignal) => Promise<string[]>) {
  const input = $state<{ query: string | null }>({ query: null });
  // $effect.root runs its callback synchronously, so this is assigned before setup returns.
  let loader!: QueryLoader<string[]>;
  const destroy = $effect.root(() => {
    loader = new QueryLoader(() =>
      input.query === null
        ? null
        : { query: input.query, load, debounceMs: 250, minQueryLength: 2 },
    );
  });
  const type = (query: string | null) => {
    input.query = query;
    flushSync();
  };
  return { loader, type, destroy };
}

describe("QueryLoader", () => {
  it("loads once after the debounce, for the last query only", async () => {
    const load = vi.fn((query: string) => Promise.resolve([query]));
    const { loader, type, destroy } = setup(load);
    type("pi");
    await vi.advanceTimersByTimeAsync(200);
    type("pis");
    await vi.advanceTimersByTimeAsync(200);
    expect(load).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(50);
    expect(load).toHaveBeenCalledExactlyOnceWith("pis", expect.any(AbortSignal));
    expect(loader.current).toEqual(["pis"]);
    expect(loader.status).toBe("ready");
    destroy();
  });

  it("aborts a stale request and ignores its late response", async () => {
    const pending: { signal: AbortSignal; resolve: (items: string[]) => void }[] = [];
    const { loader, type, destroy } = setup(
      (_query, signal) =>
        new Promise((resolve) => {
          pending.push({ signal, resolve });
        }),
    );
    type("piston");
    await vi.advanceTimersByTimeAsync(250);
    expect(loader.status).toBe("loading");
    type("pistons");
    expect(pending[0]?.signal.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(250);
    pending[1]?.resolve(["new"]);
    pending[0]?.resolve(["stale"]);
    await vi.runAllTimersAsync();
    expect(loader.current).toEqual(["new"]);
    destroy();
  });

  it("keeps the last result while the next loads and reports failures", async () => {
    const load = vi.fn((query: string) =>
      query === "bad" ? Promise.reject(new Error("offline")) : Promise.resolve([query]),
    );
    const { loader, type, destroy } = setup(load);
    type("ok");
    await vi.advanceTimersByTimeAsync(250);
    type("bad");
    await vi.advanceTimersByTimeAsync(250);
    expect(loader.status).toBe("failed");
    expect(loader.current).toEqual(["ok"]);
    destroy();
  });

  it("pauses and clears below minQueryLength or when the getter returns null", async () => {
    const load = vi.fn((query: string) => Promise.resolve([query]));
    const { loader, type, destroy } = setup(load);
    type("p");
    await vi.advanceTimersByTimeAsync(250);
    expect(load).not.toHaveBeenCalled();
    type("pi");
    await vi.advanceTimersByTimeAsync(250);
    expect(loader.current).toEqual(["pi"]);
    type(" p ");
    expect(loader.current).toBeUndefined();
    expect(loader.status).toBe("idle");
    type("pi");
    type(null);
    await vi.advanceTimersByTimeAsync(250);
    expect(load).toHaveBeenCalledTimes(1);
    destroy();
  });

  it("aborts the request in flight when its owner is destroyed", async () => {
    let signal: AbortSignal | undefined;
    const { type, destroy } = setup((_query, next) => {
      signal = next;
      return new Promise(() => {
        // Never settles, like a request still in flight.
      });
    });
    type("piston");
    await vi.advanceTimersByTimeAsync(250);
    destroy();
    expect(signal?.aborted).toBe(true);
  });
});
