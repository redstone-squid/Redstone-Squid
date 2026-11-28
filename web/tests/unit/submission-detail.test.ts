import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, expect, it } from "vitest";
import DraftAttachments from "@/features/submissions/DraftAttachments.svelte";
import { factRows, statusLabel } from "@/features/submissions/status";

const baseUrl = "https://test.invalid";
const server = setupServer(
  http.get(`${baseUrl}/v1/auth/csrf`, () => HttpResponse.json({ csrf_token: "token" })),
);
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterAll(() => server.close());

it("labels every submission outcome", () => {
  expect(["pending", "approved", "rejected", "cancelled", "stale"].map(statusLabel)).toEqual([
    "Awaiting review",
    "Approved",
    "Rejected",
    "Cancelled",
    "Needs fresh review",
  ]);
});

it("labels submitted facts, keeping zero and false and reading sizes and credits", () => {
  expect(
    factRows({
      dimensions: { width: 9, height: null, depth: 4 },
      opening_time: 0,
      capture: { seamless: false, notes: null },
      tags: ["fast", "small"],
      missing: null,
      empty: [],
      credits: [{ name: "Sam" }, { name: "Alex" }],
    }),
  ).toEqual([
    { key: "dimensions", term: "Dimensions", value: "9 × ? × 4" },
    { key: "opening_time", term: "Opening", value: "0 gt" },
    { key: "Capture.seamless", term: "Capture · Seamless", value: "No" },
    { key: "tags", term: "Tags", value: "fast and small" },
    { key: "credits", term: "Credits", value: "Sam and Alex" },
  ]);
});

it("uploads a schematic and media with the content type for each file", async () => {
  const requests: string[] = [];
  server.use(
    http.put(`${baseUrl}/v1/submissions/drafts/d1/schematics/:upload`, ({ request }) => {
      requests.push(request.headers.get("Content-Type") ?? "");
      return HttpResponse.json({});
    }),
    http.post(`${baseUrl}/v1/submissions/drafts/d1/media/:kind`, ({ request }) => {
      requests.push(request.headers.get("Content-Type") ?? "");
      return HttpResponse.json({});
    }),
  );
  render(DraftAttachments, {
    baseUrl,
    draftId: "d1",
    schematics: [
      { id: "old", filename: "old.schem", sha256: null, byte_size: 1, state: "ready" },
      {
        id: "removed",
        filename: "removed.schem",
        sha256: null,
        byte_size: 2,
        state: "deleted",
        discarded: true,
      },
    ],
    media: {
      media: [
        {
          id: "old-media",
          draft_id: "d1",
          kind: "image",
          status: "processing",
          source_content_type: "image/png",
          artifacts: [],
        },
      ],
      limits: { max_upload_bytes: 9999, max_images: 3, max_videos: 1, max_output_bytes: 9999 },
    },
  });
  expect(screen.getByText(/old.schem/)).toBeVisible();
  expect(screen.queryByText(/removed.schem/)).toBeNull();
  expect(screen.getByText(/image · processing/)).toBeVisible();
  const user = userEvent.setup();
  for (const [label, name, type] of [
    ["Schematic", "door.schem", "application/octet-stream"],
    ["Images and videos", "door.png", "image/png"],
    ["Images and videos", "door.mp4", "video/mp4"],
  ] as const) {
    await user.upload(screen.getByLabelText(label), new File(["data"], name, { type }));
    expect(await screen.findByText(name)).toBeVisible();
  }
  const input = screen.getByLabelText("Images and videos");
  await user.upload(input, new File(["x".repeat(10000)], "large.png", { type: "image/png" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("upload size limit");
  const retry = new File(["x"], "retry.png", { type: "image/png" });
  const keys: (string | null)[] = [];
  server.use(
    http.post(`${baseUrl}/v1/submissions/drafts/d1/media/:kind`, ({ request }) => {
      keys.push(request.headers.get("Idempotency-Key"));
      return keys.length === 1 ? HttpResponse.error() : HttpResponse.json({});
    }),
  );
  await user.upload(input, retry);
  expect(await screen.findByRole("alert")).toBeVisible();
  await user.upload(input, retry);
  expect(await screen.findByText("retry.png")).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
  expect(requests).toEqual(["application/octet-stream", "image/png", "video/mp4"]);
});
