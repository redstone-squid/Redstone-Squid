import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SubmissionFlow from "../../src/components/SubmissionFlow";
import type {
  DraftMediaListResponse,
  FormFieldResponse,
  FormManifestResponse,
  StoredDraftResponse,
} from "../../src/generated/types.gen";
import { SubmissionApiError, type SubmissionApi } from "../../src/lib/submission-api";
import type { RuntimeConfig } from "../../src/lib/config";

const config: RuntimeConfig = {
  apiBaseUrl: "https://api.catalogue.test",
  siteUrl: "https://catalogue.test",
  discordCommunityUrl: "https://discord.gg/redstone",
  botInviteUrl: "https://discord.com/oauth2/authorize",
};

const constraints = {
  minimum: null,
  maximum: null,
  min_length: null,
  max_length: null,
  min_items: null,
  max_items: null,
  must_equal: null,
};

function field(overrides: Partial<FormFieldResponse>): FormFieldResponse {
  return {
    id: "display_name",
    label: "Display name",
    control: "text",
    value_kind: "string",
    required: false,
    help_text: null,
    constraints,
    options: [],
    option_source: null,
    visible_when: null,
    default: null,
    repeatable: false,
    required_capability: null,
    origins: ["web"],
    ...overrides,
  };
}

const manifest: FormManifestResponse = {
  schema_id: "build_submission.v1",
  revision: 1,
  minimum_protocol: 1,
  maximum_protocol: 1,
  common_sections: [
    {
      id: "identity",
      title: "Build identity",
      fields: [
        field({}),
        field({
          id: "description",
          label: "Description",
          constraints: { ...constraints, max_length: 4_000 },
        }),
        field({
          id: "creators",
          label: "Creators",
          required: true,
          repeatable: true,
          value_kind: "string_list",
          required_capability: "repeatable_text",
        }),
      ],
    },
    {
      id: "rights",
      title: "Rights",
      fields: [
        field({
          id: "schematic_visibility",
          label: "Schematic visibility",
          control: "choice",
          required: true,
          options: [
            { value: "reviewer_only", label: "Reviewer only" },
            { value: "public_download", label: "Public download" },
          ],
        }),
        field({
          id: "schematic_license",
          label: "Public schematic license",
          control: "choice",
          options: [{ value: "cc0_1_0", label: "CC0 1.0" }],
          visible_when: {
            field_id: "schematic_visibility",
            operator: "equals",
            value: "public_download",
          },
        }),
        field({
          id: "rights_attestation",
          label: "I have permission",
          control: "boolean",
          value_kind: "boolean",
          visible_when: {
            field_id: "schematic_visibility",
            operator: "equals",
            value: "public_download",
          },
        }),
      ],
    },
    {
      id: "mechanics",
      title: "Mechanics",
      fields: [
        field({
          id: "opening_time",
          label: "Opening time",
          control: "duration",
          value_kind: "game_ticks",
        }),
        field({
          id: "restrictions",
          label: "Known restrictions",
          control: "multi_choice",
          value_kind: "string_list",
          option_source: "approved_restrictions",
        }),
        field({
          id: "ai_generated",
          label: "AI-generated or AI-assisted",
          control: "boolean",
          value_kind: "boolean",
          required: true,
          default: false,
        }),
        field({
          id: "sponsor_attribution",
          label: "Sponsor",
          control: "boolean",
          value_kind: "boolean",
          origins: ["paper"],
        }),
      ],
    },
  ],
  categories: [
    {
      code: "door",
      label: "Door",
      sections: [
        {
          id: "door_geometry",
          title: "Door geometry",
          fields: [
            field({
              id: "opening_width",
              label: "Opening width",
              control: "number",
              value_kind: "integer",
              required: true,
              constraints: { ...constraints, minimum: 1, maximum: 512 },
            }),
          ],
        },
      ],
    },
    { code: "extender", label: "Extender", sections: [] },
    { code: "utility", label: "Utility", sections: [] },
    { code: "entrance", label: "Entrance", sections: [] },
    { code: "other", label: "Other", sections: [] },
  ],
};

function storedDraft(overrides: Partial<StoredDraftResponse> = {}): StoredDraftResponse {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    schema_id: manifest.schema_id,
    schema_revision: manifest.revision,
    category: "door",
    revision: 0,
    status: "editing",
    answers: {},
    origin: "web",
    created_at: "2026-08-11T00:00:00Z",
    updated_at: "2026-08-11T00:00:00Z",
    expires_at: "2026-09-11T00:00:00Z",
    ...overrides,
  };
}

const emptyMedia: DraftMediaListResponse = {
  limits: {
    max_upload_bytes: 10_000,
    max_images: 10,
    max_videos: 3,
    max_output_bytes: 20_000,
    max_duration_milliseconds: 300_000,
    max_pixels_per_frame: 33_200_000,
    max_decoded_pixels_per_second: 250_000_000,
  },
  media: [],
};

