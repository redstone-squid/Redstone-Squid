import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { ApiError } from "@/api-client/api-error";
import { BrowserApi, errorMessage } from "@/api-client/mutation";
import { accountProfileUpdate } from "@/generated/sdk.gen";
import * as m from "@/locales/messages.gen";

const API = "https://catalogue.test";
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const update = (api: BrowserApi) => (headers: Record<string, string>) =>
  accountProfileUpdate({ client: api.client, headers, body: { bio: "Pistons" } });

describe("browser writes", () => {
  it("sends CSRF, Idempotency-Key and If-Match, and fetches the token once per page", async () => {
    let tokenFetches = 0;
    const seen: Headers[] = [];
    server.use(
      http.get(`${API}/v1/auth/csrf`, () => {
        tokenFetches += 1;
        return HttpResponse.json({ csrf_token: "token-1234567890ab" });
      }),
      http.patch(`${API}/v1/accounts/me/profile`, ({ request }) => {
        seen.push(request.headers);
        return HttpResponse.json({}, { headers: { ETag: '"account-7-r3"' } });
      }),
    );
    const api = new BrowserApi(API);
    const first = await api.mutate(update(api), { idempotencyKey: "k1", etag: '"account-7-r2"' });
    await api.mutate(update(api), { idempotencyKey: "k2" });

    expect(first.etag).toBe('"account-7-r3"');
    expect(tokenFetches).toBe(1);
    expect(seen[0]?.get("CSRF-Token")).toBe("token-1234567890ab");
    expect(seen[0]?.get("Idempotency-Key")).toBe("k1");
    expect(seen[0]?.get("If-Match")).toBe('"account-7-r2"');
    expect(seen[1]?.has("If-Match")).toBe(false);
  });

  it("asks for the CSRF token again after fetching it failed", async () => {
    let tokenFetches = 0;
    server.use(
      http.get(`${API}/v1/auth/csrf`, () => {
        tokenFetches += 1;
        return tokenFetches === 1
          ? HttpResponse.json({ title: "Unauthorized", status: 401 }, { status: 401 })
          : HttpResponse.json({ csrf_token: "token-1234567890ab" });
      }),
      http.patch(`${API}/v1/accounts/me/profile`, () => HttpResponse.json({})),
    );
    const api = new BrowserApi(API);
    await expect(api.mutate(update(api), { idempotencyKey: "k" })).rejects.toMatchObject({
      status: 401,
    });
    await expect(api.mutate(update(api), { idempotencyKey: "k" })).resolves.toBeDefined();
  });

  it("reports a write that got no response as offline, so callers retry with the same key", async () => {
    server.use(
      http.get(`${API}/v1/auth/csrf`, () =>
        HttpResponse.json({ csrf_token: "token-1234567890ab" }),
      ),
      http.patch(`${API}/v1/accounts/me/profile`, () => HttpResponse.error()),
    );
    const api = new BrowserApi(API);
    await expect(api.mutate(update(api), { idempotencyKey: "k" })).rejects.toMatchObject({
      offline: true,
    });
  });
});

describe("error messages", () => {
  const problem = (code: string | null, detail?: string) =>
    new ApiError(400, { title: "Failed", status: 400, code, detail });

  it.each([
    [
      "an internal code, hiding the server's detail",
      problem("INTERNAL_ERROR", "trace"),
      m.errorGeneric,
    ],
    [
      "a code whose detail is the message",
      problem("LAST_IDENTITY", "Keep one sign-in."),
      () => "Keep one sign-in.",
    ],
    ["a code this build does not know", problem("A_FUTURE_CODE", "New rule."), () => "New rule."],
    ["no code or detail", problem(null), m.errorGeneric],
    ["a lost connection", new ApiError(503, undefined, { offline: true }), m.errorOffline],
    [
      "a revision conflict",
      new ApiError(412, { title: "x", status: 412, code: "REVISION_CONFLICT" }),
      m.errorConflict,
    ],
    // Messages are read inside the test, once the page locale is set.
  ])("explains %s", (_case, error, message) => {
    expect(errorMessage(error)).toBe(message());
  });
});
