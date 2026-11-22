import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildsGet, buildsList } from "../../src/generated/sdk.gen";
import type { BuildsListResponse } from "../../src/generated/types.gen";
import { ApiError, createApiClient, unwrap } from "../../src/server/api";
import type { RuntimeConfig } from "../../src/server/config";

const config: RuntimeConfig = {
  apiBaseUrl: "https://api.catalogue.test",
  siteUrl: "https://catalogue.test",
  discordCommunityUrl: "https://discord.gg/redstone",
  botInviteUrl: "https://discord.com/oauth2/authorize",
};
const emptyPage: BuildsListResponse = { items: [], total: 0, next: null, prev: null };
const fetchMock = vi.fn<typeof fetch>();

function lastRequest(): Request {
  const input = fetchMock.mock.calls.at(-1)?.[0];
  if (!(input instanceof Request)) throw new Error("No request was captured.");
  return input;
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

afterEach(() => vi.unstubAllGlobals());

describe("per-request API client", () => {
  it("sends the request locale and a timeout signal", async () => {
    fetchMock.mockResolvedValueOnce(Response.json(emptyPage));
    const client = createApiClient("zh-CN", config);
    const page = unwrap(await buildsList({ client, query: { after_id: 7 } }));
    expect(page).toEqual(emptyPage);
    const request = lastRequest();
    expect(new URL(request.url).href).toBe("https://api.catalogue.test/v1/builds?after_id=7");
    expect(request.headers.get("Accept-Language")).toBe("zh-CN");
    expect(request.signal).toBeInstanceOf(AbortSignal);
  });
});

describe("API errors", () => {
  it("preserves localized RFC 9457 detail and the Request-Id reference", async () => {
    fetchMock.mockResolvedValueOnce(
      Response.json(
        { title: "未找到作品", status: 404, detail: "此作品不存在或未公开。" },
        {
          status: 404,
          headers: { "Content-Type": "application/problem+json", "Request-Id": "error-123" },
        },
      ),
    );
    const client = createApiClient("zh-CN", config);
    const result = await buildsGet({ client, path: { build_id: 404 } });
    expect(() => unwrap(result)).toThrow(ApiError);
    expect(() => unwrap(result)).toThrow(
      expect.objectContaining({
        status: 404,
        message: "此作品不存在或未公开。",
        requestId: "error-123",
      }),
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
