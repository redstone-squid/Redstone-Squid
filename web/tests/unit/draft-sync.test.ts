import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api-client/api-error";
import { BrowserApi } from "@/api-client/mutation";
import {
  draftTransport,
  DraftSync,
  type DraftSnapshot,
  type DraftTransport,
} from "@/features/submissions/draft-sync";
import type { DraftChangeRequest, StoredDraftResponse } from "@/generated/types.gen";

function draft(revision: number, answers: StoredDraftResponse["answers"]): StoredDraftResponse {
  return {
    viewer: { can_edit: true, can_discard: true },
    id: "d1",
    schema_id: "door",
    schema_revision: 1,
    category: "door",
    revision,
    status: "editing",
    answers,
    origin: "web",
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    expires_at: "2026-12-01T00:00:00Z",
  };
}

const offline = () => new ApiError(503, undefined, { offline: true });
const conflict = () =>
  new ApiError(412, { title: "Changed", status: 412, code: "REVISION_CONFLICT" });

/** A server holding one draft that applies changes against its current revision. */
function fakeServer(initial: StoredDraftResponse) {
  let stored = initial;
  let loseNextResponse = false;
  const sent: DraftChangeRequest[] = [];
  const failures: ApiError[] = [];
  const applied = new Set<string>();

  function apply(body: DraftChangeRequest): StoredDraftResponse {
    sent.push(structuredClone(body));
    const failure = failures.shift();
    if (failure) throw failure;
    if (applied.has(body.idempotency_key)) return stored;
    if (body.base_revision !== stored.revision) throw conflict();
    applied.add(body.idempotency_key);
    const answers = new Map(Object.entries(stored.answers));
    for (const op of body.operations) {
      if (op.kind === "set") answers.set(op.field_id, op.value ?? null);
      else answers.delete(op.field_id);
    }
    stored = { ...stored, revision: stored.revision + 1, answers: Object.fromEntries(answers) };
    if (loseNextResponse) {
      loseNextResponse = false;
      throw offline();
    }
    return stored;
  }

  const transport: DraftTransport = {
    change: (body) => Promise.resolve(body).then(apply),
    load: () => Promise.resolve(stored),
  };
  return {
    transport,
    sent,
    /** The next calls to `change` fail with these errors. */
    fail: (...errors: ApiError[]) => failures.push(...errors),
    /** Another client saves a change. */
    remoteEdit: (answers: StoredDraftResponse["answers"]) => {
      stored = {
        ...stored,
        revision: stored.revision + 1,
        answers: { ...stored.answers, ...answers },
      };
    },
    /** Apply the next batch but lose its response, as a dropped connection would. */
    loseResponseOnce: () => {
      loseNextResponse = true;
    },
    get stored() {
      return stored;
    },
  };
}

function start(server: ReturnType<typeof fakeServer>, storage: Storage | null = sessionStorage) {
  let latest: DraftSnapshot | undefined;
  const sync = new DraftSync(server.stored, {
    transport: server.transport,
    storage,
    onChange: (snapshot) => {
      latest = snapshot;
    },
  });
  return { sync, latest: () => latest ?? sync.snapshot };
}

beforeEach(() => {
  vi.useFakeTimers();
  sessionStorage.clear();
});
afterEach(() => vi.useRealTimers());

