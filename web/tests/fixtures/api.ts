import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";

import type {
  BuildDetail,
  BuildSummary,
  PageAnnotatedUnionBuildSearchResultRecordSearchResultMetadataSearchResultFieldInfoAnnotationNoneTypeRequiredTrueDiscriminatorResourceKind as SearchPage,
  PageBuildSummary,
  PageRecordSummary,
  PageSchematicSummary,
  ProblemDetail,
  RecordDetail,
  RecordSummary,
  SearchSuggestions,
} from "../../src/generated/types.gen";

const port = Number(process.env.FIXTURE_API_PORT ?? 8787);
const frozenDate = "2026-08-10T12:00:00Z";

const builds = [
  {
    id: 1,
    revision: 3,
    title: "Copper Bolt 5×5",
    display_name: "Copper Bolt",
    status: "confirmed",
    category: "door",
    dimensions: { width: 9, height: 7, depth: 4 },
    creators: ["CircuitSage", "红石猫"],
    tags: [
      { key: "official_seamless", name: "Seamless", value: null, unit: null },
      { key: "closing_delay", name: "Closing delay", value: "4", unit: "gt" },
    ],
    preview: { kind: "render", url: "https://media.fixture.invalid/build-1.png" },
    version_spec: "Java 1.21+",
    versions: ["Java 1.21", "Java 1.21.1"],
    opening_time: 8,
    closing_time: 10,
    created_at: "2026-08-01T09:00:00Z",
    updated_at: frozenDate,
  },
  {
    id: 2,
    revision: 1,
    title: "Observerless Iris",
    display_name: null,
    status: "confirmed",
    category: "entrance",
    dimensions: { width: 12, height: 12, depth: 5 },
    creators: ["Space Builder"],
    tags: [],
    preview: { kind: "image", url: "https://media.fixture.invalid/broken.png" },
    version_spec: null,
    versions: ["Bedrock 1.21.50"],
    opening_time: 12,
    closing_time: 12,
    created_at: "2026-07-20T09:00:00Z",
    updated_at: null,
  },
  {
    id: 3,
    revision: 2,
    title: "Slim Piston Extender",
    display_name: null,
    status: "confirmed",
    category: "extender",
    dimensions: { width: 3, height: 5, depth: 8 },
    creators: ["CircuitSage"],
    tags: [],
    preview: null,
    version_spec: null,
    versions: ["Java 1.20"],
    opening_time: null,
    closing_time: null,
    created_at: "2026-06-10T09:00:00Z",
    updated_at: null,
  },
] satisfies [BuildSummary, BuildSummary, BuildSummary];

const detail: BuildDetail = {
  ...builds[0],
  door_dimensions: { width: 5, height: 5, depth: 1 },
  patterns: ["Flush", "Full seamless"],
  orientation: "Not directional",
  extension_length: null,
  extender_type: null,
  restrictions: {
    wiring: ["No observers"],
    component: ["No slime blocks"],
    miscellaneous: [],
  },
  description:
    "A compact showcase door built for reliable survival use. The input line is exposed at the rear and the reset is fully automatic.",
  links: {
    images: ["https://media.fixture.invalid/build-1-alt.png", "http://unsafe.fixture/image.png"],
    videos: ["https://video.example.org/watch/door"],
    world_downloads: ["https://downloads.example.org/copper-bolt.zip"],
    schematics: ["https://downloads.example.org/copper-bolt.litematic"],
    renders: ["https://media.fixture.invalid/build-1.png"],
  },
};

const records = [
  {
    id: 11,
    definition_id: 4,
    competition_id: "11111111-1111-1111-1111-111111111111",
    title: "Fastest 5×5 seamless door",
    subtitle: "Java, all time",
    record_class: "fastest_smallest",
    build_kind: "door",
    version_scope: "all_time",
    status: "active",
    holder_build_ids: [1],
    computed_at: frozenDate,
  },
  {
    id: 12,
    definition_id: 5,
    competition_id: "22222222-2222-2222-2222-222222222222",
    title: "Smallest piston extender",
    subtitle: null,
    record_class: "smallest",
    build_kind: "extender",
    version_scope: "current",
    status: "active",
    holder_build_ids: [3],
    computed_at: frozenDate,
  },
] satisfies [RecordSummary, RecordSummary];

const recordDetail: RecordDetail = { ...records[0], holder_builds: [builds[0]] };

function json(body: unknown, status = 200, contentType = "application/json"): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": contentType, "Cache-Control": "no-store" },
  });
}

function problem(request: Request, status: number, code: string): Response {
  const chinese = request.headers.get("Accept-Language")?.toLowerCase().startsWith("zh");
  const body: ProblemDetail = {
    type: `https://api.redstone-squid.org/problems/${code}`,
    title: chinese ? "目录请求无效" : "Catalogue request invalid",
    status,
    detail: chinese ? "请检查搜索语法后重试。" : "Check the search syntax and try again.",
    code: code as ProblemDetail["code"],
    error_id: "fixture-error-001",
  };
  return json(body, status, "application/problem+json");
}

