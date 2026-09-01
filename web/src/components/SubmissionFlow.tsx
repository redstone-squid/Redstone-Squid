import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import type {
  ChoiceOptionResponse,
  DraftChangeRequest,
  DraftMediaListResponse,
  DraftMediaResponse,
  FormFieldResponse,
  FormManifestResponse,
  MediaKind,
  StoredDraftResponse,
  SubmissionFinalizationResponse,
} from "../generated/types.gen";
import {
  asSubmissionError,
  withConsentRetry,
  type ConsentApi,
  createSubmissionApi,
  type SubmissionApi,
  SubmissionApiError,
} from "../lib/submission-api";
import type { RuntimeConfig } from "../lib/config";
import ConsentGate from "./ConsentGate";
import { type Locale, translate } from "../lib/i18n";
import {
  draftValue,
  fieldInputValue,
  isFieldVisible,
  issueMessage,
  materializedAnswers,
  parseFieldInput,
  sectionsForDraft,
  SubmissionSchemaError,
  validateSubmissionManifest,
  type DraftValue,
} from "../lib/submission-form";

const DRAFT_STORAGE_KEY = "squid:submission-draft:v1";
const CLIENT_STORAGE_KEY = "squid:submission-client:v1";
const POLL_INTERVAL_MS = 1_500;

type Props = {
  locale: Locale;
  config: RuntimeConfig;
  api?: SubmissionApi;
  consentApi?: ConsentApi;
};

type Copy = {
  loading: string;
  unavailableTitle: string;
  unavailable: string;
  incompatibleTitle: string;
  chooseTitle: string;
  chooseBody: string;
  category: string;
  start: string;
  signIn: string;
  auth: string;
  consent: string;
  retry: string;
  saving: string;
  saved: string;
  required: string;
  optional: string;
  onePerLine: string;
  chooseOption: string;
  choicesUnavailable: string;
  mediaTitle: string;
  mediaBody: string;
  mediaUnavailable: string;
  upload: string;
  uploadBusy: string;
  stripAudio: string;
  remove: string;
  noMedia: string;
  limits: string;
  submissionTitle: string;
  submissionBody: string;
  submit: string;
  submitting: string;
  pending: string;
  attention: string;
  completed: string;
  dead: string;
  deleteDraft: string;
  deleteConfirm: string;
  schematicNote: string;
  saveBeforeSubmit: string;
};

const copyFor = (locale: Locale): Copy => ({
  loading: translate(locale, "submitFlow.loading"),
  unavailableTitle: translate(locale, "submitFlow.unavailableTitle"),
  unavailable: translate(locale, "submitFlow.unavailable"),
  incompatibleTitle: translate(locale, "submitFlow.incompatibleTitle"),
  chooseTitle: translate(locale, "submitFlow.chooseTitle"),
  chooseBody: translate(locale, "submitFlow.chooseBody"),
  category: translate(locale, "submitFlow.category"),
  start: translate(locale, "submitFlow.start"),
  signIn: translate(locale, "submitFlow.signIn"),
  auth: translate(locale, "submitFlow.auth"),
  consent: translate(locale, "submitFlow.consent"),
  retry: translate(locale, "submitFlow.retry"),
  saving: translate(locale, "submitFlow.saving"),
  saved: translate(locale, "submitFlow.saved"),
  required: translate(locale, "submitFlow.required"),
  optional: translate(locale, "submitFlow.optional"),
  onePerLine: translate(locale, "submitFlow.onePerLine"),
  chooseOption: translate(locale, "submitFlow.chooseOption"),
  choicesUnavailable: translate(locale, "submitFlow.choicesUnavailable"),
  mediaTitle: translate(locale, "submitFlow.mediaTitle"),
  mediaBody: translate(locale, "submitFlow.mediaBody"),
  mediaUnavailable: translate(locale, "submitFlow.mediaUnavailable"),
  upload: translate(locale, "submitFlow.upload"),
  uploadBusy: translate(locale, "submitFlow.uploadBusy"),
  stripAudio: translate(locale, "submitFlow.stripAudio"),
  remove: translate(locale, "submitFlow.remove"),
  noMedia: translate(locale, "submitFlow.noMedia"),
  limits: translate(locale, "submitFlow.limits"),
  submissionTitle: translate(locale, "submitFlow.submissionTitle"),
  submissionBody: translate(locale, "submitFlow.submissionBody"),
  submit: translate(locale, "submitFlow.submit"),
  submitting: translate(locale, "submitFlow.submitting"),
  pending: translate(locale, "submitFlow.pending"),
  attention: translate(locale, "submitFlow.attention"),
  completed: translate(locale, "submitFlow.completed"),
  dead: translate(locale, "submitFlow.dead"),
  deleteDraft: translate(locale, "submitFlow.deleteDraft"),
  deleteConfirm: translate(locale, "submitFlow.deleteConfirm"),
  schematicNote: translate(locale, "submitFlow.schematicNote"),
  saveBeforeSubmit: translate(locale, "submitFlow.saveBeforeSubmit"),
});

