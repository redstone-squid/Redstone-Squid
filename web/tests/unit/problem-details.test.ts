import { describe, expect, it } from "vitest";

import { decodeProblemDetail } from "../../src/lib/problem-details";

describe("HTTP problem detail decoding", () => {
  it("preserves valid fields and application codes introduced by a newer API", () => {
    const problem = {
      title: "Request refused",
      status: 409,
      type: "about:blank",
      detail: null,
      code: "A_FUTURE_CODE",
      instance: "/v1/submissions",
      resource: null,
      context: { expected: 3, actual: 4 },
    };
    expect(decodeProblemDetail(problem)).toEqual(problem);
    expect(decodeProblemDetail({ title: "Failure", status: 503 })).toEqual({
      title: "Failure",
      status: 503,
    });
  });

  it.each([
    { title: 123 },
    { status: "400" },
    { status: NaN },
    { status: 400.5 },
    { status: 600 },
    { type: null },
    { detail: 123 },
    { instance: {} },
    { code: [] },
    { resource: false },
    { context: "not an object" },
    { context: [] },
  ])("rejects malformed fields: %j", (fields) => {
    expect(decodeProblemDetail({ title: "Failure", status: 400, ...fields })).toBeUndefined();
  });

  it.each([null, [], "Failure", 400])("rejects non-object envelopes: %j", (value) => {
    expect(decodeProblemDetail(value)).toBeUndefined();
  });
});
