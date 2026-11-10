/**
 * Browser API clients for private drafts, consent, and device approval.
 *
 * These clients send the browser session cookie and obtain a CSRF token for mutations.
 * Each factory owns its token cache; UI components handle sign-in and consent prompts.
 * Request retries preserve their idempotency key or upload ID where supported, while
 * draft revision conflicts require the editor to fetch and reconcile the latest draft.
 */
import {
  accountConsentGrant,
  runtimeSettingsGet,
  runtimeSettingsUpdate,
  browserCsrfGet,
  cliEnrollmentApprove,
  consentNoticeGet,
  cliEnrollmentPreview,
  minecraftChallengeApprove,
  submissionDraftChange,
  submissionDraftCreate,
  submissionDraftDelete,
  submissionDraftGet,
  submissionFinalizationGet,
  submissionFinalizationStart,
  submissionFormCurrent,
  submissionFormOptionsGet,
  submissionMediaDiscard,
  submissionMediaList,
  submissionMediaUpload,
} from "../generated/sdk.gen";
import { createClient, type Client } from "../generated/client";
import type {
  ChallengeApprovalResponse,
  CliEnrollmentApprovalResponse,
  DraftChangeRequest,
  DraftChangeResponse,
  DraftMediaListResponse,
  DraftMediaResponse,
  FormManifestResponse,
  FormOptionSetResponse,
  MediaKind,
  PrivacyNoticeDetail,
  StoredDraftResponse,
  SubmissionFinalizationGetResponse,
} from "../generated/types.gen";
import { getRuntimeConfig, type RuntimeConfig } from "./config";
import type { Locale } from "./i18n";
import { decodeProblemDetail, type ApiProblemDetail } from "./problem-details";
import { WEB_SUBMISSION_CAPABILITIES } from "./submission-form";

type ApiResult<T> = { data: T | undefined; error?: unknown; response?: Response };

export type SubmissionFailureKind =
  "authentication" | "consent" | "conflict" | "unavailable" | "request";

export class SubmissionApiError extends Error {
  readonly status: number;
  readonly problem: ApiProblemDetail | undefined;
  readonly kind: SubmissionFailureKind;
  readonly networkFailure: boolean;
  readonly requestId: string | null;

  constructor(
    status: number,
    problem?: ApiProblemDetail,
    options?: ErrorOptions & { networkFailure?: boolean; requestId?: string | null },
  ) {
    super(problem?.detail ?? problem?.title ?? "The submission service did not answer.", options);
    this.name = "SubmissionApiError";
    this.status = status;
    this.problem = problem;
    this.networkFailure = options?.networkFailure ?? false;
    this.requestId = options?.requestId ?? null;
    this.kind =
      status === 401
        ? "authentication"
        : problem?.code === "CONSENT_REQUIRED"
          ? "consent"
          : status === 409
            ? "conflict"
            : status === 503 || this.networkFailure
              ? "unavailable"
              : "request";
  }
}

export type SubmissionApi = {
  currentForm: () => Promise<FormManifestResponse>;
  formOptions: (source: string, category: string) => Promise<FormOptionSetResponse>;
  createDraft: (category: string) => Promise<StoredDraftResponse>;
  getDraft: (draftId: string) => Promise<StoredDraftResponse>;
  changeDraft: (draftId: string, change: DraftChangeRequest) => Promise<DraftChangeResponse>;
  deleteDraft: (draftId: string) => Promise<void>;
  listMedia: (draftId: string) => Promise<DraftMediaListResponse>;
  uploadMedia: (
    draftId: string,
    kind: MediaKind,
    file: File,
    uploadId: string,
    stripAudio: boolean,
  ) => Promise<DraftMediaResponse>;
  discardMedia: (draftId: string, uploadId: string) => Promise<void>;
  submitDraft: (draftId: string) => Promise<SubmissionFinalizationGetResponse>;
  submissionStatus: (draftId: string) => Promise<SubmissionFinalizationGetResponse>;
  signInUrl: (returnTo: string) => string;
};

export type MinecraftLinkApi = {
  approve: (userCode: string) => Promise<ChallengeApprovalResponse>;
  signInUrl: (returnTo: string) => string;
};