describe("draft autosave queue", () => {
  it("coalesces typing into one batch with the field's latest value after 800 ms idle", async () => {
    const server = fakeServer(draft(1, {}));
    const { sync } = start(server);
    sync.edit({ field_id: "title", kind: "set", value: "Pis" }, "idle");
    await vi.advanceTimersByTimeAsync(500);
    sync.edit({ field_id: "title", kind: "set", value: "Piston door" }, "idle");
    await vi.advanceTimersByTimeAsync(799);
    expect(server.sent).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(server.sent).toHaveLength(1);
    expect(server.sent[0]?.operations).toMatchObject([{ field_id: "title", value: "Piston door" }]);
    expect(server.stored.answers).toEqual({ title: "Piston door" });
    expect(sync.snapshot.status).toBe("saved");
  });

  it("keeps one request in flight and sends later edits against the acknowledged revision", async () => {
    const server = fakeServer(draft(1, {}));
    const { sync } = start(server);
    sync.edit({ field_id: "width", kind: "set", value: 3 }, "now");
    await vi.advanceTimersByTimeAsync(0);
    const first = sync.flush();
    sync.edit({ field_id: "height", kind: "set", value: 4 }, "now");
    await first;
    await vi.advanceTimersByTimeAsync(0);
    expect(server.sent.map((body) => body.base_revision)).toEqual([1, 2]);
    expect(server.stored.answers).toEqual({ width: 3, height: 4 });
  });

  it("does not show server values over a field typed into while saving", async () => {
    const server = fakeServer(draft(1, { title: "Old" }));
    const { sync } = start(server);
    sync.edit({ field_id: "title", kind: "set", value: "New" }, "now");
    const saving = sync.flush();
    sync.edit({ field_id: "title", kind: "set", value: "Newer" }, "idle");
    await saving;
    expect(sync.snapshot.values.title).toBe("Newer");
  });

  it("rebases onto another client's change to a different field without a conflict", async () => {
    const server = fakeServer(draft(1, { title: "Door", width: 2 }));
    const { sync } = start(server);
    server.remoteEdit({ width: 5 });
    sync.edit({ field_id: "title", kind: "set", value: "Fast door" }, "now");
    await vi.advanceTimersByTimeAsync(0);
    expect(server.sent.map((body) => body.base_revision)).toEqual([1, 2]);
    // The rebased batch is a new request, so it needs its own key.
    expect(server.sent[1]?.idempotency_key).not.toBe(server.sent[0]?.idempotency_key);
    expect(server.stored.answers).toEqual({ title: "Fast door", width: 5 });
    expect(sync.snapshot.status).toBe("saved");
  });

  it("marks a field both sides changed as a conflict and keeps theirs until resolved", async () => {
    const server = fakeServer(draft(1, { title: "Door" }));
    const { sync, latest } = start(server);
    server.remoteEdit({ title: "Their door" });
    sync.edit({ field_id: "title", kind: "set", value: "My door" }, "now");
    await vi.advanceTimersByTimeAsync(0);
    expect(latest().status).toBe("conflict");
    expect(latest().conflicts.get("title")).toMatchObject({
      theirs: "Their door",
      mine: { value: "My door" },
    });
    expect(server.stored.answers.title).toBe("Their door");

    sync.resolve("title", "mine");
    await vi.advanceTimersByTimeAsync(0);
    expect(server.stored.answers.title).toBe("My door");
    expect(latest().status).toBe("saved");
  });

  it("drops the conflict without sending when I keep theirs", async () => {
    const server = fakeServer(draft(1, { title: "Door" }));
    const { sync } = start(server);
    server.remoteEdit({ title: "Their door" });
    sync.edit({ field_id: "title", kind: "set", value: "My door" }, "now");
    await vi.advanceTimersByTimeAsync(0);
    sync.resolve("title", "theirs");
    await vi.advanceTimersByTimeAsync(1000);
    expect(server.sent).toHaveLength(1);
    expect(sync.snapshot).toMatchObject({ status: "saved", values: { title: "Their door" } });
  });

  it("retries offline with the same key, backing off 1, 2, 4 s, and on the online event", async () => {
    const server = fakeServer(draft(1, {}));
    const { sync, latest } = start(server);
    const disconnect = sync.connect(window);
    server.fail(offline(), offline(), offline());
    sync.edit({ field_id: "width", kind: "set", value: 3 }, "now");
    await vi.advanceTimersByTimeAsync(0);
    expect(latest().status).toBe("offline");
    await vi.advanceTimersByTimeAsync(999);
    expect(server.sent).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1 + 2000);
    expect(server.sent).toHaveLength(3);
    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(0);
    expect(server.sent).toHaveLength(4);
    expect(new Set(server.sent.map((body) => body.idempotency_key)).size).toBe(1);
    expect(latest().status).toBe("saved");
    disconnect();
  });

  it("replays rather than reapplies a batch whose response was lost", async () => {
    const server = fakeServer(draft(1, { count: 1 }));
    const { sync } = start(server);
    server.loseResponseOnce();
    sync.edit({ field_id: "count", kind: "set", value: 2 }, "now");
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1000);
    expect(server.stored.revision).toBe(2);
    expect(sync.snapshot.status).toBe("saved");
  });

  it("restores unsent edits after a reload and sends them on connect", async () => {
    const server = fakeServer(draft(1, {}));
    const first = start(server);
    server.fail(offline());
    first.sync.edit({ field_id: "title", kind: "set", value: "Kept" }, "now");
    await vi.advanceTimersByTimeAsync(0);
    first.sync.edit({ field_id: "width", kind: "set", value: 3 }, "idle");

    const reloaded = start(server);
    expect(reloaded.sync.snapshot).toMatchObject({ restored: true, status: "pending" });
    reloaded.sync.connect(window);
    await vi.advanceTimersByTimeAsync(0);
    expect(server.stored.answers).toEqual({ title: "Kept", width: 3 });
    expect(sessionStorage.getItem("squid:draft:d1")).toBeNull();
  });

  it("asks before leaving only while edits are unsent", async () => {
    const server = fakeServer(draft(1, {}));
    const { sync } = start(server);
    sync.connect(window);
    const leave = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    sync.edit({ field_id: "title", kind: "set", value: "x" }, "idle");
    expect(leave()).toBe(true);
    await vi.advanceTimersByTimeAsync(800);
    expect(leave()).toBe(false);
  });

  it("returns a refused batch to the queue so a corrected value replaces it", async () => {
    const server = fakeServer(draft(1, {}));
    const { sync, latest } = start(server);
    server.fail(new ApiError(422, { title: "Invalid", status: 422 }));
    sync.edit({ field_id: "width", kind: "set", value: -1 }, "now");
    await vi.advanceTimersByTimeAsync(5000);
    expect(latest().status).toBe("error");
    expect(server.sent).toHaveLength(1);
    sync.edit({ field_id: "width", kind: "set", value: 3 }, "now");
    await vi.advanceTimersByTimeAsync(0);
    expect(server.sent[1]?.operations).toMatchObject([{ value: 3 }]);
    expect(latest().status).toBe("saved");
  });

  it("works without storage when the visitor blocks site data", async () => {
    const blocked: Storage = {
      length: 0,
      clear: () => undefined,
      key: () => null,
      getItem: () => {
        throw new DOMException("blocked", "SecurityError");
      },
      setItem: () => {
        throw new DOMException("blocked", "SecurityError");
      },
      removeItem: () => undefined,
    };
    const server = fakeServer(draft(1, {}));
    const { sync } = start(server, blocked);
    sync.edit({ field_id: "title", kind: "set", value: "x" }, "now");
    await vi.advanceTimersByTimeAsync(0);
    expect(server.stored.answers).toEqual({ title: "x" });
  });
});

describe("draft transport", () => {
  const API = "https://catalogue.test";
  const mockApi = setupServer();

  it("sends the batch's idempotency key as the header the API reserves", async () => {
    vi.useRealTimers();
    mockApi.listen({ onUnhandledFrame: "error" });
    let key: string | null = null;
    mockApi.use(
      http.get(`${API}/v1/auth/csrf`, () =>
        HttpResponse.json({ csrf_token: "token-1234567890ab" }),
      ),
      http.post(`${API}/v1/submissions/drafts/d1/changes`, ({ request }) => {
        key = request.headers.get("Idempotency-Key");
        return HttpResponse.json({ draft: draft(2, { title: "x" }), replayed: false });
      }),
    );
    try {
      const saved = await draftTransport(new BrowserApi(API), "d1").change({
        base_revision: 1,
        client_instance_id: "tab",
        idempotency_key: "batch-1",
        operations: [{ operation_id: "op", field_id: "title", kind: "set", value: "x" }],
      });
      expect(saved.revision).toBe(2);
      expect(key).toBe("batch-1");
    } finally {
      mockApi.close();
    }
  });
});
