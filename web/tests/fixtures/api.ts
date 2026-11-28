import { text as readText } from "node:stream/consumers";
import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { doorEvidence } from "./door-record";

import type {
  BuildDetail,
  PublicBuildSummary,
  SearchResultPage as SearchPage,
  BuildsListResponse,
  RecordsListResponse,
  BuildSchematicsListResponse,
  ProblemDetail,
  PublicRecordDetail,
  PublicRecordSummary,
  BrowserProvider,
  PrivacyNoticeDetail,
  SessionSnapshot,
  SearchSuggestions,
  SuggestionPage,
} from "@/generated/types.gen";

// Deterministic public catalogue data for browser checks; this is not a full API emulator.
const port = Number(process.env.FIXTURE_API_PORT ?? 8787);
const frozenDate = "2026-08-10T12:00:00Z";

const builds = [
  {
    id: 1,
    revision: 3,
    title: "Copper Bolt 5×5",
    display_name: "Copper Bolt",
    status: "approved",
    category: "Door",
    dimensions: { width: 9, height: 7, depth: 4 },
    creators: [
      { name: "CircuitSage", account_id: null },
      { name: "红石猫", account_id: null },
    ],
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
    status: "approved",
    category: "Entrance",
    dimensions: { width: 12, height: 12, depth: 5 },
    creators: [{ name: "Space Builder", account_id: null }],
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
    status: "approved",
    category: "Extender",
    dimensions: { width: 3, height: 5, depth: 8 },
    creators: [{ name: "CircuitSage", account_id: null }],
    tags: [],
    preview: null,
    version_spec: null,
    versions: ["Java 1.20"],
    opening_time: null,
    closing_time: null,
    created_at: "2026-06-10T09:00:00Z",
    updated_at: null,
  },
] satisfies [PublicBuildSummary, PublicBuildSummary, PublicBuildSummary];

const detail: BuildDetail = {
  ...builds[0],
  submitted_at: builds[0].created_at,
  schematics: { count: 0, primary: null },
  details: {
    category: "Door",
    door_dimensions: { width: 5, height: 5, depth: 1 },
    orientation: "Door",
    patterns: ["Flush", "Full seamless"],
    visible_opening_time: null,
    visible_closing_time: null,
  },
  restrictions: {
    wiring: ["No observers"],
    component: ["No slime blocks"],
    miscellaneous: [],
  },
  description:
    "A compact showcase door built for reliable survival use. The input line is exposed at the rear and the reset is fully automatic.",
  sponsor: {
    installation_id: "77777777-7777-4777-8777-777777777777",
    display_name: "Coppercraft",
    address: "play.coppercraft.example",
    description: "A public survival redstone server.",
    website_url: "https://coppercraft.example/",
  },
  media: [
    { kind: "render", url: "https://media.fixture.invalid/build-1.png" },
    { kind: "image", url: "https://media.fixture.invalid/build-1-alt.png" },
    { kind: "image", url: "http://unsafe.fixture/image.png" },
    { kind: "video", url: "https://video.example.org/watch/door" },
  ],
  links: {
    world_downloads: ["https://downloads.example.org/copper-bolt.zip"],
    schematics: ["https://downloads.example.org/copper-bolt.litematic"],
  },
};

const records = [
  {
    result_id: 11,
    definition_id: 4,
    record_id: "11111111-1111-1111-1111-111111111111",
    title: "Fastest 5×5 seamless door",
    subtitle: "Java, all time",
    criterion: "fastest_smallest",
    build_kind: "door",
    version_scope: "all_time",
    category_key: "door:door|5x5|t[]|Door:r[]:p[]",
    status: "resolved",
    holder_build_ids: [1],
    holders: [{ build_id: 1, title: "Fastest 5×5 seamless door", creators: [], preview: null }],
    metrics: { opening_time: { value: 12, unit: "game_ticks", display: "12 gt" } },
    computed_at: frozenDate,
  },
  {
    result_id: 12,
    definition_id: 5,
    record_id: "22222222-2222-2222-2222-222222222222",
    title: "Smallest piston extender",
    subtitle: null,
    criterion: "smallest",
    build_kind: "extender",
    version_scope: "up_to_date",
    category_key: "extender:extender|Upward|3|t[]:r[]:p[]",
    status: "resolved",
    holder_build_ids: [3],
    holders: [{ build_id: 3, title: "Smallest piston extender", creators: [], preview: null }],
    computed_at: frozenDate,
  },
] satisfies [PublicRecordSummary, PublicRecordSummary];

const recordDetail: PublicRecordDetail = { ...records[0], holder_builds: [builds[0]] };
const expandableRecord: PublicRecordDetail = {
  ...records[0],
  result_id: 13,
  record_id: "33333333-3333-3333-3333-333333333333",
  title: "Smallest 6×3 door",
  criterion: "smallest",
  door_evidence: doorEvidence,
  holder_builds: [
    {
      ...builds[0],
      title: "Expandable n×3 door",
      dimensions: { width: 99, height: 3, depth: 1 },
      opening_time: 198,
    },
  ],
};

