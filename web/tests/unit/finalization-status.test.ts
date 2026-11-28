import { afterEach, beforeEach, expect, it, vi } from "vitest";
import "@/features/submissions/finalization-status";

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  document.querySelector("finalization-status")?.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("backs off on unchanged status and pauses when the tab is hidden", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 304 }));
  vi.stubGlobal("fetch", fetch);
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  const element = document.createElement("finalization-status");
  element.dataset.url = "https://test.invalid/status";
  element.dataset.etag = '"first"';
  document.body.append(element);
  await vi.advanceTimersByTimeAsync(2000);
  expect(fetch).toHaveBeenCalledWith(
    element.dataset.url,
    expect.objectContaining({ headers: { "If-None-Match": '"first"' }, credentials: "include" }),
  );
  await vi.advanceTimersByTimeAsync(3999);
  expect(fetch).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(fetch).toHaveBeenCalledTimes(2);
  hidden.mockReturnValue(true);
  document.dispatchEvent(new Event("visibilitychange"));
  await vi.advanceTimersByTimeAsync(40000);
  expect(fetch).toHaveBeenCalledTimes(2);
  hidden.mockReturnValue(false);
  document.dispatchEvent(new Event("visibilitychange"));
  await vi.advanceTimersByTimeAsync(8000);
  expect(fetch).toHaveBeenCalledTimes(3);
  element.remove();
  await vi.advanceTimersByTimeAsync(40000);
  expect(fetch).toHaveBeenCalledTimes(3);
});

it("retries an offline poll and cancels an in-flight request on leaving", async () => {
  const fetch = vi
    .fn<(url: string, options: RequestInit) => Promise<Response>>()
    .mockRejectedValueOnce(new TypeError("offline"))
    .mockImplementation(
      (_url: string, options: RequestInit) =>
        new Promise<Response>((_resolve, reject) =>
          options.signal?.addEventListener("abort", () => reject(new Error("aborted"))),
        ),
    );
  vi.stubGlobal("fetch", fetch);
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  const element = document.createElement("finalization-status");
  element.dataset.url = "https://test.invalid/status";
  document.body.append(element);
  await vi.advanceTimersByTimeAsync(2000);
  expect(fetch.mock.calls[0]?.[1].headers).toEqual({});
  await vi.advanceTimersByTimeAsync(4000);
  document.dispatchEvent(new Event("visibilitychange"));
  await vi.advanceTimersByTimeAsync(4000);
  expect(fetch).toHaveBeenCalledTimes(2);
  element.remove();
  expect(fetch.mock.calls[1]?.[1].signal?.aborted).toBe(true);
  await vi.advanceTimersByTimeAsync(30000);
  expect(fetch).toHaveBeenCalledTimes(2);
});
