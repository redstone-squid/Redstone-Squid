import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  DraftMediaListResponse,
  DraftMediaResponse,
  FormManifestResponse,
  StoredDraftResponse,
  SubmissionFinalizationResponse,
} from "../../src/generated/types.gen";
import {
  asSubmissionError,
  createCliLinkApi,
  createMinecraftLinkApi,
  createSubmissionApi,
  SubmissionApiError,
} from "../../src/lib/submission-api";
import type { RuntimeConfig } from "../../src/lib/config";

const config: RuntimeConfig = {
  apiBaseUrl: "https://api.catalogue.test",
  siteUrl: "https://catalogue.test",
  discordCommunityUrl: "https://discord.gg/redstone",
  botInviteUrl: "https://discord.com/oauth2/authorize",
};

const form = {
  schema_id: "build_submission.v1",
  revision: 1,
  minimum_protocol: 1,
  maximum_protocol: 1,
  common_sections: [],
  categories: [],
} satisfies FormManifestResponse;

const draft = {
  id: "11111111-1111-4111-8111-111111111111",
  schema_id: "build_submission.v1",
  schema_revision: 1,
  category: "door",
  revision: 0,
  status: "editing",
  answers: {},
  origin: "web",
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
  expires_at: "2026-09-11T00:00:00Z",
} satisfies StoredDraftResponse;

const mediaItem = {
  id: "22222222-2222-4222-8222-222222222222",
  draft_id: draft.id,
  kind: "video",
  status: "processing",
  source_content_type: "video/mp4",
  artifacts: [],
} satisfies DraftMediaResponse;

const media = {
  limits: {
    max_upload_bytes: 500_000_000,
    max_images: 10,
    max_videos: 3,
    max_output_bytes: 500_000_000,
    max_duration_milliseconds: 300_000,
    max_pixels_per_frame: 33_200_000,
    max_decoded_pixels_per_second: 250_000_000,
  },
  media: [mediaItem],
} satisfies DraftMediaListResponse;

const finalization = {
  draft_id: draft.id,
  draft_revision: 0,
  status: "pending",
  issues: [],
  build_id: null,
} satisfies SubmissionFinalizationResponse;

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function capturedRequest(index = -1): Request {
  const call = index < 0 ? fetchMock.mock.calls.at(index) : fetchMock.mock.calls[index];
  if (!call || !(call[0] instanceof Request)) throw new Error("No request captured.");
  return call[0];
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  document.cookie = "squid_csrf=csrf-token; path=/";
});

afterEach(() => {
  document.cookie = "squid_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  vi.unstubAllGlobals();
});