function json(body: unknown, status = 200, contentType = "application/json"): Response {
  return Response.json(body, {
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
  };
  return Response.json(body, {
    status,
    headers: {
      "Content-Type": "application/problem+json",
      "Cache-Control": "no-store",
      "Request-Id": "fixture-error-001",
    },
  });
}

function buildPage(url: URL): BuildsListResponse {
  if (url.searchParams.get("page_size") === "6")
    return { items: builds, total: builds.length, next: null, prev: null };
  if (url.searchParams.get("cursor") === "builds-2") {
    return {
      items: [builds[2]],
      total: 3,
      next: null,
      prev: { offset: null, cursor: "builds-2-back" },
    };
  }
  return {
    items: builds.slice(0, 2),
    total: 3,
    next: { offset: null, cursor: "builds-2" },
    prev: null,
  };
}

function recordPage(url: URL): RecordsListResponse {
  if (url.searchParams.get("page_size") === "3")
    return { items: records, total: records.length, next: null, prev: null };
  if (url.searchParams.get("cursor") === "records-1") {
    return {
      items: [records[1]],
      total: 2,
      next: null,
      prev: { offset: null, cursor: "records-1-back" },
    };
  }
  return { items: [records[0]], total: 2, next: { offset: null, cursor: "records-1" }, prev: null };
}

/**
 * Browser sessions are named by their cookie value, so tests pick a state by setting it:
 * `member` has accepted the notice, `pending` and `stale` have not, and `stale` fails to
 * accept because the notice changed. Anything else is an expired session.
 */
const sessionUsers = { member: "Copper Fan", pending: "New Member", stale: "Slow Reader" };

function cookie(request: Request, name: string): string | undefined {
  for (const pair of (request.headers.get("Cookie") ?? "").split(";")) {
    const [key, ...value] = pair.trim().split("=");
    if (key === name) return value.join("=");
  }
  return undefined;
}

const indexing = new Map<string, boolean>();

function session(request: Request): SessionSnapshot {
  const name = cookie(request, "__Host-squid_session") ?? "";
  const display = Object.hasOwn(sessionUsers, name)
    ? sessionUsers[name as keyof typeof sessionUsers]
    : null;
  return {
    user:
      display === null
        ? null
        : { id: "00000000-0000-4000-8000-000000000007", display_name: display, avatar_url: null },
    permissions: [],
    counts: { unread_notifications: 0, drafts: 0, drafts_needing_attention: 0, review_queue: null },
  };
}

/** Browser writes need the CSRF cookie echoed in the header, as the API checks. */
function csrfMatches(request: Request): boolean {
  const token = cookie(request, "squid_csrf");
  return token !== undefined && request.headers.get("CSRF-Token") === token;
}

async function routeRequest(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/livez") return new Response("ok");

  const profileId = "00000000-0000-4000-8000-000000000007";
  const creditId = "00000000-0000-4000-8000-000000000017";
  const stats = {
    build_count: 1,
    builds_by_year: [{ year: 2026, count: 1 }],
    records_held: [],
    records_ever: 0,
  };
  if (
    url.pathname === "/v1/creator-aliases/summary" &&
    url.searchParams.get("name") === "Space Builder"
  )
    return json({ id: 7, name: "Space Builder", stats });
  if (url.pathname === `/v1/accounts/${profileId}`)
    return json({ id: profileId, hidden: false, display_name: "Space Builder" });
  if (url.pathname === `/v1/accounts/${profileId}/creator-stats`) return json(stats);
  if (
    url.pathname === "/v1/builds" &&
    (url.searchParams.has("creator_name_id") || url.searchParams.has("account_id"))
  )
    return json({
      items: [
        {
          ...builds[1],
          creators: [{ name: "Space Builder", account_id: profileId }],
          credits: [
            {
              credit_id: creditId,
              name_id: 7,
              name: "Space Builder",
              role: "creator",
              kind: "name",
              account_id: profileId,
              attribution: "explicit",
              username: null,
            },
          ],
        },
      ],
      total: 1,
      prev: null,
      next: null,
    });
  if (url.pathname === "/v1/accounts/me/claims") return json([]);
  if (
    url.pathname === "/v1/creator-aliases/7/claims" &&
    request.method === "POST" &&
    csrfMatches(request)
  ) {
    const body: unknown = await request.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "credit_ids" in body &&
      Array.isArray(body.credit_ids) &&
      body.credit_ids.includes(creditId)
    )
      return json(
        {
          id: 1,
          name: "Space Builder",
          status: "pending",
          credits: [{ credit_id: creditId, build_id: 2 }],
          approved_credit_ids: [],
        },
        201,
      );
    return problem(request, 422, "VALIDATION_ERROR");
  }
  if (url.pathname === "/v1/session" && cookie(request, "__Host-squid_session") === "outage")
    return problem(request, 503, "SERVICE_UNAVAILABLE");
  if (url.pathname === "/v1/records/rate-limited") {
    const response = problem(request, 429, "RATE_LIMITED");
    response.headers.set("Retry-After", "45");
    return response;
  }
  if (url.pathname === "/v1/session") return json(session(request));
  if (url.pathname === "/v1/auth/providers") {
    return json([{ id: "discord" }] satisfies BrowserProvider[]);
  }
  if (url.pathname === "/v1/privacy/policy") {
    return json({
      version: "2026-08-18",
      locale: "en",
      title: "Privacy notice",
      body: "Redstone Squid stores your Discord user ID.\n\nAutomatic indexing is off by default.",
    } satisfies PrivacyNoticeDetail);
  }
  if (
    url.pathname === "/v1/accounts/me" ||
    url.pathname === "/v1/accounts/me/discord-build-indexing"
  ) {
    const user = cookie(request, "__Host-squid_session");
    if (user === undefined || session(request).user === null)
      return problem(request, 401, "AUTHENTICATION_REQUIRED");
    if (request.method === "PUT") {
      if (!csrfMatches(request)) return problem(request, 403, "FORBIDDEN");
      const body: unknown = await request.json();
      if (
        typeof body !== "object" ||
        body === null ||
        !("enabled" in body) ||
        typeof body.enabled !== "boolean"
      )
        return problem(request, 422, "INVALID_SETTING");
      indexing.set(user, body.enabled);
    }
    return json({ discord_build_indexing_enabled: indexing.get(user) ?? false });
  }
  if (url.pathname === "/v1/auth/logout" && request.method === "POST") {
    if (!csrfMatches(request)) return problem(request, 403, "FORBIDDEN");
    return new Response(null, { status: 204 });
  }

  if (url.pathname === "/v1/tags/lookup") {
    if (["zero-tick", "old-name"].includes(url.searchParams.get("slug") ?? "")) {
      return json({ name: "Zero tick", slug: "zero-tick" });
    }
    return problem(request, 404, "not_found");
  }

  if (url.pathname === "/v1/builds") return json(buildPage(url) satisfies BuildsListResponse);
  // Both detail reads exceed the catalogue client's five-second deadline.
  if (url.pathname === "/v1/builds/998") {
    await new Promise((resolve) => setTimeout(resolve, 5_500));
    return json(detail);
  }
  if (url.pathname === "/v1/builds/998/schematics") {
    await new Promise((resolve) => setTimeout(resolve, 5_500));
    return json({
      items: [],
      total: 0,
      next: null,
      prev: null,
    } satisfies BuildSchematicsListResponse);
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
          attached_at: "2026-08-01T00:00:00Z",
          blocks: null,
        },
      ],
      total: 1,
      next: null,
      prev: null,
    } satisfies BuildSchematicsListResponse);
  }
  if (/^\/v1\/builds\/\d+(?:\/schematics)?$/.test(url.pathname))
    return problem(request, 404, "not_found");

  if (url.pathname === "/v1/records") return json(recordPage(url) satisfies RecordsListResponse);
  if (url.pathname === `/v1/records/${recordDetail.record_id}`)
    return json(recordDetail satisfies PublicRecordDetail);
  if (url.pathname === `/v1/records/${expandableRecord.record_id}`) return json(expandableRecord);
  if (/^\/v1\/records\/\d+$/.test(url.pathname)) return problem(request, 404, "not_found");

  if (url.pathname === "/v1/search/suggest") {
    return json({
      suggestions: ["door", "door type", "door seamless"],
    } satisfies SearchSuggestions);
  }
  if (url.pathname === "/v1/suggest/build_titles") {
    const query = (url.searchParams.get("q") ?? "").toLowerCase();
    return json({
      source: "build_titles",
      revision: null,
      replacement: null,
      items: ["door", "door type", "door seamless"]
        .filter((value) => value.startsWith(query))
        .map((value) => ({ value, label: value, description: null, kind: "text" })),
    } satisfies SuggestionPage);
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
            record_id: records[0].record_id,
            title: records[0].title,
            subtitle: records[0].subtitle,
            build_id: 1,
            build_title: builds[0].title,
            criterion: records[0].criterion,
            version_scope: records[0].version_scope,
            metrics: { opening_time: { value: 8, unit: "game_ticks", display: "8 gt" } },
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
      total: 1,
      next: null,
      prev: null,
      filters: [],
      counts: null,
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
      body:
        incoming.method === "GET" || incoming.method === "HEAD"
          ? undefined
          : await readText(incoming),
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