function fakeApi(overrides: Partial<SubmissionApi> = {}): SubmissionApi {
  let serverDraft = storedDraft();
  return {
    currentForm: vi.fn(() => Promise.resolve(manifest)),
    formOptions: vi.fn((source, category) =>
      Promise.resolve({
        source,
        category,
        revision: 1,
        options: [{ value: "locational", label: "Locational" }],
      }),
    ),
    createDraft: vi.fn((category) => {
      serverDraft = storedDraft({ category });
      return Promise.resolve(serverDraft);
    }),
    getDraft: vi.fn(() => Promise.resolve(serverDraft)),
    changeDraft: vi.fn<SubmissionApi["changeDraft"]>((_draftId, change) => {
      const answers = { ...serverDraft.answers };
      for (const operation of change.operations) {
        if (operation.kind === "unset") Reflect.deleteProperty(answers, operation.field_id);
        else answers[operation.field_id] = operation.value;
      }
      serverDraft = { ...serverDraft, answers, revision: serverDraft.revision + 1 };
      return Promise.resolve({ draft: serverDraft, replayed: false });
    }),
    deleteDraft: vi.fn(() => Promise.resolve()),
    listMedia: vi.fn(() => Promise.resolve(emptyMedia)),
    uploadMedia: vi.fn<SubmissionApi["uploadMedia"]>((_draftId, kind, file, uploadId) =>
      Promise.resolve({
        id: uploadId,
        draft_id: serverDraft.id,
        kind,
        status: "processing",
        source_content_type: file.type,
        artifacts: [],
      }),
    ),
    discardMedia: vi.fn(() => Promise.resolve()),
    submitDraft: vi.fn<SubmissionApi["submitDraft"]>(() =>
      Promise.resolve({
        draft_id: serverDraft.id,
        draft_revision: serverDraft.revision,
        status: "needs_attention",
        issues: [{ field_id: "display_name", reason: "required" }],
        build_id: null,
      }),
    ),
    submissionStatus: vi.fn(() => Promise.reject(new SubmissionApiError(404))),
    signInUrl: vi.fn(
      (returnTo) =>
        `https://api.catalogue.test/v1/auth/discord?redirect_to=${encodeURIComponent(returnTo)}`,
    ),
    ...overrides,
  };
}

