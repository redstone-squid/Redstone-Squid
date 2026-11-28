import { http, HttpResponse, type JsonBodyType } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { buildsGet, buildsList } from "@/generated/sdk.gen";
import type { BuildsListResponse } from "@/generated/types.gen";
import { ApiError, unwrap } from "@/api-client/api-error";
import { createApiClient, forwardedFor, load } from "@/server/api";
import type { RuntimeConfig } from "@/server/config";

const API = "https://api.catalogue.test";
const config: RuntimeConfig = {
  apiBaseUrl: "https://catalogue.test",
  apiInternalUrl: API,
  siteUrl: "https://catalogue.test",
  discordCommunityUrl: "https://discord.gg/redstone",
  botInviteUrl: "https://discord.com/oauth2/authorize",
};
const emptyPage: BuildsListResponse = { items: [], total: 0, next: null, prev: null };

// Each test registers the responses it needs; unhandled requests fail the test.
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** Answer `GET /v1/builds/:id` with an error body, as the API would for a failed lookup. */
function failBuildLookup(status: number, body: JsonBodyType, headers: Record<string, string> = {}) {
  server.use(
    http.get(`${API}/v1/builds/:id`, () =>
      HttpResponse.json(body, {
        status,
        headers: { "Content-Type": "application/problem+json", ...headers },
      }),
    ),
  );
  return buildsGet({
    client: createApiClient("zh-CN", config, undefined),
    path: { build_id: 404 },
  });
}

describe("per-request API client", () => {
  it("sends the request locale and query to the API", async () => {
    let request: Request | undefined;
    server.use(
      http.get(`${API}/v1/builds`, (info) => {
        request = info.request;
        return HttpResponse.json(emptyPage);
      }),
    );
    const client = createApiClient("zh-CN", config, "203.0.113.9, 172.18.0.2");
    expect(unwrap(await buildsList({ client, query: { cursor: "abc" } }))).toEqual(emptyPage);
    expect(request?.url).toBe(`${API}/v1/builds?cursor=abc`);
    expect(request?.headers.get("Accept-Language")).toBe("zh-CN");
    expect(request?.headers.get("X-Forwarded-For")).toBe("203.0.113.9, 172.18.0.2");
  });

  it("appends the peer to the forwarded chain, as a proxy does", () => {
    const request = (header?: string) =>
      new Request("https://catalogue.test/", {
        headers: header === undefined ? {} : { "X-Forwarded-For": header },
      });
    // A spoofed first entry stays in the chain; the API reads from the trusted right end.
    expect(forwardedFor(request("198.51.100.1, 203.0.113.9"), "172.18.0.2")).toBe(
      "198.51.100.1, 203.0.113.9, 172.18.0.2",
    );
    expect(forwardedFor(request(), "172.18.0.2")).toBe("172.18.0.2");
    expect(forwardedFor(request(" "), undefined)).toBeUndefined();
  });

  it("returns a service error when the API deadline expires", async () => {
    const started = Promise.withResolvers<undefined>();
    const release = Promise.withResolvers<undefined>();
    server.use(
      http.get(`${API}/v1/builds`, async () => {
        started.resolve(undefined);
        await release.promise;
        return HttpResponse.json(emptyPage);
      }),
    );
    const deadline = new AbortController();
    // Hold the response so the deadline expires while fetch is waiting for the API.
    const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValueOnce(deadline.signal);
    try {
      const pending = buildsList({ client: createApiClient("en", config, undefined) });
      await started.promise;
      deadline.abort(new DOMException("Deadline expired", "TimeoutError"));
      release.resolve(undefined);
      const result = await pending;
      expect(() => unwrap(result)).toThrow(expect.objectContaining({ status: 503 }));
    } finally {
      release.resolve(undefined);
      timeout.mockRestore();
    }
  });
});

describe("API errors", () => {
  it("preserves localized RFC 9457 detail and the Request-Id reference", async () => {
    const result = await failBuildLookup(
      404,
      { title: "未找到作品", status: 404, detail: "此作品不存在或未公开。" },
      { "Request-Id": "error-123" },
    );
    expect(() => unwrap(result)).toThrow(
      expect.objectContaining({
        status: 404,
        message: "此作品不存在或未公开。",
        requestId: "error-123",
      }),
    );
  });

  it("keeps application codes a newer API introduces", async () => {
    const problem = {
      title: "Request refused",
      status: 409,
      code: "A_FUTURE_CODE",
      context: { expected: 3, actual: 4 },
    };
    const result = await failBuildLookup(409, problem);
    expect(() => unwrap(result)).toThrow(expect.objectContaining({ problem }));
  });

  it.each([
    { title: 123, status: 400 },
    { title: "Failure", status: 400.5 },
    { title: "Failure", status: 600 },
    { title: "Failure", status: 400, code: [] },
    { title: "Failure", status: 400, context: [] },
    "Failure",
  ])("ignores a malformed error body and keeps the HTTP status: %j", async (body) => {
    const result = await failBuildLookup(400, body);
    expect(() => unwrap(result)).toThrow(
      expect.objectContaining({ status: 400, problem: undefined }),
    );
  });

  it("keeps typed field issues and drops ones with a code this build cannot format", () => {
    const tooLong = { field: "/body/bio", code: "too_long", params: { maximum: 280 } };
    const error = new ApiError(422, {
      title: "Invalid request",
      status: 422,
      context: {
        fields: [tooLong, { field: "title", code: "a_future_code" }, { code: "required" }],
      },
    });
    expect(error.fieldErrors).toEqual([tooLong]);
  });

  it.each([
    ["REVISION_CONFLICT", true],
    ["DOMAIN_ERROR", false],
    [undefined, false],
  ])("treats code %s as a revision conflict: %s", (code, conflict) => {
    expect(new ApiError(412, { title: "x", status: 412, code }).conflict).toBe(conflict);
  });

  it("wraps unknown failures as a service error and keeps known failures", () => {
    const known = new ApiError(429, { title: "Slow down", status: 429 });
    expect(ApiError.from(known)).toBe(known);
    const unknown = ApiError.from(new TypeError("socket closed"));
    expect(unknown.status).toBe(503);
    expect(unknown.problem).toBeUndefined();
    expect(unknown.cause).toBeInstanceOf(TypeError);
  });
});

describe("load", () => {
  const client = () => createApiClient("en", config, undefined);

  it("returns the data of a successful call", async () => {
    server.use(http.get(`${API}/v1/builds`, () => HttpResponse.json(emptyPage)));
    expect(await load(buildsList({ client: client() }))).toEqual(emptyPage);
  });

  it("keeps HTTP statuses and reports a network failure as 503", async () => {
    server.use(
      http.get(`${API}/v1/builds/404`, () =>
        HttpResponse.json({ title: "Not found", status: 404 }, { status: 404 }),
      ),
      http.get(`${API}/v1/builds/500`, () =>
        HttpResponse.json({ title: "Broken", status: 500 }, { status: 500 }),
      ),
      http.get(`${API}/v1/builds/0`, () => HttpResponse.error()),
    );
    const lookup = (build_id: number) => load(buildsGet({ client: client(), path: { build_id } }));
    await expect(lookup(404)).rejects.toMatchObject({ status: 404 });
    await expect(lookup(500)).rejects.toMatchObject({ status: 500 });
    await expect(lookup(0)).rejects.toMatchObject({ status: 503 });
  });
});
