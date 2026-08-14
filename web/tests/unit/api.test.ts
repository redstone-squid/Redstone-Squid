import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  BuildDetail,
  BuildSummary,
  PageBuildSummary,
  PageRecordSummary,
  PageSchematicSummary,
  RecordDetail,
} from "../../src/generated/types.gen";
import {
  asCatalogueError,
  CatalogueApiError,
  createCatalogueClient,
  fetchBuild,
  fetchBuilds,
  fetchRecord,
  fetchRecords,
  fetchSchematics,
  fetchSearch,
  fetchSuggestions,
} from "../../src/lib/api";
import type { RuntimeConfig } from "../../src/lib/config";

const config: RuntimeConfig = {
  apiBaseUrl: "https://api.catalogue.test",
  siteUrl: "https://catalogue.test",
  discordCommunityUrl: "https://discord.gg/redstone",
  botInviteUrl: "https://discord.com/oauth2/authorize",
};

const summary: BuildSummary = {
  id: 1,
  revision: 2,
  title: "Compact Door",
  display_name: null,
  status: "confirmed",
  category: "door",
  dimensions: { width: 5, height: 6, depth: 7 },
  creators: ["Builder"],
  tags: [],
  preview: null,
  version_spec: null,
  versions: ["Java 1.21"],
  opening_time: 8,
  closing_time: 9,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-02T00:00:00Z",
};
const build: BuildDetail = {
  ...summary,
  details: {
    category: "Door",
    door_dimensions: { width: 3, height: 3, depth: 1 },
    orientation: "Door",
    patterns: ["Flush"],
    visible_opening_time: null,
    visible_closing_time: null,
  },
  restrictions: {},
  description: "A test build.",
  links: { images: [], videos: [], world_downloads: [], schematics: [], renders: [] },
};
const record: RecordDetail = {
  id: 3,
  definition_id: 2,
  competition_id: "11111111-1111-1111-1111-111111111111",
  title: "Fastest door",
  subtitle: null,
  record_class: "fastest",
  build_kind: "door",
  version_scope: "all_time",
  status: "active",
  holder_build_ids: [1],
  computed_at: "2026-08-10T00:00:00Z",
  holder_builds: [summary],
};

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200, contentType = "application/json"): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": contentType } });
}

function requestedUrl(call = fetchMock.mock.calls.at(-1)): URL {
  if (!call) throw new Error("No fetch request was captured.");
  const input = call[0];
  return new URL(input instanceof Request ? input.url : String(input));
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

afterEach(() => vi.unstubAllGlobals());

describe("per-request API client", () => {
  it("uses isolated clients with the active API locale", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        items: [summary],
        total: 1,
        next: null,
        prev: null,
      } satisfies PageBuildSummary),
    );
    const client = createCatalogueClient("zh-CN", config);
    const result = await client.get({ url: "/v1/builds" });
    expect(result.data).toEqual({ items: [summary], total: 1, next: null, prev: null });
    const request = fetchMock.mock.calls[0]?.[0];
    expect(request).toBeInstanceOf(Request);
    expect((request as Request).headers.get("Accept-Language")).toBe("zh-CN");
    expect((request as Request).signal).toBeInstanceOf(AbortSignal);
  });
});