function buildPage(url: URL): PageBuildSummary {
  if (url.searchParams.get("cursor") === "build-page-2") {
    return { items: [builds[2]], next_cursor: null, has_more: false };
  }
  const query = url.searchParams.get("q") ?? "";
  const selected = query.includes("Space Builder") ? [builds[1]] : builds.slice(0, 2);
  return {
    items: selected,
    next_cursor: selected.length > 1 ? "build-page-2" : null,
    has_more: selected.length > 1,
  };
}

function recordPage(url: URL): PageRecordSummary {
  if (url.searchParams.get("cursor") === "record-page-2") {
    return { items: [records[1]], next_cursor: null, has_more: false };
  }
  return { items: [records[0]], next_cursor: "record-page-2", has_more: true };
}

async function routeRequest(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/livez") return new Response("ok");

  if (url.pathname === "/v1/builds") {
    const query = url.searchParams.get("q") ?? "";
    if (query.includes("syntax:error") || query === "(")
      return problem(request, 400, "invalid_query");
    if (query.includes("force:unavailable")) return problem(request, 503, "service_unavailable");
    return json(buildPage(url) satisfies PageBuildSummary);
  }
  if (url.pathname === "/v1/builds/998") {
    await new Promise((resolve) => setTimeout(resolve, 5_500));
    return json(detail);
  }
  if (url.pathname === "/v1/builds/998/schematics") {
    await new Promise((resolve) => setTimeout(resolve, 5_500));
    return json({ items: [], next_cursor: null, has_more: false } satisfies PageSchematicSummary);
  }
  if (url.pathname === "/v1/builds/1") return json(detail satisfies BuildDetail);
  if (url.pathname === "/v1/builds/1/schematics") {
    return json({
      items: [
        {
          id: 8,
          primary: true,
          format: "litematic",
          byte_size: 12544,
          dimensions: { width: 9, height: 7, depth: 4 },
          allocated_dimensions: { width: 9, height: 7, depth: 4 },
          block_count: 184,
          bounding_volume: 252,
          entity_count: 0,
          palette_size: 12,
          source_data_version: 3955,
          analyzer_version: "nucleation-0.11.0",
          analysis_schema_version: 1,
          license: "cc_by_4_0",
          license_url: "https://creativecommons.org/licenses/by/4.0/",
          download_url: "/v1/builds/1/schematics/8/content",
        },
      ],
      next_cursor: null,
      has_more: false,
    } satisfies PageSchematicSummary);
  }
  if (/^\/v1\/builds\/\d+(?:\/schematics)?$/.test(url.pathname))
    return problem(request, 404, "not_found");

  if (url.pathname === "/v1/records") return json(recordPage(url) satisfies PageRecordSummary);
  if (url.pathname === "/v1/records/11") return json(recordDetail satisfies RecordDetail);
  if (/^\/v1\/records\/\d+$/.test(url.pathname)) return problem(request, 404, "not_found");

  if (url.pathname === "/v1/search/suggest") {
    return json({
      suggestions: ["door", "door type", "door seamless"],
    } satisfies SearchSuggestions);
  }
  if (url.pathname === "/v1/search") {
    const query = url.searchParams.get("q") ?? "";
    if (query.includes("syntax:error") || query === "(")
      return problem(request, 400, "invalid_query");
    const page: SearchPage = {
      items: [
        { resource_kind: "build", score: 1, build: builds[0] },
        {
          resource_kind: "record",
          score: 0.8,
          record: {
            record_id: 11,
            title: records[0].title,
            subtitle: records[0].subtitle,
            build_id: 1,
            build_title: builds[0].title,
            record_class: records[0].record_class,
            version_scope: records[0].version_scope,
            tags: ["door"],
            metrics: { opening_time: 8 },
          },
        },
        {
          resource_kind: "metadata",
          score: 0.5,
          metadata: {
            id: "tag:1",
            title: "Seamless",
            metadata_kind: "tag",
            description: "A seamless opening.",
            aliases: ["full seamless"],
          },
        },
      ],
      next_cursor: null,
      has_more: false,
    };
    return json(page);
  }
  return problem(request, 404, "not_found");
}

async function handleIncoming(incoming: IncomingMessage, outgoing: ServerResponse): Promise<void> {
  try {
    const headers = new Headers();
    for (const [name, value] of Object.entries(incoming.headers)) {
      if (Array.isArray(value)) value.forEach((item) => headers.append(name, item));
      else if (value !== undefined) headers.set(name, value);
    }
    const request = new Request(`http://127.0.0.1:${port}${incoming.url ?? "/"}`, {
      method: incoming.method,
      headers,
    });
    const response = await routeRequest(request);
    outgoing.statusCode = response.status;
    response.headers.forEach((value, name) => outgoing.setHeader(name, value));
    outgoing.end(Buffer.from(await response.arrayBuffer()));
  } catch (error) {
    outgoing.statusCode = 500;
    outgoing.end("fixture server error");
    console.error(
      JSON.stringify({
        event: "fixture_api_error",
        error: error instanceof Error ? error.message : String(error),
      }),
    );
  }
}

const server = createServer((incoming, outgoing) => {
  void handleIncoming(incoming, outgoing);
});
server.listen(port, "0.0.0.0");

console.info(JSON.stringify({ event: "fixture_api_started", port, frozen_date: frozenDate }));
