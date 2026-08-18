import {
  accountConsentGrant,
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
  ProblemDetail,
  StoredDraftResponse,
  SubmissionFinalizationResponse,
} from "../generated/types.gen";
import { getRuntimeConfig, type RuntimeConfig } from "./config";
import type { Locale } from "./i18n";
import { WEB_SUBMISSION_CAPABILITIES } from "./submission-form";

type ApiResult<T> = { data: T | undefined; error?: unknown; response?: Response };

export type SubmissionFailureKind =
  "authentication" | "consent" | "conflict" | "unavailable" | "request";

export class SubmissionApiError extends Error {
  readonly status: number;
  readonly problem: ProblemDetail | undefined;
  readonly kind: SubmissionFailureKind;
  readonly networkFailure: boolean;
  readonly requestId: string | null;

  constructor(
    status: number,
    problem?: ProblemDetail,
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
  submitDraft: (draftId: string) => Promise<SubmissionFinalizationResponse>;
  submissionStatus: (draftId: string) => Promise<SubmissionFinalizationResponse>;
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

function isProblemDetail(value: unknown): value is ProblemDetail {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<ProblemDetail>;
  return typeof candidate.title === "string" && typeof candidate.status === "number";
}

function apiError(result: { error?: unknown; response?: Response }): SubmissionApiError {
  const problem = isProblemDetail(result.error) ? result.error : undefined;
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

/**
 * Where to send a browser to log in.
 *
 * `/v1/auth/{provider}` is templated server-side, and `"discord"` is one instance of the
 * template rather than a special case -- adding a second provider is a caller passing a
 * different slug, not another URL builder. This used to be three identical copies.
 */
function signInUrl(config: RuntimeConfig, returnTo: string, provider = "discord"): string {
  const url = new URL(`${config.apiBaseUrl}/v1/auth/${provider}`);
  url.searchParams.set("redirect_to", returnTo);
  return url.toString();
}

function requestId(): string {
  return crypto.randomUUID();
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
      const idempotencyKey = requestId();
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
      const idempotencyKey = requestId();
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
      const idempotencyKey = requestId();
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
      mutations.execute(requestId(), async (headers) =>
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

export function createMinecraftLinkApi(
  locale: Locale,
  config: RuntimeConfig = getRuntimeConfig(),
): MinecraftLinkApi {
  const client = createSubmissionClient(locale, config);
  const mutations = createMutationSession(client);
  return {
    approve: async (userCode) => {
      const idempotencyKey = requestId();
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
      const idempotencyKey = requestId();
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
 * Read the privacy notice, and record acceptance of the exact version that was read.
 *
 * Lives here rather than in its own module so it inherits the CSRF and idempotency handling
 * every other mutation already goes through. The notice itself is fetched from the API rather
 * than kept in the web dictionary: the version a receipt names has to identify one piece of
 * text, and a second copy on the client is how that stops being true.
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
      const idempotencyKey = requestId();
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
 * Wrap an API so a consent refusal hands back the call that was refused.
 *
 * `SubmissionFlow` can fail the consent gate on any of a dozen calls -- creating a draft,
 * changing a field, uploading media, finalizing -- and each one would otherwise need its own
 * copy of "remember this and retry it". Wrapping once means every method recovers, including
 * ones added later, and the components keep their existing error handling untouched.
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