describe("typed catalogue reads", () => {
  it("maps build query options and enforces the page-size cap", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        items: [summary],
        total: 9,
        next: { offset: null, after_id: 3, before_id: null },
        prev: null,
      } satisfies PageBuildSummary),
    );
    await expect(
      fetchBuilds("en", { q: "door", sort: "-id", afterId: 7, pageSize: 200 }),
    ).resolves.toMatchObject({ total: 9 });
    const url = requestedUrl();
    expect(url.pathname).toBe("/v1/builds");
    expect(url.searchParams.get("q")).toBe("door");
    expect(url.searchParams.get("sort")).toBe("-id");
    expect(url.searchParams.get("after_id")).toBe("7");
    expect(url.searchParams.get("page_size")).toBe("50");
  });

  it("sends only the parameter that addresses the page", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ items: [], total: 0, next: null, prev: null } satisfies PageBuildSummary),
    );
    // The API rejects a combination outright, so the client must never assemble one.
    await fetchBuilds("en", { offset: 40, afterId: 7, beforeId: 3 });
    const url = requestedUrl();
    expect(url.searchParams.get("after_id")).toBe("7");
    expect(url.searchParams.has("offset")).toBe(false);
    expect(url.searchParams.has("before_id")).toBe(false);
  });

  it("addresses a page backwards or by offset when no forward anchor is given", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ items: [], total: 0, next: null, prev: null } satisfies PageBuildSummary),
      )
      .mockResolvedValueOnce(
        jsonResponse({ items: [], total: 0, next: null, prev: null } satisfies PageBuildSummary),
      )
      .mockResolvedValueOnce(
        jsonResponse({ items: [], total: 0, next: null, prev: null } satisfies PageBuildSummary),
      );
    await fetchBuilds("en", { beforeId: 3, offset: 40 });
    expect(requestedUrl().searchParams.get("before_id")).toBe("3");
    await fetchBuilds("en", { offset: 40 });
    expect(requestedUrl().searchParams.get("offset")).toBe("40");
    await fetchBuilds("en", { offset: 0 });
    expect(requestedUrl().searchParams.has("offset")).toBe(false);
  });

  it("loads build, record, record page, and schematic detail shapes", async () => {
    const recordPage: PageRecordSummary = { items: [record], total: 1, next: null, prev: null };
    const schematicPage: PageSchematicSummary = {
      items: [],
      total: 0,
      next: null,
      prev: null,
    };
    fetchMock
      .mockResolvedValueOnce(jsonResponse(build))
      .mockResolvedValueOnce(jsonResponse(recordPage))
      .mockResolvedValueOnce(jsonResponse(record))
      .mockResolvedValueOnce(jsonResponse(schematicPage));
    await expect(fetchBuild("en", 1)).resolves.toEqual(build);
    expect(requestedUrl().pathname).toBe("/v1/builds/1");
    await expect(fetchRecords("en", { afterId: 12, pageSize: 100 })).resolves.toEqual(recordPage);
    expect(requestedUrl().searchParams.get("after_id")).toBe("12");
    expect(requestedUrl().searchParams.get("page_size")).toBe("50");
    await expect(fetchRecord("en", 3)).resolves.toEqual(record);
    expect(requestedUrl().pathname).toBe("/v1/records/3");
    await expect(fetchSchematics("en", 1)).resolves.toEqual(schematicPage);
    expect(requestedUrl().pathname).toBe("/v1/builds/1/schematics");
  });

  it("loads cross-resource results and suggestions", async () => {
    const searchPage = {
      items: [{ resource_kind: "build" as const, score: 1, build: summary }],
      total: 1,
      next: null,
      prev: null,
    };
    fetchMock
      .mockResolvedValueOnce(jsonResponse(searchPage))
      .mockResolvedValueOnce(jsonResponse({ suggestions: ["door", "door type"] }));
    await expect(
      fetchSearch("zh-CN", { q: "door", scope: "all", sort: "width", offset: 20, pageSize: 80 }),
    ).resolves.toEqual(searchPage);
    expect(requestedUrl().searchParams.get("page_size")).toBe("50");
    expect(requestedUrl().searchParams.get("offset")).toBe("20");
    await expect(fetchSuggestions("zh-CN", "do")).resolves.toEqual({
      suggestions: ["door", "door type"],
    });
    expect(requestedUrl().searchParams.get("limit")).toBe("8");
  });
});

describe("problem response mapping", () => {
  it("preserves localized RFC 9457 detail and reference IDs", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          title: "未找到作品",
          status: 404,
          detail: "此作品不存在或未公开。",
          error_id: "error-123",
        },
        404,
        "application/problem+json",
      ),
    );
    const error = await fetchBuild("zh-CN", 404).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(CatalogueApiError);
    expect(error).toMatchObject({
      status: 404,
      message: "此作品不存在或未公开。",
      problem: { error_id: "error-123" },
    });
  });

  it("redacts unknown failures into a service error and keeps known failures", () => {
    const known = new CatalogueApiError(429, { title: "Slow down", status: 429 });
    expect(asCatalogueError(known)).toBe(known);
    const unknown = asCatalogueError(new TypeError("socket closed"));
    expect(unknown.status).toBe(503);
    expect(unknown.problem).toBeUndefined();
    expect(unknown.cause).toBeInstanceOf(TypeError);
  });
});
