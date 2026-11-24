import { isEqual } from "es-toolkit";
import { ApiError, unwrap } from "@/api-client/api-error";
import type { BrowserApi } from "@/api-client/mutation";
import { submissionDraftChange, submissionDraftGet } from "@/generated/sdk.gen";
import type {
  DraftChangeRequest,
  FieldOperationRequest,
  StoredDraftResponse,
  SubmissionAttentionIssueResponse,
} from "@/generated/types.gen";

type Answers = StoredDraftResponse["answers"];
type FieldValue = Answers[string];

/** One field edit. `unset` clears a field, empties an optional one, or hides a conditional one. */
export type DraftEdit = { field_id: string } & (
  { kind: "set"; value: FieldValue } | { kind: "unset" }
);

/** What `SaveStatus` shows. */
export type SyncStatus = "saved" | "pending" | "saving" | "offline" | "error" | "conflict";

/** A field both this tab and another client changed. The draft keeps theirs until resolved. */
export type FieldConflict = { mine: FieldOperationRequest; theirs: FieldValue | undefined };

export type DraftSnapshot = {
  status: SyncStatus;
  /** The stored answers with this tab's unsent edits on top: what the form should show. */
  values: Answers;
  conflicts: ReadonlyMap<string, FieldConflict>;
  issues: readonly SubmissionAttentionIssueResponse[];
  /** The queue was rebuilt from edits a previous page load left unsent. */
  restored: boolean;
  error: ApiError | null;
};

/** The two calls the queue makes. Both throw `ApiError`. */
export type DraftTransport = {
  change(body: DraftChangeRequest): Promise<StoredDraftResponse>;
  load(): Promise<StoredDraftResponse>;
};

export type DraftSyncOptions = {
  transport: DraftTransport;
  onChange: (snapshot: DraftSnapshot) => void;
  /** Mirrors unsent edits and the tab's client id; null where storage is blocked. */
  storage: Storage | null;
};

type Saved = {
  revision: number;
  answers: Answers;
  inFlight: DraftChangeRequest | null;
  pending: FieldOperationRequest[];
};

const TEXT_IDLE_MS = 800;
const MAX_BACKOFF_MS = 30_000;
const CLIENT_KEY = "squid:draft-client";

/**
 * The autosave queue for one draft, as components.md §5 "Draft autosave" specifies.
 *
 * Edits become operations; a field's newer unsent operation replaces its older one. One
 * `DraftChangeRequest` is in flight at a time, against the last acknowledged revision. A batch
 * keeps its idempotency key until the server answers it, so a retry after a lost response
 * replays rather than applying twice. On a revision conflict the queue reloads the draft and
 * rebases unsent operations onto it; a field the other side also changed becomes a conflict.
 * Unsent work survives a reload through `storage`.
 */
export class DraftSync {
  readonly #id: string;
  readonly #options: DraftSyncOptions;
  readonly #clientId: string;
  #revision: number;
  #answers: Answers;
  #issues: readonly SubmissionAttentionIssueResponse[];
  #inFlight: DraftChangeRequest | null = null;
  #pending = new Map<string, FieldOperationRequest>();
  readonly #conflicts = new Map<string, FieldConflict>();
  #sending = false;
  #error: ApiError | null = null;
  #failures = 0;
  #timer: ReturnType<typeof setTimeout> | undefined;
  readonly #restored: boolean;

  constructor(draft: StoredDraftResponse, options: DraftSyncOptions) {
    this.#id = draft.id;
    this.#options = options;
    this.#revision = draft.revision;
    this.#answers = draft.answers;
    this.#issues = draft.preparation_issues ?? [];
    this.#clientId = this.#read(CLIENT_KEY) ?? crypto.randomUUID();
    this.#write(CLIENT_KEY, this.#clientId);
    const saved = this.#read(this.#storageKey);
    const restored = saved === undefined ? undefined : (JSON.parse(saved) as Saved);
    if (restored) {
      // The saved base may be older than the page's draft; sending against it then conflicts
      // and rebases, which needs the answers that base had.
      this.#revision = restored.revision;
      this.#answers = restored.answers;
      this.#inFlight = restored.inFlight;
      this.#pending = new Map(restored.pending.map((op) => [op.field_id, op]));
    }
    this.#restored = restored !== undefined;
  }