export type ConsentApi = {
  notice: () => Promise<PrivacyNoticeDetail>;
  grant: (version: string) => Promise<void>;
};

export type CliLinkApi = {
  preview: (userCode: string) => Promise<CliEnrollmentApprovalResponse>;
  approve: (userCode: string) => Promise<CliEnrollmentApprovalResponse>;
  signInUrl: (returnTo: string) => string;
};

function apiError(result: { error?: unknown; response?: Response }): SubmissionApiError {
  const problem = decodeProblemDetail(result.error);
  const networkFailure = result.response === undefined;
  return new SubmissionApiError(result.response?.status ?? problem?.status ?? 503, problem, {
    cause: result.error,
    networkFailure,
    requestId: result.response?.headers.get("Request-Id") ?? null,
  });
}

function unwrap<T>(result: ApiResult<T>): T {
  if (result.data !== undefined) return result.data;
  throw apiError(result);
}

function csrfToken(cookie = typeof document === "undefined" ? "" : document.cookie): string | null {
  const prefix = "squid_csrf=";
  const encoded = cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length);
  if (!encoded) return null;
  try {
    return decodeURIComponent(encoded);
  } catch {
    return null;
  }
}

function mutationHeaders(csrf: string, idempotencyKey?: string): Record<string, string> {
  return {
    "CSRF-Token": csrf,
    ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
  };
}

/** Build the provider login URL; the server validates the requested return destination. */
function signInUrl(config: RuntimeConfig, returnTo: string, provider = "discord"): string {
  const url = new URL(`${config.apiBaseUrl}/v1/auth/${provider}`);
  url.searchParams.set("redirect_to", returnTo);
  return url.toString();
}

function createSubmissionClient(locale: Locale, config: RuntimeConfig): Client {
  return createClient({
    baseUrl: config.apiBaseUrl,
    credentials: "include",
    cache: "no-store",
    headers: { "Accept-Language": locale },
    responseStyle: "fields",
  });
}

type MutationSession = {
  execute: <T>(
    idempotencyKey: string | undefined,
    request: (headers: Record<string, string>) => Promise<T>,
  ) => Promise<T>;
};

/**
 * Cache CSRF credentials for one API instance and retry a refused mutation once.
 *
 * A 403 forces a token fetch and disables the cookie shortcut for this instance. The replay
 * keeps the supplied idempotency key; other failures propagate to the calling API method.
 */
function createMutationSession(client: Client): MutationSession {
  let cachedToken: string | undefined;
  let pendingToken: Promise<string> | undefined;
  let allowCookieFastPath = true;

  const resolveToken = async (): Promise<string> => {
    if (cachedToken) return cachedToken;
    if (allowCookieFastPath) {
      const localToken = csrfToken();
      if (localToken) {
        cachedToken = localToken;
        return localToken;
      }
    }
    // Concurrent mutations share the token request, including when the API cookie is cross-origin.
    pendingToken ??= browserCsrfGet({ client }).then((result) => unwrap(result).csrf_token);
    try {
      cachedToken = await pendingToken;
      return cachedToken;
    } finally {
      pendingToken = undefined;
    }
  };

  return {
    execute: async (idempotencyKey, request) => {
      const run = async () => request(mutationHeaders(await resolveToken(), idempotencyKey));
      try {
        return await run();
      } catch (error) {
        if (!(error instanceof SubmissionApiError) || error.status !== 403) throw error;
        cachedToken = undefined;
        allowCookieFastPath = false;
        return await run();
      }
    },
  };
}

async function uploadRawMedia(
  client: Client,
  mutations: MutationSession,
  draftId: string,
  kind: MediaKind,
  file: File,
  uploadId: string,
  stripAudio: boolean,
): Promise<DraftMediaResponse> {
  return mutations.execute(undefined, async (headers) =>
    unwrap(
      await submissionMediaUpload({
        client,
        path: { draft_id: draftId, kind },
        query: {
          upload_id: uploadId,
          ...(kind === "video" ? { strip_audio: stripAudio } : {}),
        },
        body: file,
        headers: { ...headers, "Content-Type": file.type },
      }),
    ),
  );
}