function storedValue(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storeValue(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // A private draft still works when storage is blocked; it simply cannot resume after reload.
  }
}

function withoutKey<T>(values: Record<string, T>, key: string): Record<string, T> {
  return Object.fromEntries(Object.entries(values).filter(([candidate]) => candidate !== key));
}

function clientInstanceId(): string {
  const existing = storedValue(CLIENT_STORAGE_KEY);
  if (existing) return existing;
  const created = `web:${crypto.randomUUID()}`;
  storeValue(CLIENT_STORAGE_KEY, created);
  return created;
}

function readableBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${Math.round(bytes / (1024 * 1024))} MiB`;
  return `${Math.round(bytes / 1024)} KiB`;
}

function failureMessage(error: SubmissionApiError, copy: Copy): string {
  if (error.kind === "authentication") return copy.auth;
  if (error.kind === "consent") return copy.consent;
  if (error.kind === "unavailable") return copy.unavailable;
  return error.message;
}

function mediaFailureMessage(error: SubmissionApiError, copy: Copy): string {
  return error.kind === "unavailable" ? copy.mediaUnavailable : failureMessage(error, copy);
}

export default function SubmissionFlow({ locale, config, api: suppliedApi, consentApi }: Props) {
  // Memoized because the effects below take `copy` as a dependency: a fresh
  // object each render would re-run them forever.
  const copy = useMemo(() => copyFor(locale), [locale]);
  // The call a consent refusal interrupted, or null. Held in state rather than a ref because it
  // decides what renders: its presence *is* "show the notice".
  const [pendingRetry, setPendingRetry] = useState<(() => Promise<unknown>) | null>(null);
  const baseApi = useMemo(
    () => suppliedApi ?? createSubmissionApi(locale, config),
    [config, locale, suppliedApi],
  );
  // Any refused call is recoverable in place: the gate appears, and accepting re-runs exactly
  // the call that was refused rather than restarting the flow.
  const api = useMemo(
    () => withConsentRetry(baseApi, (retry) => setPendingRetry(() => retry)),
    [baseApi],
  );
  const [manifest, setManifest] = useState<FormManifestResponse>();
  const [draft, setDraft] = useState<StoredDraftResponse>();
  const draftRef = useRef<StoredDraftResponse | undefined>(undefined);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [selectedCategory, setSelectedCategory] = useState("door");
  const [options, setOptions] = useState<Record<string, ChoiceOptionResponse[]>>({});
  const [optionsError, setOptionsError] = useState(false);
  const [media, setMedia] = useState<DraftMediaListResponse>();
  const [mediaError, setMediaError] = useState<string>();
  const [finalization, setFinalization] = useState<SubmissionFinalizationResponse>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [stripAudio, setStripAudio] = useState(true);
  const [blockingError, setBlockingError] = useState<{ title: string; message: string }>();
  const [actionError, setActionError] = useState<SubmissionApiError>();
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({});
  const saveErrorsRef = useRef(new Set<string>());
  const [pendingFields, setPendingFields] = useState<Set<string>>(new Set());
  const saveQueue = useRef<Promise<void>>(Promise.resolve());
  const fieldVersions = useRef(new Map<string, number>());
  const clientId = useRef<string | undefined>(undefined);

  const installDraft = useCallback((next: StoredDraftResponse) => {
    draftRef.current = next;
    setDraft(next);
    setAnswers(next.answers);
    setSelectedCategory(next.category);
    storeValue(DRAFT_STORAGE_KEY, next.id);
  }, []);

  const loadEditorResources = useCallback(
    async (form: FormManifestResponse, current: StoredDraftResponse) => {
      const sections = sectionsForDraft(form, current);
      const sources = [
        ...new Set(
          sections.flatMap((section) =>
            section.fields.flatMap((field) => (field.option_source ? [field.option_source] : [])),
          ),
        ),
      ];
      setOptionsError(false);
      const optionTask = Promise.all(
        sources.map((source) => api.formOptions(source, current.category)),
      )
        .then((sets) =>
          setOptions(Object.fromEntries(sets.map((set) => [set.source, set.options]))),
        )
        .catch(() => setOptionsError(true));
      const mediaTask = api
        .listMedia(current.id)
        .then((result) => {
          setMedia(result);
          setMediaError(undefined);
        })
        .catch((error: unknown) =>
          setMediaError(mediaFailureMessage(asSubmissionError(error), copy)),
        );
      const statusTask = api
        .submissionStatus(current.id)
        .then(setFinalization)
        .catch((error: unknown) => {
          if (asSubmissionError(error).status !== 404) setActionError(asSubmissionError(error));
        });
      await Promise.all([optionTask, mediaTask, statusTask]);
    },
    [api, copy],
  );

  useEffect(() => {
    let active = true;
    void api
      .currentForm()
      .then(validateSubmissionManifest)
      .then(async (form) => {
        if (!active) return;
        setManifest(form);
        const draftId = storedValue(DRAFT_STORAGE_KEY);
        if (!draftId) return;
        try {
          const resumed = await api.getDraft(draftId);
          if (
            resumed.origin !== "web" ||
            resumed.schema_id !== form.schema_id ||
            resumed.schema_revision !== form.revision
          ) {
            throw new SubmissionSchemaError(
              "The saved draft uses a form revision this browser cannot safely resume.",
            );
          }
          installDraft(resumed);
          void loadEditorResources(form, resumed);
        } catch (error) {
          if (error instanceof SubmissionSchemaError) throw error;
          const failure = asSubmissionError(error);
          if (failure.status === 404) storeValue(DRAFT_STORAGE_KEY, null);
          else setActionError(failure);
        }
      })
      .catch((error: unknown) => {
        if (!active) return;
        if (error instanceof SubmissionSchemaError) {
          setBlockingError({ title: copy.incompatibleTitle, message: error.message });
        } else {
          setBlockingError({
            title: copy.unavailableTitle,
            message: failureMessage(asSubmissionError(error), copy),
          });
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api, copy, installDraft, loadEditorResources]);

  useEffect(() => {
    if (!draft || !media?.media.some((item) => item.status === "processing")) return;
    const timer = window.setInterval(() => {
      void api
        .listMedia(draft.id)
        .then((result) => {
          setMedia(result);
          setMediaError(undefined);
        })
        .catch((error: unknown) =>
          setMediaError(mediaFailureMessage(asSubmissionError(error), copy)),
        );
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [api, copy, draft, media]);

  useEffect(() => {
    if (!draft || !finalization || !new Set(["pending", "claimed"]).has(finalization.status)) {
      return;
    }
    const timer = window.setInterval(() => {
      void api
        .submissionStatus(draft.id)
        .then((result) => {
          setFinalization(result);
          setActionError(undefined);
        })
        .catch((error: unknown) => setActionError(asSubmissionError(error)));
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [api, draft, finalization]);

  const beginDraft = async () => {
    if (!manifest) return;
    setBusy(true);
    setActionError(undefined);
    try {
      const created = await api.createDraft(selectedCategory);
      installDraft(created);
      await loadEditorResources(manifest, created);
    } catch (error) {
      setActionError(asSubmissionError(error));
    } finally {
      setBusy(false);
    }
  };

  const commitField = (field: FormFieldResponse, value: DraftValue | undefined): void => {
    const current = draftRef.current;
    if (!current) return;
    const version = (fieldVersions.current.get(field.id) ?? 0) + 1;
    fieldVersions.current.set(field.id, version);
    setAnswers((existing) => {
      return value === undefined
        ? withoutKey(existing, field.id)
        : { ...existing, [field.id]: value };
    });
    saveErrorsRef.current.delete(field.id);
    setSaveErrors((existing) => {
      return withoutKey(existing, field.id);
    });
    setPendingFields((existing) => new Set(existing).add(field.id));

    const execute = async () => {
      const send = async (base: StoredDraftResponse, reuse?: DraftChangeRequest) => {
        const operationId = crypto.randomUUID();
        const idempotencyKey = crypto.randomUUID();
        const change: DraftChangeRequest =
          reuse ??
          ({
            base_revision: base.revision,
            client_instance_id: (clientId.current ??= clientInstanceId()),
            idempotency_key: idempotencyKey,
            operations: [
              value === undefined
                ? { operation_id: operationId, field_id: field.id, kind: "unset" }
                : { operation_id: operationId, field_id: field.id, kind: "set", value },
            ],
          } satisfies DraftChangeRequest);
        try {
          return await api.changeDraft(base.id, change);
        } catch (error) {
          const failure = asSubmissionError(error);
          if (failure.networkFailure && reuse === undefined) return await send(base, change);
          throw failure;
        }
      };

      try {
        let base = draftRef.current;
        if (!base) return;
        let result;
        try {
          result = await send(base);
        } catch (error) {
          const failure = asSubmissionError(error);
          if (failure.kind !== "conflict") throw failure;
          const latest = await api.getDraft(base.id);
          base = latest;
          draftRef.current = latest;
          setDraft(latest);
          setAnswers((local) => {
            const rebased = { ...latest.answers, ...local };
            return value === undefined
              ? withoutKey(rebased, field.id)
              : { ...rebased, [field.id]: value };
          });
          result = await send(latest);
        }
        draftRef.current = result.draft;
        setDraft(result.draft);
        setFinalization(undefined);
        if (fieldVersions.current.get(field.id) === version) {
          saveErrorsRef.current.delete(field.id);
          setSaveErrors((existing) => withoutKey(existing, field.id));
        }
      } catch (error) {
        const failure = asSubmissionError(error);
        if (fieldVersions.current.get(field.id) === version) {
          saveErrorsRef.current.add(field.id);
          setSaveErrors((existing) => ({ ...existing, [field.id]: failureMessage(failure, copy) }));
        }
      } finally {
        setPendingFields((existing) => {
          if (fieldVersions.current.get(field.id) !== version) return existing;
          const next = new Set(existing);
          next.delete(field.id);
          return next;
        });
      }
    };
    saveQueue.current = saveQueue.current.then(execute, execute);
  };

  const refreshMedia = async () => {
    if (!draft) return;
    try {
      setMedia(await api.listMedia(draft.id));
      setMediaError(undefined);
    } catch (error) {
      setMediaError(mediaFailureMessage(asSubmissionError(error), copy));
    }
  };

  const uploadFiles = async (files: FileList | null) => {
    if (!draft || !media || !files) return;
    setUploading(true);
    setMediaError(undefined);
    let imageCount = media.media.filter(
      (item) => item.kind === "image" && !new Set(["dead", "discarded"]).has(item.status),
    ).length;
    let videoCount = media.media.filter(
      (item) => item.kind === "video" && !new Set(["dead", "discarded"]).has(item.status),
    ).length;
    try {
      for (const file of [...files]) {
        const kind: MediaKind | undefined = file.type.startsWith("image/")
          ? "image"
          : file.type.startsWith("video/")
            ? "video"
            : undefined;
        if (!kind) throw new Error(`${file.name}: choose an image or video file.`);
        if (file.size < 1 || file.size > media.limits.max_upload_bytes) {
          throw new Error(
            `${file.name}: file size must be between 1 byte and ${readableBytes(media.limits.max_upload_bytes)}.`,
          );
        }
        if (
          (kind === "image" && imageCount >= media.limits.max_images) ||
          (kind === "video" && videoCount >= media.limits.max_videos)
        ) {
          throw new Error(`${file.name}: this draft has reached its ${kind} limit.`);
        }
        const uploaded = await api.uploadMedia(
          draft.id,
          kind,
          file,
          crypto.randomUUID(),
          stripAudio,
        );
        setMedia((current) =>
          current
            ? {
                ...current,
                media: [...current.media.filter((item) => item.id !== uploaded.id), uploaded],
              }
            : current,
        );
        if (kind === "image") imageCount += 1;
        else videoCount += 1;
      }
      await refreshMedia();
    } catch (error) {
      setMediaError(
        error instanceof SubmissionApiError
          ? mediaFailureMessage(error, copy)
          : error instanceof Error
            ? error.message
            : copy.mediaUnavailable,
      );
    } finally {
      setUploading(false);
    }
  };

  const discardMedia = async (item: DraftMediaResponse) => {
    if (!draft) return;
    setMediaError(undefined);
    try {
      await api.discardMedia(draft.id, item.id);
      await refreshMedia();
    } catch (error) {
      setMediaError(mediaFailureMessage(asSubmissionError(error), copy));
    }
  };

  const submit = async () => {
    if (!draft) return;
    setBusy(true);
    setActionError(undefined);
    await saveQueue.current;
    if (saveErrorsRef.current.size > 0) {
      setBusy(false);
      return;
    }
    try {
      setFinalization(await api.submitDraft(draft.id));
    } catch (error) {
      setActionError(asSubmissionError(error));
    } finally {
      setBusy(false);
    }
  };

  const deleteDraft = async () => {
    if (!draft || !window.confirm(copy.deleteConfirm)) return;
    setBusy(true);
    setActionError(undefined);
    try {
      await api.deleteDraft(draft.id);
      storeValue(DRAFT_STORAGE_KEY, null);
      draftRef.current = undefined;
      setDraft(undefined);
      setAnswers({});
      setMedia(undefined);
      setFinalization(undefined);
      setOptions({});
      fieldVersions.current.clear();
    } catch (error) {
      setActionError(asSubmissionError(error));
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <p role="status">{copy.loading}</p>;
  if (blockingError) {
    return (
      <section className="surface submission-alert" role="alert">
        <p className="kicker">FORM / STOPPED</p>
        <h2>{blockingError.title}</h2>
        <p>{blockingError.message}</p>
        <button className="button" type="button" onClick={() => window.location.reload()}>
          {copy.retry}
        </button>
      </section>
    );
  }
  if (!manifest) return null;

  // A blocking step, so it replaces the flow rather than sitting inside one of its branches:
  // nothing further can succeed until the notice is accepted, and the draft lives server-side,
  // so returning to it afterwards costs nothing.
  if (pendingRetry) {
    return (
      <ConsentGate
        locale={locale}
        config={config}
        api={consentApi}
        onAccepted={async () => {
          setPendingRetry(null);
          setActionError(undefined);
          // Its own failure is already reported through the normal error path.
          await pendingRetry().catch(() => undefined);
        }}
        onCancel={() => setPendingRetry(null)}
      />
    );
  }

  const signInUrl =
    typeof window === "undefined"
      ? api.signInUrl(config.siteUrl)
      : api.signInUrl(window.location.href);
  if (!draft) {
    return (
      <section className="surface submission-start">
        <p className="kicker">
          FORM / {manifest.schema_id} / R{manifest.revision}
        </p>
        <h2>{copy.chooseTitle}</h2>
        <p>{copy.chooseBody}</p>
        {actionError && <ActionError error={actionError} copy={copy} signInUrl={signInUrl} />}
        <div className="field">
          <label htmlFor="submission-category">{copy.category}</label>
          <select
            id="submission-category"
            value={selectedCategory}
            onChange={(event) => setSelectedCategory(event.currentTarget.value)}
          >
            {manifest.categories.map((category) => (
              <option key={category.code} value={category.code}>
                {category.label}
              </option>
            ))}
          </select>
        </div>
        <button
          className="button button--primary"
          type="button"
          disabled={busy}
          onClick={() => void beginDraft()}
        >
          {busy ? copy.loading : copy.start}
        </button>
      </section>
    );
  }

  const sections = sectionsForDraft(manifest, draft);
  const visibleAnswers = materializedAnswers(sections, answers);
  const fieldLabels = Object.fromEntries(
    sections.flatMap((section) => section.fields.map((field) => [field.id, field.label])),
  );
  const draftEditable = draft.status === "editing" || draft.status === "needs_attention";
  const locked =
    !draftEditable || finalization?.status === "pending" || finalization?.status === "claimed";

  return (
    <div className="submission-flow">
      <div className="submission-toolbar">
        <div>
          <p className="kicker">
            DRAFT / {draft.id.slice(0, 8)} / R{draft.revision}
          </p>
          <p className="submission-save-state" role="status">
            {pendingFields.size > 0 ? copy.saving : copy.saved}
          </p>
        </div>
        {draftEditable && finalization?.status !== "completed" && (
          <button
            className="button"
            type="button"
            disabled={busy || locked}
            onClick={() => void deleteDraft()}
          >
            {copy.deleteDraft}
          </button>
        )}
      </div>

      {actionError && <ActionError error={actionError} copy={copy} signInUrl={signInUrl} />}

      {sections.map((section) => {
        const visibleFields = section.fields.filter((field) =>
          isFieldVisible(field, visibleAnswers),
        );
        if (visibleFields.length === 0) return null;
        return (
          <section
            className="surface submission-section"
            key={section.id}
            aria-labelledby={`section-${section.id}`}
          >
            <h2 id={`section-${section.id}`}>{section.title}</h2>
            <div className="submission-fields">
              {visibleFields.map((field) => (
                <FieldEditor
                  key={field.id}
                  field={field}
                  value={visibleAnswers[field.id]}
                  options={field.option_source ? options[field.option_source] : field.options}
                  optionsLoading={Boolean(field.option_source && !options[field.option_source])}
                  pending={pendingFields.has(field.id)}
                  error={saveErrors[field.id]}
                  issue={finalization?.issues.find((issue) => issue.field_id === field.id)}
                  disabled={locked || finalization?.status === "completed"}
                  copy={copy}
                  locale={locale}
                  onCommit={(value) => commitField(field, value)}
                />
              ))}
            </div>
          </section>
        );
      })}

      {optionsError && (
        <p className="submission-inline-error" role="alert">
          {copy.choicesUnavailable}
        </p>
      )}

      <MediaSection
        copy={copy}
        media={media}
        error={mediaError}
        uploading={uploading}
        stripAudio={stripAudio}
        disabled={locked || finalization?.status === "completed"}
        onStripAudio={setStripAudio}
        onFiles={uploadFiles}
        onDiscard={discardMedia}
      />

      <section
        className="surface submission-section submission-finalization"
        aria-labelledby="submission-finalization"
      >
        <p className="kicker">FINALIZE / PENDING BUILD</p>
        <h2 id="submission-finalization">{copy.submissionTitle}</h2>
        <p>{copy.submissionBody}</p>
        <p className="submission-note">{copy.schematicNote}</p>
        {Object.keys(saveErrors).length > 0 && (
          <p className="submission-inline-error" role="alert">
            {copy.saveBeforeSubmit}
          </p>
        )}
        {finalization && (
          <FinalizationStatus
            value={finalization}
            copy={copy}
            locale={locale}
            fieldLabels={fieldLabels}
          />
        )}
        {finalization?.status !== "completed" && (
          <button
            className="button button--primary"
            type="button"
            disabled={busy || locked || pendingFields.size > 0}
            onClick={() => void submit()}
          >
            {busy ? copy.submitting : copy.submit}
          </button>
        )}
      </section>
    </div>
  );
}

function ActionError({
  error,
  copy,
  signInUrl,
}: {
  error: SubmissionApiError;
  copy: Copy;
  signInUrl: string;
}) {
  return (
    <div className="submission-inline-error" role="alert">
      <p>{failureMessage(error, copy)}</p>
      {error.kind === "authentication" && (
        <a className="button button--primary" href={signInUrl}>
          {copy.signIn}
        </a>
      )}
    </div>
  );
}

type FieldEditorProps = {
  field: FormFieldResponse;
  value: unknown;
  options?: ChoiceOptionResponse[];
  optionsLoading: boolean;
  pending: boolean;
  error?: string;
  issue?: SubmissionFinalizationResponse["issues"][number];
  disabled: boolean;
  copy: Copy;
  locale: Locale;
  onCommit: (value: DraftValue | undefined) => void;
};

function FieldEditor({
  field,
  value,
  options,
  optionsLoading,
  pending,
  error,
  issue,
  disabled,
  copy,
  locale,
  onCommit,
}: FieldEditorProps) {
  const id = useId();
  const [raw, setRaw] = useState(() => fieldInputValue(field, value));
  const [parseError, setParseError] = useState<string>();
  const describedBy =
    [field.help_text ? `${id}-help` : null, error || issue || parseError ? `${id}-error` : null]
      .filter(Boolean)
      .join(" ") || undefined;
  const commitRaw = () => {
    const parsed = parseFieldInput(field, raw);
    setParseError(parsed.error);
    if (!parsed.error) onCommit(parsed.value);
  };
  const state = pending ? copy.saving : field.required ? copy.required : copy.optional;
  const message =
    parseError ?? error ?? (issue ? issueMessage(issue, field.label, locale) : undefined);
  const invalid = message ? true : undefined;

  if (field.control === "boolean") {
    return (
      <div className="field submission-field submission-field--boolean">
        <label htmlFor={id}>
          <input
            id={id}
            type="checkbox"
            checked={draftValue(value) === true}
            disabled={disabled}
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => onCommit(event.currentTarget.checked)}
          />
          <span>
            {field.label} <small>({state})</small>
          </span>
        </label>
        {field.help_text && <small id={`${id}-help`}>{field.help_text}</small>}
        {message && (
          <small className="field-error" id={`${id}-error`} role="alert">
            {message}
          </small>
        )}
      </div>
    );
  }

  if (field.control === "multi_choice") {
    const selected = Array.isArray(draftValue(value)) ? (draftValue(value) as string[]) : [];
    return (
      <fieldset
        className="field submission-field"
        aria-invalid={invalid}
        aria-describedby={describedBy}
        disabled={disabled || optionsLoading}
      >
        <legend>
          {field.label} <small>({state})</small>
        </legend>
        <div className="submission-check-grid">
          {(options ?? []).map((option) => (
            <label key={option.value}>
              <input
                type="checkbox"
                checked={selected.includes(option.value)}
                onChange={(event) =>
                  onCommit(
                    event.currentTarget.checked
                      ? [...selected, option.value]
                      : selected.filter((value) => value !== option.value),
                  )
                }
              />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
        {optionsLoading && <small>{copy.loading}</small>}
        {field.help_text && <small id={`${id}-help`}>{field.help_text}</small>}
        {message && (
          <small className="field-error" id={`${id}-error`} role="alert">
            {message}
          </small>
        )}
      </fieldset>
    );
  }

  if (field.control === "choice") {
    return (
      <div className="field submission-field">
        <label htmlFor={id}>
          {field.label} <small>({state})</small>
        </label>
        <select
          id={id}
          value={typeof draftValue(value) === "string" ? String(value) : ""}
          required={field.required}
          disabled={disabled || optionsLoading}
          aria-invalid={invalid}
          aria-describedby={describedBy}
          onChange={(event) => onCommit(event.currentTarget.value || undefined)}
        >
          <option value="">{optionsLoading ? copy.loading : copy.chooseOption}</option>
          {(options ?? []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        {field.help_text && <small id={`${id}-help`}>{field.help_text}</small>}
        {message && (
          <small className="field-error" id={`${id}-error`} role="alert">
            {message}
          </small>
        )}
      </div>
    );
  }

  const multiline = field.repeatable || (field.constraints.max_length ?? 0) > 500;
  return (
    <div className={`field submission-field${multiline ? " submission-field--wide" : ""}`}>
      <label htmlFor={id}>
        {field.label} <small>({state})</small>
      </label>
      {multiline ? (
        <textarea
          id={id}
          value={raw}
          required={field.required}
          disabled={disabled}
          maxLength={field.repeatable ? undefined : (field.constraints.max_length ?? undefined)}
          aria-invalid={invalid}
          aria-describedby={describedBy}
          onChange={(event) => setRaw(event.currentTarget.value)}
          onBlur={commitRaw}
        />
      ) : (
        <input
          id={id}
          type={field.control === "number" ? "number" : "text"}
          inputMode={
            field.control === "number" || field.control === "duration" ? "decimal" : undefined
          }
          value={raw}
          required={field.required}
          disabled={disabled}
          min={field.constraints.minimum ?? undefined}
          max={field.constraints.maximum ?? undefined}
          minLength={field.constraints.min_length ?? undefined}
          maxLength={field.constraints.max_length ?? undefined}
          placeholder={field.control === "duration" ? "10gt / 5rt / 0.5s" : undefined}
          aria-invalid={invalid}
          aria-describedby={describedBy}
          onChange={(event) => setRaw(event.currentTarget.value)}
          onBlur={commitRaw}
        />
      )}
      {field.repeatable && <small>{copy.onePerLine}</small>}
      {field.help_text && <small id={`${id}-help`}>{field.help_text}</small>}
      {message && (
        <small className="field-error" id={`${id}-error`} role="alert">
          {message}
        </small>
      )}
    </div>
  );
}

function MediaSection({
  copy,
  media,
  error,
  uploading,
  stripAudio,
  disabled,
  onStripAudio,
  onFiles,
  onDiscard,
}: {
  copy: Copy;
  media?: DraftMediaListResponse;
  error?: string;
  uploading: boolean;
  stripAudio: boolean;
  disabled: boolean;
  onStripAudio: (value: boolean) => void;
  onFiles: (files: FileList | null) => Promise<void>;
  onDiscard: (item: DraftMediaResponse) => Promise<void>;
}) {
  const inputId = useId();
  return (
    <section className="surface submission-section" aria-labelledby="submission-media">
      <h2 id="submission-media">{copy.mediaTitle}</h2>
      <p>{copy.mediaBody}</p>
      {error && (
        <p className="submission-inline-error" role="alert">
          {error}
        </p>
      )}
      {media ? (
        <>
          <p className="submission-limit">
            {copy.limits
              .replace("{images}", String(media.limits.max_images))
              .replace("{videos}", String(media.limits.max_videos))
              .replace("{size}", readableBytes(media.limits.max_upload_bytes))}
          </p>
          <div className="submission-upload-controls">
            <label className="button" htmlFor={inputId}>
              {uploading ? copy.uploadBusy : copy.upload}
            </label>
            <input
              className="visually-hidden"
              id={inputId}
              type="file"
              accept="image/*,video/*"
              multiple
              disabled={disabled || uploading}
              onChange={(event) => {
                const input = event.currentTarget;
                void onFiles(input.files).finally(() => {
                  input.value = "";
                });
              }}
            />
            <label className="submission-checkbox">
              <input
                type="checkbox"
                checked={stripAudio}
                disabled={disabled || uploading}
                onChange={(event) => onStripAudio(event.currentTarget.checked)}
              />
              <span>{copy.stripAudio}</span>
            </label>
          </div>
          {media.media.filter((item) => item.status !== "discarded").length === 0 ? (
            <p>{copy.noMedia}</p>
          ) : (
            <ul className="submission-media-list">
              {media.media
                .filter((item) => item.status !== "discarded")
                .map((item) => (
                  <li key={item.id}>
                    <div>
                      <strong>{item.kind}</strong>
                      <span className={`submission-status submission-status--${item.status}`}>
                        {item.status}
                      </span>
                      {item.artifacts.map((artifact) => (
                        <small key={artifact.role}>
                          {artifact.role}:{" "}
                          {Number.isFinite(artifact.width) && Number.isFinite(artifact.height)
                            ? `${artifact.width}×${artifact.height} `
                            : ""}
                          {artifact.content_type}
                        </small>
                      ))}
                    </div>
                    <button
                      className="button"
                      type="button"
                      disabled={disabled}
                      onClick={() => void onDiscard(item)}
                    >
                      {copy.remove}
                    </button>
                  </li>
                ))}
            </ul>
          )}
        </>
      ) : (
        !error && <p role="status">{copy.loading}</p>
      )}
    </section>
  );
}

function FinalizationStatus({
  value,
  copy,
  locale,
  fieldLabels,
}: {
  value: SubmissionFinalizationResponse;
  copy: Copy;
  locale: Locale;
  fieldLabels: Record<string, string>;
}) {
  const message =
    value.status === "completed"
      ? copy.completed.replace("{id}", value.build_id === null ? "—" : String(value.build_id))
      : value.status === "needs_attention"
        ? copy.attention
        : value.status === "dead"
          ? copy.dead
          : copy.pending;
  return (
    <div className={`submission-result submission-result--${value.status}`} role="status">
      <p>
        <strong>{message}</strong>
      </p>
      {value.issues.length > 0 && (
        <ul>
          {value.issues.map((issue) => (
            <li key={`${issue.field_id}:${issue.reason}`}>
              {issueMessage(issue, fieldLabels[issue.field_id], locale)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
