import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type {
  FormFieldResponse,
  FormManifestResponse,
  StoredDraftResponse,
  SubmissionAttentionReason,
} from "../../src/generated/types.gen";
import {
  draftValue,
  fieldInputValue,
  isFieldVisible,
  issueMessage,
  materializedAnswers,
  parseDuration,
  parseFieldInput,
  sectionsForDraft,
  SubmissionSchemaError,
  validateSubmissionManifest,
} from "../../src/lib/submission-form";

const durationCases = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "../../../contracts/fixtures/duration-cases.json"),
    "utf8",
  ),
) as {
  core: { input: string; ticks: number }[];
  client_rejects: { input: string }[];
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

function field(overrides: Partial<FormFieldResponse> = {}): FormFieldResponse {
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

function manifest(overrides: Partial<FormManifestResponse> = {}): FormManifestResponse {
  return {
    schema_id: "build_submission.v1",
    revision: 1,
    minimum_protocol: 1,
    maximum_protocol: 1,
    common_sections: [{ id: "identity", title: "Identity", fields: [field()] }],
    categories: ["door", "extender", "utility", "entrance", "other"].map((code) => ({
      code,
      label: code,
      sections: [],
    })),
    ...overrides,
  };
}

function draft(overrides: Partial<StoredDraftResponse> = {}): StoredDraftResponse {
  return {
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
    ...overrides,
  };
}

describe("submission form compatibility", () => {
  it("accepts protocol one with all five categories and browser controls", () => {
    expect(validateSubmissionManifest(manifest()).schema_id).toBe("build_submission.v1");
  });

  it("rejects both newer and retired protocol ranges", () => {
    expect(() =>
      validateSubmissionManifest(manifest({ minimum_protocol: 2, maximum_protocol: 3 })),
    ).toThrow(/supports 1/);
    expect(() =>
      validateSubmissionManifest(manifest({ minimum_protocol: 1, maximum_protocol: 0 })),
    ).toThrow(SubmissionSchemaError);
  });

  it("rejects missing categories, repeated fields, unsupported controls, and required capabilities", () => {
    expect(() =>
      validateSubmissionManifest(manifest({ categories: manifest().categories.slice(0, 4) })),
    ).toThrow(/other/);
    expect(() =>
      validateSubmissionManifest(
        manifest({
          common_sections: [
            { id: "one", title: "One", fields: [field()] },
            { id: "two", title: "Two", fields: [field()] },
          ],
        }),
      ),
    ).toThrow(/repeats field/);
    expect(() =>
      validateSubmissionManifest(
        manifest({
          common_sections: [
            { id: "one", title: "One", fields: [field({ control: "slider" as never })] },
          ],
        }),
      ),
    ).toThrow(/cannot safely render/);
    expect(() =>
      validateSubmissionManifest(
        manifest({
          common_sections: [
            {
              id: "one",
              title: "One",
              fields: [field({ required: true, required_capability: "camera_access" })],
            },
          ],
        }),
      ),
    ).toThrow(/camera_access/);
  });

  it("ignores non-web-only fields while checking and rendering a category", () => {
    const paper = field({ id: "sponsor", origins: ["paper"], control: "slider" as never });
    const form = validateSubmissionManifest(
      manifest({
        common_sections: [{ id: "identity", title: "Identity", fields: [field(), paper] }],
      }),
    );
    expect(sectionsForDraft(form, draft())[0]?.fields.map((item) => item.id)).toEqual([
      "display_name",
    ]);
    expect(() => sectionsForDraft(form, draft({ category: "future" }))).toThrow(/future/);
  });
});

describe("renderer values", () => {
  const visibilityField = field({
    id: "license",
    visible_when: { field_id: "visibility", operator: "equals", value: "public" },
  });

  it("applies defaults and evaluates each visibility operator", () => {
    const sections = [
      {
        id: "rights",
        title: "Rights",
        fields: [
          field({ id: "enabled", value_kind: "boolean", control: "boolean", default: true }),
        ],
      },
    ];
    expect(materializedAnswers(sections, {})).toEqual({ enabled: true });
    expect(materializedAnswers(sections, { enabled: false })).toEqual({ enabled: false });
    expect(isFieldVisible(visibilityField, { visibility: "public" })).toBe(true);
    expect(isFieldVisible(visibilityField, { visibility: "private" })).toBe(false);
    expect(
      isFieldVisible(
        {
          ...visibilityField,
          visible_when: { field_id: "visibility", operator: "not_equals", value: "private" },
        },
        { visibility: "public" },
      ),
    ).toBe(true);
    expect(
      isFieldVisible(
        {
          ...visibilityField,
          visible_when: { field_id: "visibility", operator: "in", value: ["public", "staff"] },
        },
        { visibility: "staff" },
      ),
    ).toBe(true);
    expect(isFieldVisible(field(), {})).toBe(true);
  });

  it("recognizes only supported compact draft values", () => {
    expect(draftValue("name")).toBe("name");
    expect(draftValue(true)).toBe(true);
    expect(draftValue(4)).toBe(4);
    expect(draftValue(["one", "two"])).toEqual(["one", "two"]);
    expect(draftValue(Number.NaN)).toBeUndefined();
    expect(draftValue(["one", 2])).toBeUndefined();
    expect(draftValue({})).toBeUndefined();
  });

  it("parses every core case of the shared duration fixture", () => {
    for (const { input, ticks } of durationCases.core) {
      expect(parseDuration(input), input).toBe(ticks);
    }
  });

  it("rejects every client_rejects case of the shared duration fixture", () => {
    for (const { input } of durationCases.client_rejects) {
      expect(parseDuration(input), input).toBeUndefined();
    }
  });

  it("parses text, lists, numeric values, duration values, and unset inputs", () => {
    expect(parseFieldInput(field(), " Door ")).toEqual({ value: " Door " });
    expect(
      parseFieldInput(field({ value_kind: "string_list", repeatable: true }), "Alice\n\n Bob "),
    ).toEqual({
      value: ["Alice", "Bob"],
    });
    expect(parseFieldInput(field({ value_kind: "string_list", repeatable: true }), "  ")).toEqual(
      {},
    );
    expect(parseFieldInput(field({ control: "number", value_kind: "integer" }), "4")).toEqual({
      value: 4,
    });
    expect(parseFieldInput(field({ control: "number", value_kind: "integer" }), "4.5")).toEqual({
      error: "Enter a valid number.",
    });
    expect(
      parseFieldInput(field({ control: "duration", value_kind: "game_ticks" }), "2rt"),
    ).toEqual({ value: 4 });
    expect(
      parseFieldInput(field({ control: "duration", value_kind: "game_ticks" }), "fast").error,
    ).toMatch(/explicit unit/);
    expect(parseFieldInput(field(), "")).toEqual({});
  });

  it("formats lists, durations, scalar values, and unknown values for controls", () => {
    expect(fieldInputValue(field({ value_kind: "string_list" }), ["Alice", "Bob"])).toBe(
      "Alice\nBob",
    );
    expect(fieldInputValue(field({ control: "duration" }), 20)).toBe("20gt");
    expect(fieldInputValue(field(), "Door")).toBe("Door");
    expect(fieldInputValue(field(), 4)).toBe("4");
    expect(fieldInputValue(field(), false)).toBe("");
  });
});

describe("stable finalization issue copy", () => {
  it("maps every stable reason without claiming schematic sanitization", () => {
    const reasons: SubmissionAttentionReason[] = [
      "unknown_field",
      "required",
      "wrong_type",
      "required_value",
      "below_minimum",
      "above_maximum",
      "too_short",
      "too_long",
      "too_few_items",
      "too_many_items",
      "unknown_option",
      "schema_unsupported",
      "schematic_required",
      "schematic_processing",
      "schematic_rejected",
      "media_processing",
      "media_rejected",
      "target_rejected",
      "retry_exhausted",
    ];
    for (const reason of reasons) {
      expect(issueMessage({ field_id: "submission", reason })).toMatch(/^Submission .+\.$/);
    }
    expect(issueMessage({ field_id: "display_name", reason: "required" }, "Display name")).toBe(
      "Display name is required.",
    );
    expect(
      issueMessage({ field_id: "display_name", reason: "required" }, "显示名称", "zh-CN"),
    ).toBe("显示名称为必填项。");
    expect(issueMessage({ field_id: "schematic", reason: "schematic_required" })).not.toMatch(
      /sanitiz/i,
    );
  });
});