/**
 * Create a credentialed, uncached client for private drafts.
 *
 * Draft creation and media upload retry a network failure once, keeping the request identity.
 * Other methods leave network retries to callers. Mutations share the CSRF refresh policy.
 */
export function createSubmissionApi(
  locale: Locale,
  config: RuntimeConfig = getRuntimeConfig(),
): SubmissionApi {
  const client = createSubmissionClient(locale, config);
  const mutations = createMutationSession(client);
  return {
    currentForm: async () => unwrap(await submissionFormCurrent({ client })),
    formOptions: async (source, category) =>
      unwrap(
        await submissionFormOptionsGet({
          client,
          path: { source },
          query: { category },
        }),
      ),
    createDraft: async (category) => {
      const idempotencyKey = crypto.randomUUID();
      const request = async () =>
        mutations.execute(idempotencyKey, async (headers) =>
          unwrap(
            await submissionDraftCreate({
              client,
              body: {
                category,
                origin: "web",
                client_capabilities: [...WEB_SUBMISSION_CAPABILITIES],
              },
              headers,
            }),
          ),
        );
      try {
        return await request();
      } catch (error) {
        if (!(error instanceof SubmissionApiError) || !error.networkFailure) throw error;
        return await request();
      }
    },
    getDraft: async (draftId) =>
      unwrap(await submissionDraftGet({ client, path: { draft_id: draftId } })),
    changeDraft: async (draftId, change) =>
      mutations.execute(change.idempotency_key, async (headers) =>
        unwrap(
          await submissionDraftChange({
            client,
            path: { draft_id: draftId },
            body: change,
            headers,
          }),
        ),
      ),
    deleteDraft: async (draftId) => {
      const idempotencyKey = crypto.randomUUID();
      await mutations.execute(idempotencyKey, async (headers) => {
        const result = await submissionDraftDelete({
          client,
          path: { draft_id: draftId },
          headers,
        });
        if (result.data === undefined && result.response?.status !== 204) throw apiError(result);
      });
    },
    listMedia: async (draftId) =>
      unwrap(
        await submissionMediaList({
          client,
          path: { draft_id: draftId },
        }),
      ),
    uploadMedia: async (draftId, kind, file, uploadId, stripAudio) => {
      try {
        return await uploadRawMedia(client, mutations, draftId, kind, file, uploadId, stripAudio);
      } catch (error) {
        if (!(error instanceof SubmissionApiError) || !error.networkFailure) throw error;
        return await uploadRawMedia(client, mutations, draftId, kind, file, uploadId, stripAudio);
      }
    },
    discardMedia: async (draftId, uploadId) => {
      const idempotencyKey = crypto.randomUUID();
      await mutations.execute(idempotencyKey, async (headers) => {
        const result = await submissionMediaDiscard({
          client,
          path: { draft_id: draftId, upload_id: uploadId },
          headers,
        });
        if (result.data === undefined && result.response?.status !== 204) throw apiError(result);
      });
    },
    submitDraft: async (draftId) =>
      mutations.execute(crypto.randomUUID(), async (headers) =>
        unwrap(
          await submissionFinalizationStart({
            client,
            path: { draft_id: draftId },
            headers,
          }),
        ),
      ),
    submissionStatus: async (draftId) =>
      unwrap(
        await submissionFinalizationGet({
          client,
          path: { draft_id: draftId },
        }),
      ),
    signInUrl: (returnTo) => signInUrl(config, returnTo),
  };
}

/** Approve a player code, retrying one network failure with the same idempotency key. */
export function createMinecraftLinkApi(
  locale: Locale,
  config: RuntimeConfig = getRuntimeConfig(),
): MinecraftLinkApi {
  const client = createSubmissionClient(locale, config);
  const mutations = createMutationSession(client);
  return {
    approve: async (userCode) => {
      const idempotencyKey = crypto.randomUUID();
      const request = async () =>
        mutations.execute(idempotencyKey, async (headers) =>
          unwrap(
            await minecraftChallengeApprove({
              client,
              body: { user_code: userCode },
              headers,
            }),
          ),
        );
      try {
        return await request();
      } catch (error) {
        if (!(error instanceof SubmissionApiError) || !error.networkFailure) throw error;
        return await request();
      }
    },
    signInUrl: (returnTo) => signInUrl(config, returnTo),
  };
}

