import { http, HttpResponse, type JsonBodyType } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { buildsGet, buildsList } from "@/generated/sdk.gen";
import type { BuildsListResponse } from "@/generated/types.gen";
import { ApiError, createApiClient, unwrap } from "@/server/api";
import type { RuntimeConfig } from "@/server/config";

const API = "https://api.catalogue.test";
const config: RuntimeConfig = {
  apiBaseUrl: API,
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
  return buildsGet({ client: createApiClient("zh-CN", config), path: { build_id: 404 } });
}

describe("per-request API client", () => {
  it("sends the request locale and a timeout signal", async () => {
    let request: Request | undefined;
    server.use(
      http.get(`${API}/v1/builds`, (info) => {
        request = info.request;
        return HttpResponse.json(emptyPage);
      }),
    );
    const client = createApiClient("zh-CN", config);
    expect(unwrap(await buildsList({ client, query: { after_id: 7 } }))).toEqual(emptyPage);
    expect(request?.url).toBe(`${API}/v1/builds?after_id=7`);
    expect(request?.headers.get("Accept-Language")).toBe("zh-CN");
    expect(request?.signal).toBeInstanceOf(AbortSignal);
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

  it("wraps unknown failures as a service error and keeps known failures", () => {
    const known = new ApiError(429, { title: "Slow down", status: 429 });
    expect(ApiError.from(known)).toBe(known);
    const unknown = ApiError.from(new TypeError("socket closed"));
    expect(unknown.status).toBe(503);
    expect(unknown.problem).toBeUndefined();
    expect(unknown.cause).toBeInstanceOf(TypeError);
  });
});
