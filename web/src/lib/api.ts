import { createClient, type Client } from "../generated/client";
import {
  buildSchematicsList,
  buildsGet,
  buildsList,
  recordsGet,
  recordsList,
  searchExecute,
  searchTermsSuggest,
} from "../generated/sdk.gen";
import type {
  BuildDetail,
  PageAnnotatedUnionBuildSearchResultRecordSearchResultMetadataSearchResultFieldInfoAnnotationNoneTypeRequiredTrueDiscriminatorResourceKind as SearchPage,
  PageBuildSummary,
  PageRecordSummary,
  PageSchematicSummary,
  ProblemDetail,
  RecordDetail,
  SearchScope,
  SearchSuggestions,
} from "../generated/types.gen";
import { getRuntimeConfig, type RuntimeConfig } from "./config";
import type { Locale } from "./i18n";

const API_TIMEOUT_MS = 5_000;

type ApiResult<T> = { data: T | undefined; error?: unknown; response?: Response };

export class CatalogueApiError extends Error {
  readonly status: number;
  readonly problem: ProblemDetail | undefined;

  constructor(status: number, problem?: ProblemDetail, options?: ErrorOptions) {
    super(problem?.detail ?? problem?.title ?? "Catalogue API request failed.", options);
    this.name = "CatalogueApiError";
    this.status = status;
    this.problem = problem;
  }
}

function isProblemDetail(value: unknown): value is ProblemDetail {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<ProblemDetail>;
  return typeof candidate.title === "string" && typeof candidate.status === "number";
}

function unwrap<T>(result: ApiResult<T>): T {
  if (result.data !== undefined) return result.data;
  const problem = isProblemDetail(result.error) ? result.error : undefined;
  throw new CatalogueApiError(result.response?.status ?? problem?.status ?? 503, problem);
}

function timedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const timeout = AbortSignal.timeout(API_TIMEOUT_MS);
  const signal = init?.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
  return fetch(input, { ...init, signal });
}

export function createCatalogueClient(
  locale: Locale,
  config: RuntimeConfig = getRuntimeConfig(),
): Client {
  return createClient({
    baseUrl: config.apiBaseUrl,
    fetch: timedFetch,
    headers: { "Accept-Language": locale },
    responseStyle: "fields",
  });
}

/** How a request addresses one page. At most one field is meaningful at a time. */
export type PageQuery = {
  offset?: number;
  afterId?: number;
  beforeId?: number;
};

/** Serialize a page address, omitting the parameters the API treats as mutually exclusive. */
function pageParams(query: PageQuery): Record<string, number> {
  if (query.afterId !== undefined) return { after_id: query.afterId };
  if (query.beforeId !== undefined) return { before_id: query.beforeId };
  if (query.offset) return { offset: query.offset };
  return {};
}

export type BuildQuery = PageQuery & {
  q?: string;
  sort?: string;
  pageSize?: number;
};

export async function fetchBuilds(
  locale: Locale,
  query: BuildQuery = {},
): Promise<PageBuildSummary> {
  const result = await buildsList({
    client: createCatalogueClient(locale),
    query: {
      ...(query.q ? { q: query.q } : {}),
      ...(query.sort ? { sort: query.sort } : {}),
      ...pageParams(query),
      page_size: Math.min(query.pageSize ?? 20, 50),
    },
  });
  return unwrap(result);
}

export async function fetchBuild(locale: Locale, id: number): Promise<BuildDetail> {
  const result = await buildsGet({
    client: createCatalogueClient(locale),
    path: { build_id: id },
  });
  return unwrap(result);
}

export type RecordQuery = PageQuery & {
  sort?: string;
  pageSize?: number;
};

export async function fetchRecords(
  locale: Locale,
  query: RecordQuery = {},
): Promise<PageRecordSummary> {
  const result = await recordsList({
    client: createCatalogueClient(locale),
    query: {
      ...(query.sort ? { sort: query.sort } : {}),
      ...pageParams(query),
      page_size: Math.min(query.pageSize ?? 20, 50),
    },
  });
  return unwrap(result);
}

export async function fetchRecord(locale: Locale, id: number): Promise<RecordDetail> {
  const result = await recordsGet({
    client: createCatalogueClient(locale),
    path: { record_id: id },
  });
  return unwrap(result);
}

export async function fetchSchematics(
  locale: Locale,
  buildId: number,
): Promise<PageSchematicSummary> {
  const result = await buildSchematicsList({
    client: createCatalogueClient(locale),
    path: { build_id: buildId },
    query: { page_size: 50 },
  });
  return unwrap(result);
}

/** Relevance has no identifier order to anchor to, so search pages are addressed by offset. */
export type CatalogueSearchQuery = {
  q: string;
  scope?: SearchScope;
  sort?: string;
  offset?: number;
  pageSize?: number;
};

export async function fetchSearch(
  locale: Locale,
  query: CatalogueSearchQuery,
): Promise<SearchPage> {
  const result = await searchExecute({
    client: createCatalogueClient(locale),
    query: {
      q: query.q,
      scope: query.scope ?? "all",
      ...(query.sort ? { sort: query.sort } : {}),
      ...(query.offset ? { offset: query.offset } : {}),
      page_size: Math.min(query.pageSize ?? 20, 50),
    },
  });
  return unwrap(result);
}

export async function fetchSuggestions(locale: Locale, q: string): Promise<SearchSuggestions> {
  const result = await searchTermsSuggest({
    client: createCatalogueClient(locale),
    query: { q, limit: 8 },
  });
  return unwrap(result);
}

export function asCatalogueError(error: unknown): CatalogueApiError {
  if (error instanceof CatalogueApiError) return error;
  return new CatalogueApiError(503, undefined, { cause: error });
}