describe("submission API transport", () => {
  it("reads the current form and category-aware dynamic options with credentials", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(form))
      .mockResolvedValueOnce(
        jsonResponse({ source: "approved_patterns", category: "door", revision: 4, options: [] }),
      );
    const api = createSubmissionApi("zh-CN", config);
    await expect(api.currentForm()).resolves.toBeTruthy();
    expect(capturedRequest()).toMatchObject({ credentials: "include", cache: "no-store" });
    expect(capturedRequest().headers.get("Accept-Language")).toBe("zh-CN");
    await expect(api.formOptions("approved_patterns", "door")).resolves.toMatchObject({
      revision: 4,
    });
    const url = new URL(capturedRequest().url);
    expect(url.pathname).toBe("/v1/submissions/form/options/approved_patterns");
    expect(url.searchParams.get("category")).toBe("door");
  });

  it("creates a web draft with renderer capabilities, CSRF, and an HTTP idempotency key", async () => {
    fetchMock
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(jsonResponse(draft, 201));
    await expect(createSubmissionApi("en", config).createDraft("door")).resolves.toEqual(draft);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const request = capturedRequest();
    expect(request.headers.get("CSRF-Token")).toBe("csrf-token");
    expect(request.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    await expect(request.clone().json()).resolves.toEqual({
      category: "door",
      origin: "web",
      client_capabilities: ["repeatable_text"],
    });
    expect(capturedRequest(0).headers.get("Idempotency-Key")).toBe(
      capturedRequest(1).headers.get("Idempotency-Key"),
    );
    await expect(capturedRequest(0).clone().json()).resolves.toEqual(
      await capturedRequest(1).clone().json(),
    );
  });

  it("fetches and caches the session CSRF token when the API cookie is cross-origin", async () => {
    document.cookie = "squid_csrf=%E0%A4%A; path=/";
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "remote-csrf-token" }))
      .mockResolvedValueOnce(jsonResponse(draft, 201))
      .mockResolvedValueOnce(jsonResponse(finalization, 202));
    const api = createSubmissionApi("en", config);
    await api.createDraft("door");
    await api.submitDraft(draft.id);
    expect(new URL(capturedRequest(0).url).pathname).toBe("/v1/auth/csrf");
    expect(capturedRequest(0)).toMatchObject({ credentials: "include", cache: "no-store" });
    expect(capturedRequest(1).headers.get("CSRF-Token")).toBe("remote-csrf-token");
    expect(capturedRequest(2).headers.get("CSRF-Token")).toBe("remote-csrf-token");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("refreshes CSRF once after a 403 and replays the same idempotent mutation", async () => {
    document.cookie = "squid_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "stale-token" }))
      .mockResolvedValueOnce(jsonResponse({ title: "Forbidden", status: 403 }, 403))
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "fresh-token" }))
      .mockResolvedValueOnce(jsonResponse(draft, 201));
    await expect(createSubmissionApi("en", config).createDraft("door")).resolves.toEqual(draft);
    expect(
      fetchMock.mock.calls.map((_, index) => new URL(capturedRequest(index).url).pathname),
    ).toEqual([
      "/v1/auth/csrf",
      "/v1/submissions/drafts",
      "/v1/auth/csrf",
      "/v1/submissions/drafts",
    ]);
    expect(capturedRequest(1).headers.get("CSRF-Token")).toBe("stale-token");
    expect(capturedRequest(3).headers.get("CSRF-Token")).toBe("fresh-token");
    expect(capturedRequest(1).headers.get("Idempotency-Key")).toBe(
      capturedRequest(3).headers.get("Idempotency-Key"),
    );
  });

  it("gets and retry-safely changes a draft using the body identity as the HTTP identity", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(draft))
      .mockResolvedValueOnce(jsonResponse({ draft: { ...draft, revision: 1 }, replayed: false }));
    const api = createSubmissionApi("en", config);
    await expect(api.getDraft(draft.id)).resolves.toEqual(draft);
    const change = {
      base_revision: 0,
      client_instance_id: "web:test",
      idempotency_key: "33333333-3333-4333-8333-333333333333",
      operations: [
        {
          operation_id: "44444444-4444-4444-8444-444444444444",
          field_id: "display_name",
          kind: "set" as const,
          value: "Compact Door",
        },
      ],
    };
    await api.changeDraft(draft.id, change);
    expect(capturedRequest().headers.get("Idempotency-Key")).toBe(change.idempotency_key);
    await expect(capturedRequest().clone().json()).resolves.toEqual(change);
  });

  it("uses the generated binary endpoint with exact MIME, upload UUID, and a reusable body", async () => {
    const file = new File(["video"], "door.mp4", { type: "video/mp4" });
    fetchMock
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(jsonResponse(mediaItem, 202));
    const api = createSubmissionApi("en", config);
    await expect(api.uploadMedia(draft.id, "video", file, mediaItem.id, true)).resolves.toEqual(
      mediaItem,
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const call of fetchMock.mock.calls) {
      const request = call[0] as Request;
      const url = new URL(request.url);
      expect(request.headers.get("Content-Type")).toBe("video/mp4");
      expect(url.searchParams.get("upload_id")).toBe(mediaItem.id);
      expect(url.searchParams.get("strip_audio")).toBe("true");
      expect(request.body).not.toBeNull();
    }
  });

  it("lists and discards private media without exposing storage details", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(media))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const api = createSubmissionApi("en", config);
    await expect(api.listMedia(draft.id)).resolves.toEqual(media);
    await expect(api.discardMedia(draft.id, mediaItem.id)).resolves.toBeUndefined();
    expect(capturedRequest().method).toBe("DELETE");
    expect(capturedRequest().url).toContain(`/media/${mediaItem.id}`);
    expect(capturedRequest().headers.get("Idempotency-Key")).toBeTruthy();
  });

  it("submits, polls, deletes, and builds a return-safe Discord login URL", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(finalization, 202))
      .mockResolvedValueOnce(jsonResponse(finalization))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const api = createSubmissionApi("en", config);
    await expect(api.submitDraft(draft.id)).resolves.toEqual(finalization);
    expect(capturedRequest().headers.get("Idempotency-Key")).toBeTruthy();
    await expect(api.submissionStatus(draft.id)).resolves.toEqual(finalization);
    await expect(api.deleteDraft(draft.id)).resolves.toBeUndefined();
    const signIn = new URL(api.signInUrl("https://catalogue.test/submit"));
    expect(signIn.pathname).toBe("/v1/auth/discord");
    expect(signIn.searchParams.get("redirect_to")).toBe("https://catalogue.test/submit");
  });
});