beforeEach(() => localStorage.clear());

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("SubmissionFlow", { timeout: 15_000 }, () => {
  it("offers all five categories, never invents type_label, and creates the selected draft", async () => {
    const api = fakeApi();
    render(<SubmissionFlow locale="en" config={config} api={api} />);
    expect(await screen.findByRole("heading", { name: "Choose a build category" })).toBeVisible();
    const category = screen.getByLabelText("Build category");
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Door",
      "Extender",
      "Utility",
      "Entrance",
      "Other",
    ]);
    expect(screen.queryByText(/type label/i)).not.toBeInTheDocument();
    fireEvent.change(category, { target: { value: "other" } });
    fireEvent.click(screen.getByRole("button", { name: "Start draft" }));
    expect(api.createDraft).toHaveBeenCalledWith("other");
    expect(await screen.findByRole("heading", { name: "Build identity" })).toBeVisible();
    expect(screen.queryByText("Sponsor")).not.toBeInTheDocument();
  });

  it("fails clearly on authentication, consent, service failure, and incompatible schema", async () => {
    const user = userEvent.setup();
    const authApi = fakeApi({
      createDraft: vi.fn(() => Promise.reject(new SubmissionApiError(401))),
    });
    const { unmount } = render(<SubmissionFlow locale="en" config={config} api={authApi} />);
    await user.click(await screen.findByRole("button", { name: "Start draft" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sign in with Discord");
    expect(screen.getByRole("link", { name: "Sign in with Discord" })).toHaveAttribute(
      "href",
      expect.stringContaining("/v1/auth/discord"),
    );
    unmount();

    const consentApi = fakeApi({
      createDraft: vi.fn(() =>
        Promise.reject(
          new SubmissionApiError(400, { title: "Consent", status: 400, code: "CONSENT_REQUIRED" }),
        ),
      ),
    });
    const consent = render(<SubmissionFlow locale="en" config={config} api={consentApi} />);
    await user.click(await screen.findByRole("button", { name: "Start draft" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("privacy notice");
    consent.unmount();

    const unavailable = fakeApi({
      currentForm: vi.fn(() => Promise.reject(new SubmissionApiError(503))),
    });
    const service = render(<SubmissionFlow locale="en" config={config} api={unavailable} />);
    expect(
      await screen.findByRole("heading", { name: "Submission service unavailable" }),
    ).toBeVisible();
    service.unmount();

    const incompatible = fakeApi({
      currentForm: vi.fn(() =>
        Promise.resolve({ ...manifest, minimum_protocol: 2, maximum_protocol: 2 }),
      ),
    });
    render(<SubmissionFlow locale="en" config={config} api={incompatible} />);
    expect(
      await screen.findByRole("heading", { name: "This browser cannot render the current form" }),
    ).toBeVisible();
  });

  it("renders dynamic and conditional controls and saves typed canonical values", async () => {
    const api = fakeApi();
    render(<SubmissionFlow locale="en" config={config} api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start draft" }));
    const name = await screen.findByLabelText(/Display name/);
    fireEvent.change(name, { target: { value: "Compact Door" } });
    fireEvent.blur(name);
    await waitFor(() => expect(api.changeDraft).toHaveBeenCalled());

    expect(screen.queryByLabelText(/Public schematic license/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Schematic visibility/), {
      target: { value: "public_download" },
    });
    expect(await screen.findByLabelText(/Public schematic license/)).toBeVisible();
    fireEvent.change(screen.getByLabelText(/Public schematic license/), {
      target: { value: "cc0_1_0" },
    });
    fireEvent.click(screen.getByLabelText(/I have permission/));
    fireEvent.click(await screen.findByLabelText("Locational"));

    const duration = screen.getByLabelText(/Opening time/);
    fireEvent.change(duration, { target: { value: "fast" } });
    fireEvent.blur(duration);
    expect(await screen.findByText(/explicit unit/)).toBeVisible();
    fireEvent.change(duration, { target: { value: "0.5s" } });
    fireEvent.blur(duration);
    await waitFor(() =>
      expect(api.changeDraft).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          operations: [expect.objectContaining({ field_id: "opening_time", value: 10 })],
        }),
      ),
    );
    expect(screen.getByText(/does not attach or claim to sanitize schematics/i)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Submit draft" }));
    expect(await screen.findAllByText("Display name is required.")).toHaveLength(2);
    expect(screen.getByText(/needs attention/)).toBeVisible();
  });

  it("resumes a retained draft and renders a terminal stable finalization issue", async () => {
    const retained = storedDraft({
      revision: 3,
      status: "needs_attention",
      answers: { display_name: "Retained Door", ai_generated: false },
    });
    localStorage.setItem("squid:submission-draft:v1", retained.id);
    const api = fakeApi({
      getDraft: vi.fn(() => Promise.resolve(retained)),
      listMedia: vi.fn(() => Promise.reject(new SubmissionApiError(503))),
      submissionStatus: vi.fn<SubmissionApi["submissionStatus"]>(() =>
        Promise.resolve({
          draft_id: retained.id,
          draft_revision: retained.revision,
          status: "dead",
          issues: [{ field_id: "submission", reason: "retry_exhausted" }],
          build_id: null,
        }),
      ),
    });
    render(<SubmissionFlow locale="en" config={config} api={api} />);
    expect(await screen.findByDisplayValue("Retained Door")).toBeVisible();
    expect(screen.getByText(/could not complete/)).toBeVisible();
    expect(screen.getByText(/several retries/)).toBeVisible();
    expect(screen.getByText(/Media processing is temporarily unavailable/)).toBeVisible();
    expect(api.createDraft).not.toHaveBeenCalled();
  });

  it("rejects unsupported and oversized media before transport", async () => {
    const limited = {
      ...emptyMedia,
      limits: { ...emptyMedia.limits, max_upload_bytes: 3 },
    };
    const api = fakeApi({ listMedia: vi.fn(() => Promise.resolve(limited)) });
    render(<SubmissionFlow locale="en" config={config} api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start draft" }));
    const input = await screen.findByLabelText("Add images or video");
    fireEvent.change(input, {
      target: { files: [new File(["txt"], "notes.txt", { type: "text/plain" })] },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("choose an image or video");
    fireEvent.change(input, {
      target: { files: [new File(["large"], "large.png", { type: "image/png" })] },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("between 1 byte and");
    expect(api.uploadMedia).not.toHaveBeenCalled();
  });

  it("lets the latest rapid edit own a field's pending and error state", async () => {
    const saved = storedDraft({ revision: 1, answers: { display_name: "New" } });
    const changeDraft = vi
      .fn<SubmissionApi["changeDraft"]>()
      .mockRejectedValueOnce(new SubmissionApiError(503))
      .mockResolvedValueOnce({ draft: saved, replayed: false });
    const api = fakeApi({ changeDraft });
    render(<SubmissionFlow locale="en" config={config} api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start draft" }));
    const name = await screen.findByLabelText(/Display name/);
    fireEvent.change(name, { target: { value: "Old" } });
    fireEvent.blur(name);
    fireEvent.change(name, { target: { value: "New" } });
    fireEvent.blur(name);

    await waitFor(() => expect(changeDraft).toHaveBeenCalledTimes(2));
    expect(changeDraft.mock.calls[0]?.[1].operations[0]).toMatchObject({ value: "Old" });
    expect(changeDraft.mock.calls[1]?.[1].operations[0]).toMatchObject({ value: "New" });
    expect(await screen.findByText("All changes saved")).toBeVisible();
    expect(screen.queryByText(/Fix or retry the unsaved fields/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Submit draft" }));
    await waitFor(() => expect(api.submitDraft).toHaveBeenCalledOnce());
  });

  it("rebases one optimistic edit after a revision conflict", async () => {
    const latest = storedDraft({ revision: 4, answers: { description: "Saved elsewhere" } });
    const changed = {
      ...latest,
      revision: 5,
      answers: { ...latest.answers, display_name: "Door" },
    };
    const changeDraft = vi
      .fn<SubmissionApi["changeDraft"]>()
      .mockRejectedValueOnce(new SubmissionApiError(409))
      .mockResolvedValueOnce({ draft: changed, replayed: false });
    const api = fakeApi({ changeDraft, getDraft: vi.fn(() => Promise.resolve(latest)) });
    render(<SubmissionFlow locale="en" config={config} api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start draft" }));
    fireEvent.change(await screen.findByLabelText(/Display name/), { target: { value: "Door" } });
    fireEvent.blur(screen.getByLabelText(/Display name/));
    await waitFor(() => expect(changeDraft).toHaveBeenCalledTimes(2));
    expect(changeDraft.mock.calls[1]?.[1].base_revision).toBe(4);
    expect(await screen.findByText("All changes saved")).toBeVisible();
  });

  it("uploads, lists, discards, submits, polls completion, and confirms deletion once", async () => {
    let media = emptyMedia;
    const uploaded = {
      id: "22222222-2222-4222-8222-222222222222",
      draft_id: storedDraft().id,
      kind: "image" as const,
      status: "completed" as const,
      source_content_type: "image/png",
      artifacts: [{ role: "output" as const, content_type: "image/webp", width: 320, height: 240 }],
    };
    const listMedia = vi.fn(() => Promise.resolve(media));
    const uploadMedia = vi.fn<SubmissionApi["uploadMedia"]>(() => {
      media = { ...emptyMedia, media: [uploaded] };
      return Promise.resolve(uploaded);
    });
    const submitDraft = vi.fn<SubmissionApi["submitDraft"]>(() =>
      Promise.resolve({
        draft_id: storedDraft().id,
        draft_revision: 0,
        status: "pending",
        issues: [],
        build_id: null,
      }),
    );
    const api = fakeApi({ listMedia, uploadMedia, submitDraft });
    render(<SubmissionFlow locale="en" config={config} api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start draft" }));
    const upload = await screen.findByLabelText("Add images or video");
    fireEvent.change(upload, {
      target: { files: [new File(["png"], "door.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(uploadMedia).toHaveBeenCalled());
    expect(await screen.findByText(/320×240 image\/webp/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() =>
      expect(api.discardMedia).toHaveBeenCalledWith(storedDraft().id, uploaded.id),
    );

    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Submit draft" }));
    await act(async () => Promise.resolve());
    expect(screen.getByText(/being finalized/)).toBeVisible();
    vi.mocked(api.submissionStatus).mockResolvedValue({
      draft_id: storedDraft().id,
      draft_revision: 0,
      status: "completed",
      issues: [],
      build_id: 42,
    });
    await act(async () => vi.advanceTimersByTimeAsync(1_500));
    expect(screen.getByText(/Build #42 was created/)).toBeVisible();
    expect(screen.queryByRole("link", { name: /View build/ })).not.toBeInTheDocument();
    vi.useRealTimers();

    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    // Completed submissions intentionally cannot be deleted, so restart with a retained editable draft.
    localStorage.setItem("squid:submission-draft:v1", storedDraft().id);
    const editableApi = fakeApi({
      getDraft: vi.fn(() => Promise.resolve(storedDraft())),
      submissionStatus: vi.fn(() => Promise.reject(new SubmissionApiError(404))),
    });
    const editable = render(<SubmissionFlow locale="en" config={config} api={editableApi} />);
    await screen.findAllByRole("button", { name: "Delete draft" });
    const deleteButtons = screen.getAllByRole("button", { name: "Delete draft" });
    const deleteButton = deleteButtons.at(-1);
    if (!deleteButton) throw new Error("Delete button did not render.");
    fireEvent.click(deleteButton);
    await waitFor(() => expect(editableApi.deleteDraft).toHaveBeenCalledOnce());
    expect(confirm).toHaveBeenCalledOnce();
    editable.unmount();
  });
});
