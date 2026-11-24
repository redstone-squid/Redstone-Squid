import { QueryClient } from "@tanstack/svelte-query";

let browserClient: QueryClient | undefined;

// A failure that carries a 4xx status, such as the application's API errors, will fail again.
function retryable(failures: number, error: unknown): boolean {
  const status =
    typeof error === "object" && error !== null && "status" in error ? error.status : undefined;
  return failures < 2 && !(typeof status === "number" && status >= 400 && status < 500);
}

/**
 * The page's QueryClient. Each island is its own Svelte root, so context cannot share a client
 * between them; this module can, because the browser evaluates it once per page. Pass it to
 * `QueryClientProvider` at the island's root.
 *
 * On the server every call returns a new client, so one visitor's data never reaches another's
 * render. Mutations never retry on their own: a write retries only with its idempotency key,
 * which the caller owns.
 */
export function pageQueryClient(): QueryClient {
  const client =
    (import.meta.env.SSR ? undefined : browserClient) ??
    new QueryClient({
      defaultOptions: {
        queries: { retry: retryable },
        mutations: { retry: false },
      },
    });
  if (!import.meta.env.SSR) browserClient = client;
  return client;
}