/** Preview a device or approve it, retrying approval once on network failure with the same key. */
export function createCliLinkApi(
  locale: Locale,
  config: RuntimeConfig = getRuntimeConfig(),
): CliLinkApi {
  const client = createSubmissionClient(locale, config);
  const mutations = createMutationSession(client);
  return {
    preview: async (userCode) =>
      unwrap(
        await cliEnrollmentPreview({
          client,
          query: { user_code: userCode },
        }),
      ),
    approve: async (userCode) => {
      const idempotencyKey = crypto.randomUUID();
      const request = async () =>
        mutations.execute(idempotencyKey, async (headers) =>
          unwrap(
            await cliEnrollmentApprove({
              client,
              body: { user_code: userCode },
              headers,
            }),
          ),
        );
      try {
        return await request();
      } catch (error) {
        if (!(error instanceof SubmissionApiError) || !error.networkFailure) throw error;
        return await request();
      }
    },
    signInUrl: (returnTo) => signInUrl(config, returnTo),
  };
}

export function asSubmissionError(error: unknown): SubmissionApiError {
  if (error instanceof SubmissionApiError) return error;
  return new SubmissionApiError(503, undefined, { cause: error, networkFailure: true });
}

/**
 * Fetch the server-authored privacy notice and record acceptance of its displayed version.
 *
 * The grant uses the shared CSRF refresh policy and an idempotency key. Keeping the notice
 * text and version together on the server avoids accepting a different client-side copy.
 */
export function createConsentApi(
  locale: Locale,
  config: RuntimeConfig = getRuntimeConfig(),
): ConsentApi {
  const client = createSubmissionClient(locale, config);
  const mutations = createMutationSession(client);
  return {
    notice: async () => unwrap(await consentNoticeGet({ client })),
    grant: async (version) => {
      const idempotencyKey = crypto.randomUUID();
      await mutations.execute(idempotencyKey, async (headers) =>
        unwrap(
          await accountConsentGrant({
            client,
            // Sent so the server refuses acceptance of a notice this client never displayed.
            body: { version },
            headers,
          }),
        ),
      );
    },
  };
}

/**
 * Capture a rejected promise call so the caller can retry it after obtaining consent.
 *
 * The original promise still rejects. The supplied retry repeats the method with the same
 * arguments but discards its result; it does not resume the original awaiting UI handler.
 * Callers must arrange any state refresh after replay and handle replay failures themselves.
 */
export function withConsentRetry<T extends object>(
  api: T,
  onConsentRequired: (retry: () => Promise<unknown>) => void,
): T {
  return new Proxy(api, {
    get(target, property, receiver) {
      const value = Reflect.get(target, property, receiver) as unknown;
      if (typeof value !== "function") return value;
      return (...args: unknown[]) => {
        const call = () => (value as (...a: unknown[]) => unknown).apply(target, args);
        const result = call();
        if (!(result instanceof Promise)) return result;
        return result.catch((error: unknown) => {
          if (error instanceof SubmissionApiError && error.kind === "consent") {
            onConsentRequired(async () => {
              await call();
            });
          }
          throw error;
        });
      };
    },
  });
}

export type RuntimeSettingsApi = {
  get: () => Promise<import("../generated/types.gen").RuntimeSettingsDetail>;
  update: (
    patch: import("../generated/types.gen").RuntimeSettingsPatch,
  ) => Promise<import("../generated/types.gen").RuntimeSettingsDetail>;
  signInUrl: (returnTo: string) => string;
};

/** Global settings use the same browser session, CSRF, and retry handling as private drafts. */
export function createRuntimeSettingsApi(
  locale: Locale,
  config: RuntimeConfig,
): RuntimeSettingsApi {
  const client = createSubmissionClient(locale, config);
  const mutations = createMutationSession(client);
  return {
    get: async () => unwrap(await runtimeSettingsGet({ client })),
    update: async (body) => {
      const key = crypto.randomUUID();
      return mutations.execute(key, async (headers) =>
        unwrap(await runtimeSettingsUpdate({ client, body, headers })),
      );
    },
    signInUrl: (returnTo) => signInUrl(config, returnTo),
  };
}
