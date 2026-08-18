import { beforeEach, describe, expect, it, vi } from "vitest";

import { CatalogueApiError } from "../../src/lib/api";
import { respondWithSuggestions } from "../../src/lib/suggest-route";

const fetchSuggestionPage = vi.hoisted(() => vi.fn());

vi.mock("../../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/lib/api")>()),
  fetchSuggestionPage,
}));

const page = { items: [], next_cursor: null };

function request(query = ""): URL {
  return new URL(`https://catalogue.test/api/suggest/creators${query}`);
}

describe("respondWithSuggestions", () => {
  beforeEach(() => {
    fetchSuggestionPage.mockReset();
    fetchSuggestionPage.mockResolvedValue(page);
  });

  it("answers a valid request without reaching for a cursor it was not given", async () => {
    const response = await respondWithSuggestions("en", "creators", request("?q=no"));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(page);
    expect(response.headers.get("Cache-Control")).toBe("private, max-age=30");
    // A suggestion payload is echoed user input; sniffing it as anything else is a hazard.
    expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
    expect(fetchSuggestionPage).toHaveBeenCalledWith("en", {
      source: "creators",
      q: "no",
      cursor: undefined,
    });
  });

  it("treats a missing query as the empty prefix", async () => {
    await respondWithSuggestions("en", "creators", request());

    expect(fetchSuggestionPage).toHaveBeenCalledWith("en", {
      source: "creators",
      q: "",
      cursor: undefined,
    });
  });

  it("passes a well-formed cursor through", async () => {
    await respondWithSuggestions("en", "creators", request("?cursor=40"));

    expect(fetchSuggestionPage).toHaveBeenCalledWith("en", {
      source: "creators",
      q: "",
      cursor: 40,
    });
  });

  it.each([undefined, "", "Creators", "1creators", "creators-x", "a".repeat(65)])(
    "refuses %o as a source name without calling the API",
    async (source) => {
      const response = await respondWithSuggestions("en", source, request());

      expect(response.status).toBe(404);
      expect(fetchSuggestionPage).not.toHaveBeenCalled();
    },
  );

  it("refuses an over-long query", async () => {
    const response = await respondWithSuggestions(
      "en",
      "creators",
      request(`?q=${"a".repeat(201)}`),
    );

    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ title: "Query too long" });
    expect(fetchSuggestionPage).not.toHaveBeenCalled();
  });

  it.each(["-1", "1.5", "abc", "9007199254740993"])("refuses %s as a cursor", async (cursor) => {
    const response = await respondWithSuggestions("en", "creators", request(`?cursor=${cursor}`));

    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ title: "Invalid cursor" });
    expect(fetchSuggestionPage).not.toHaveBeenCalled();
  });

  it("passes an upstream problem title through with its status", async () => {
    fetchSuggestionPage.mockRejectedValue(
      new CatalogueApiError(429, { title: "Too many requests", status: 429 }),
    );

    const response = await respondWithSuggestions("en", "creators", request());

    expect(response.status).toBe(429);
    expect(await response.json()).toEqual({ title: "Too many requests", status: 429 });
    expect(response.headers.get("Cache-Control")).toBe("no-store");
  });

  it.each([
    ["en", "Suggestions unavailable"],
    ["zh-CN", "无法获取搜索建议"],
  ] as const)("falls back to the %s title when the failure carries none", async (locale, title) => {
    fetchSuggestionPage.mockRejectedValue(new Error("socket hang up"));

    const response = await respondWithSuggestions(locale, "creators", request());

    // A non-API failure becomes a 503 rather than leaking the cause to the browser.
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ title, status: 503 });
  });
});