describe("submission problem mapping", () => {
  it("classifies authentication, consent, conflicts, service failures, and request errors", () => {
    expect(new SubmissionApiError(401).kind).toBe("authentication");
    expect(
      new SubmissionApiError(400, { title: "Consent", status: 400, code: "CONSENT_REQUIRED" }).kind,
    ).toBe("consent");
    expect(new SubmissionApiError(409).kind).toBe("conflict");
    expect(new SubmissionApiError(503).kind).toBe("unavailable");
    expect(new SubmissionApiError(400).kind).toBe("request");
  });

  it("preserves RFC problem detail and redacts unknown network failures", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ title: "Draft full", status: 409, detail: "Delete an old draft." }, 409),
    );
    const caught = await createSubmissionApi("en", config)
      .createDraft("door")
      .catch((error: unknown) => error);
    expect(caught).toMatchObject({
      status: 409,
      message: "Delete an old draft.",
      kind: "conflict",
    });
    const network = asSubmissionError(new TypeError("private socket detail"));
    expect(network).toMatchObject({ status: 503, kind: "unavailable", networkFailure: true });
    expect(asSubmissionError(caught)).toBe(caught);
  });
});

describe("Minecraft user-code approval transport", () => {
  it("retries once with the same key and sends no device credential", async () => {
    const approval = {
      id: "55555555-5555-4555-8555-555555555555",
      java_uuid: "66666666-6666-4666-8666-666666666666",
      origin: "fabric" as const,
      approved_at: "2026-08-11T00:00:00Z",
    };
    fetchMock
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(jsonResponse(approval));
    const api = createMinecraftLinkApi("en", config);
    await expect(api.approve("ABCD-EFGH-IJKL-MNOP")).resolves.toEqual(approval);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const first = capturedRequest(0);
    const second = capturedRequest(1);
    expect(new URL(first.url).pathname).toBe("/v1/minecraft/auth/challenges/approval");
    expect(first.headers.get("CSRF-Token")).toBe("csrf-token");
    expect(first.headers.get("Idempotency-Key")).toBe(second.headers.get("Idempotency-Key"));
    await expect(first.clone().json()).resolves.toEqual({ user_code: "ABCD-EFGH-IJKL-MNOP" });
    expect(JSON.stringify(await second.clone().json())).not.toMatch(/device|token/i);
    expect(api.signInUrl("https://catalogue.test/minecraft/link")).toContain("redirect_to=");
  });
});

describe("CLI user-code approval transport", () => {
  it("previews without mutation credentials and retry-safely approves only the short code", async () => {
    const approval = {
      id: "55555555-5555-4555-8555-555555555555",
      client_instance_id: "66666666-6666-4666-8666-666666666666",
      label: "Alice's workstation",
      public_key_fingerprint: "1234-5678-90AB-CDEF-1234",
      created_at: "2026-08-11T00:00:00Z",
      expires_at: "2026-08-11T00:10:00Z",
      approved_at: null,
    };
    fetchMock
      .mockResolvedValueOnce(jsonResponse(approval))
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(jsonResponse({ ...approval, approved_at: "2026-08-11T00:01:00Z" }));
    const api = createCliLinkApi("en", config);

    await expect(api.preview("ABCD-EFGH")).resolves.toEqual(approval);
    const previewRequest = capturedRequest(0);
    expect(previewRequest.method).toBe("GET");
    expect(new URL(previewRequest.url).searchParams.get("user_code")).toBe("ABCD-EFGH");
    expect(previewRequest.headers.get("Idempotency-Key")).toBeNull();

    await expect(api.approve("ABCD-EFGH")).resolves.toMatchObject({
      approved_at: expect.any(String),
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const first = capturedRequest(1);
    const second = capturedRequest(2);
    expect(first.method).toBe("POST");
    expect(first.headers.get("CSRF-Token")).toBe("csrf-token");
    expect(first.headers.get("Idempotency-Key")).toBe(second.headers.get("Idempotency-Key"));
    await expect(first.clone().json()).resolves.toEqual({ user_code: "ABCD-EFGH" });
    expect(JSON.stringify(await second.clone().json())).not.toMatch(
      /device_code|private_key|token/i,
    );
  });
});