  get snapshot(): DraftSnapshot {
    const values = new Map(Object.entries(this.#answers));
    for (const op of [...(this.#inFlight?.operations ?? []), ...this.#pending.values()]) {
      if (op.kind === "set") values.set(op.field_id, op.value ?? null);
      else values.delete(op.field_id);
    }
    return {
      status: this.#status(),
      values: Object.fromEntries(values),
      conflicts: new Map(this.#conflicts),
      issues: this.#issues,
      restored: this.#restored,
      error: this.#error,
    };
  }

  /** True while an edit has not reached the server; the page guards `beforeunload` with it. */
  get unsent(): boolean {
    return this.#inFlight !== null || this.#pending.size > 0;
  }

  /**
   * Queue an edit. Text sends after `TEXT_IDLE_MS` without typing (or on `flush()` at blur);
   * choices, toggles and uploads pass `"now"`. Editing a conflicted field settles it as mine.
   */
  edit(edit: DraftEdit, when: "idle" | "now"): void {
    this.#conflicts.delete(edit.field_id);
    this.#pending.set(edit.field_id, { ...edit, operation_id: crypto.randomUUID() });
    this.#changed();
    this.#schedule(when === "now" ? 0 : TEXT_IDLE_MS);
  }

  /** Settle a conflict: resend my value, or keep the one the draft already has. */
  resolve(fieldId: string, choice: "mine" | "theirs"): void {
    const conflict = this.#conflicts.get(fieldId);
    if (!conflict) return;
    this.#conflicts.delete(fieldId);
    if (choice === "mine") {
      this.#pending.set(fieldId, { ...conflict.mine, operation_id: crypto.randomUUID() });
      this.#schedule(0);
    }
    this.#changed();
  }

  /**
   * Send now: the batch waiting on a failure, else the pending edits. Does nothing while a
   * request is in flight; the queue sends the rest when it returns.
   */
  async flush(): Promise<void> {
    clearTimeout(this.#timer);
    if (this.#sending) return;
    if (this.#inFlight === null) {
      if (this.#pending.size === 0) return;
      this.#inFlight = {
        base_revision: this.#revision,
        client_instance_id: this.#clientId,
        idempotency_key: crypto.randomUUID(),
        operations: this.#pending.values().toArray(),
      };
      this.#pending.clear();
    }
    this.#sending = true;
    this.#changed();
    try {
      this.#accept(await this.#options.transport.change(this.#inFlight));
    } catch (caught) {
      await this.#recover(ApiError.from(caught));
    } finally {
      this.#sending = false;
      this.#changed();
    }
    if (this.#error === null && this.#pending.size > 0) await this.flush();
  }

  /**
   * Start syncing: send edits a previous load left unsent, retry when the browser comes back
   * online, and ask before leaving while edits are unsent. Returns the cleanup.
   */
  connect(target: Window): () => void {
    const online = (): void => void this.flush();
    const leaving = (event: BeforeUnloadEvent): void => {
      if (this.unsent) event.preventDefault();
    };
    target.addEventListener("online", online);
    target.addEventListener("beforeunload", leaving);
    if (this.unsent) void this.flush();
    return () => {
      clearTimeout(this.#timer);
      target.removeEventListener("online", online);
      target.removeEventListener("beforeunload", leaving);
    };
  }

  #accept(draft: StoredDraftResponse): void {
    this.#inFlight = null;
    this.#revision = draft.revision;
    this.#answers = draft.answers;
    this.#issues = draft.preparation_issues ?? [];
    this.#error = null;
    this.#failures = 0;
  }

  async #recover(failure: ApiError): Promise<void> {
    let error = failure;
    if (error.conflict) {
      try {
        this.#rebase(await this.#options.transport.load());
        return;
      } catch (caught) {
        error = ApiError.from(caught);
      }
    }
    this.#error = error;
    if (error.offline || error.status >= 500) {
      // Keep the batch and its key: the server may have applied it before the response was lost.
      this.#failures += 1;
      this.#schedule(Math.min(1000 * 2 ** (this.#failures - 1), MAX_BACKOFF_MS));
    } else if (this.#inFlight) {
      // The server refused this batch, so resending it cannot succeed. Return its operations
      // to the queue behind newer edits, so fixing a field replaces the refused value.
      for (const op of this.#inFlight.operations) {
        if (!this.#pending.has(op.field_id)) this.#pending.set(op.field_id, op);
      }
      this.#inFlight = null;
    }
  }

  #rebase(remote: StoredDraftResponse): void {
    const local = new Map<string, FieldOperationRequest>();
    for (const op of [...(this.#inFlight?.operations ?? []), ...this.#pending.values()]) {
      local.set(op.field_id, op);
    }
    const base = this.#answers;
    this.#pending = new Map();
    for (const op of local.values()) {
      const theirs = remote.answers[op.field_id];
      // A change that already landed, such as our own batch whose response was lost.
      if (isEqual(theirs, op.kind === "set" ? op.value : undefined)) continue;
      if (isEqual(theirs, base[op.field_id])) this.#pending.set(op.field_id, op);
      else this.#conflicts.set(op.field_id, { mine: op, theirs });
    }
    this.#accept(remote);
  }

  #status(): SyncStatus {
    if (this.#conflicts.size > 0) return "conflict";
    if (this.#error) return this.#error.offline ? "offline" : "error";
    if (this.#sending) return "saving";
    return this.unsent ? "pending" : "saved";
  }

  #schedule(delay: number): void {
    clearTimeout(this.#timer);
    this.#timer = setTimeout(() => void this.flush(), delay);
  }

  #changed(): void {
    if (this.unsent) {
      const saved: Saved = {
        revision: this.#revision,
        answers: this.#answers,
        inFlight: this.#inFlight,
        pending: this.#pending.values().toArray(),
      };
      this.#write(this.#storageKey, JSON.stringify(saved));
    } else {
      this.#write(this.#storageKey, undefined);
    }
    this.#options.onChange(this.snapshot);
  }

  get #storageKey(): string {
    return `squid:draft:${this.#id}`;
  }

  // Storage can throw when the visitor blocks site data; the queue then just isn't mirrored.
  #read(key: string): string | undefined {
    try {
      return this.#options.storage?.getItem(key) ?? undefined;
    } catch {
      return undefined;
    }
  }

  #write(key: string, value: string | undefined): void {
    try {
      if (value === undefined) this.#options.storage?.removeItem(key);
      else this.#options.storage?.setItem(key, value);
    } catch {
      // See #read.
    }
  }
}

/** The real calls for one draft, sent with the write headers `BrowserApi` adds. */
export function draftTransport(api: BrowserApi, draftId: string): DraftTransport {
  const path = { draft_id: draftId };
  return {
    change: async (body) =>
      (
        await api.mutate(
          (headers) => submissionDraftChange({ client: api.client, path, body, headers }),
          { idempotencyKey: body.idempotency_key },
        )
      ).data.draft,
    load: async () => unwrap(await submissionDraftGet({ client: api.client, path })),
  };
}
