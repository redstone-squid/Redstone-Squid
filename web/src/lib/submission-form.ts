import type {
  FormFieldResponse,
  FormManifestResponse,
  FormSectionResponse,
  StoredDraftResponse,
  SubmissionAttentionIssueResponse,
} from "../generated/types.gen";
import { translate, type Locale } from "./i18n";

export const SUBMISSION_PROTOCOL = 1;
export const WEB_SUBMISSION_CAPABILITIES = [
  "repeatable_text",
  "typed_credits",
  "showcase_tag_values",
] as const;

const REQUIRED_CATEGORIES = new Set(["door", "extender", "utility", "entrance", "other"]);
const SUPPORTED_CONTROLS = new Set([
  "text",
  "number",
  "choice",
  "multi_choice",
  "duration",
  "boolean",
  "credits",
  "showcase_tags",
]);
const SUPPORTED_VALUE_KINDS = new Set([
  "string",
  "integer",
  "number",
  "boolean",
  "string_list",
  "game_ticks",
  "credit_list",
  "showcase_tag_list",
]);

export type CreditAnswer = {
  name: string;
  kind: string;
  role: string;
  username?: string | null;
  subject?: string | null;
  attribution?: string;
  source_message_id?: number | null;
};
/** A chosen showcase tag. `value` is the raw text as entered, omitted for tags that take no value. */
export type ShowcaseTagAnswer = { key: string; value?: string };
export type DraftValue =
  string | number | boolean | string[] | CreditAnswer[] | ShowcaseTagAnswer[];

export class SubmissionSchemaError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SubmissionSchemaError";
  }
}

/**
 * Return the same manifest if this browser can render its web fields, or throw SubmissionSchemaError.
 *
 * Checks protocol compatibility, required categories, duplicate fields per category, controls,
 * value kinds, and required capabilities. Server-side answer validation remains authoritative.
 */
export function validateSubmissionManifest(
  manifest: FormManifestResponse,
  locale: Locale = "en",
): FormManifestResponse {
  if (
    manifest.minimum_protocol > SUBMISSION_PROTOCOL ||
    manifest.maximum_protocol < SUBMISSION_PROTOCOL
  ) {
    throw new SubmissionSchemaError(
      translate(locale, "submitFlow.schemaProtocol", {
        min: manifest.minimum_protocol,
        max: manifest.maximum_protocol,
        supported: SUBMISSION_PROTOCOL,
      }),
    );
  }

  const categories = new Set(manifest.categories.map((category) => category.code));
  const missingCategories = [...REQUIRED_CATEGORIES].filter(
    (category) => !categories.has(category),
  );
  if (missingCategories.length > 0) {
    throw new SubmissionSchemaError(
      translate(locale, "submitFlow.schemaCategories", {
        categories: new Intl.ListFormat(locale).format(missingCategories),
      }),
    );
  }

  const fieldIds = new Set<string>();
  for (const category of manifest.categories) {
    for (const section of [...manifest.common_sections, ...category.sections]) {
      for (const field of section.fields) {
        if (!field.origins.includes("web")) continue;
        if (fieldIds.has(`${category.code}:${field.id}`)) {
          throw new SubmissionSchemaError(
            translate(locale, "submitFlow.schemaDuplicate", { field: field.id }),
          );
        }
        fieldIds.add(`${category.code}:${field.id}`);
        if (
          !SUPPORTED_CONTROLS.has(field.control) ||
          !SUPPORTED_VALUE_KINDS.has(field.value_kind)
        ) {
          throw new SubmissionSchemaError(
            translate(locale, "submitFlow.schemaField", { field: field.id }),
          );
        }
        if (
          field.required &&
          field.required_capability !== null &&
          !WEB_SUBMISSION_CAPABILITIES.includes(
            field.required_capability as (typeof WEB_SUBMISSION_CAPABILITIES)[number],
          )
        ) {
          throw new SubmissionSchemaError(
            translate(locale, "submitFlow.schemaCapability", {
              capability: field.required_capability,
            }),
          );
        }
      }
    }
  }
  return manifest;
}

export function sectionsForDraft(
  manifest: FormManifestResponse,
  draft: StoredDraftResponse,
  locale: Locale = "en",
): FormSectionResponse[] {
  const category = manifest.categories.find((candidate) => candidate.code === draft.category);
  if (!category) {
    throw new SubmissionSchemaError(
      translate(locale, "submitFlow.schemaCategory", { category: draft.category }),
    );
  }
  return [...manifest.common_sections, ...category.sections]
    .map((section) => ({
      ...section,
      fields: section.fields.filter((field) => field.origins.includes("web")),
    }))
    .filter((section) => section.fields.length > 0);
}

/** Copy answers and fill absent keys from form defaults without persisting those defaults. */
export function materializedAnswers(
  sections: FormSectionResponse[],
  answers: Readonly<Record<string, unknown>>,
): Record<string, unknown> {
  const materialized = { ...answers };
  for (const section of sections) {
    for (const field of section.fields) {
      if (!(field.id in materialized) && field.default !== null) {
        materialized[field.id] = field.default;
      }
    }
  }
  return materialized;
}

export function isFieldVisible(
  field: FormFieldResponse,
  answers: Record<string, unknown>,
): boolean {
  const rule = field.visible_when;
  if (rule === null) return true;
  const actual = answers[rule.field_id];
  if (rule.operator === "equals") return Object.is(actual, rule.value);
  if (rule.operator === "not_equals") return !Object.is(actual, rule.value);
  return Array.isArray(rule.value) && rule.value.some((value) => Object.is(value, actual));
}

export function isCreditAnswer(value: unknown): value is CreditAnswer {
  return (
    value !== null &&
    typeof value === "object" &&
    "name" in value &&
    typeof value.name === "string" &&
    "kind" in value &&
    typeof value.kind === "string" &&
    "role" in value &&
    typeof value.role === "string"
  );
}

export function isShowcaseTagAnswer(value: unknown): value is ShowcaseTagAnswer {
  return (
    value !== null &&
    typeof value === "object" &&
    "key" in value &&
    typeof value.key === "string" &&
    (!("value" in value) || typeof value.value === "string")
  );
}

export function draftValue(value: unknown): DraftValue | undefined {
  if (typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) return value;
  if (Array.isArray(value)) {
    const entries: unknown[] = value;
    if (entries.every(isCreditAnswer)) return entries;
    if (entries.every(isShowcaseTagAnswer)) return entries;
  }
  return undefined;
}

// The unit vocabulary is shared with the other surfaces; contracts/fixtures/duration-cases.json
// is the authority, and tests/unit/submission-form.test.ts asserts every core case against it.
/** Return nonnegative whole game ticks for explicit gt/t, rt, or s units; reject fractional ticks. */
export function parseDuration(value: string): number | undefined {
  const match = /^\s*(\d+(?:\.\d+)?|\.\d+)\s*(gt|rt|t|s)\s*$/i.exec(value);
  if (!match?.[1] || !match[2]) return undefined;
  const amount = Number(match[1]);
  const unit = match[2].toLowerCase();
  const multiplier = unit === "s" ? 20 : unit === "rt" ? 2 : 1;
  const ticks = amount * multiplier;
  return Number.isSafeInteger(ticks) ? ticks : undefined;
}

export function fieldInputValue(field: FormFieldResponse, value: unknown): string {
  const parsed = draftValue(value);
  if (Array.isArray(parsed))
    return parsed
      .map((item) => (typeof item === "string" ? item : "name" in item ? item.name : item.key))
      .join("\n");
  if (typeof parsed === "number" && field.control === "duration") return `${parsed}gt`;
  if (typeof parsed === "string" || typeof parsed === "number") return String(parsed);
  return "";
}

/**
 * Convert control text to a draft value, an error, or an empty object meaning unset.
 *
 * Lists trim and omit blank lines; scalar text preserves whitespace. Form constraints are
 * checked separately by the server.
 */
export function parseFieldInput(
  field: FormFieldResponse,
  raw: string,
  locale: Locale = "en",
): { value?: DraftValue; error?: string } {
  if (field.value_kind === "string_list") {
    const value = raw
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
    return value.length > 0 ? { value } : {};
  }
  if (raw.trim() === "") return {};
  if (field.control === "duration") {
    const value = parseDuration(raw);
    return value === undefined
      ? { error: translate(locale, "validation.duration-unit") }
      : { value };
  }
  if (field.value_kind === "integer" || field.value_kind === "number") {
    const value = Number(raw);
    const valid =
      Number.isFinite(value) && (field.value_kind !== "integer" || Number.isSafeInteger(value));
    return valid ? { value } : { error: translate(locale, "validation.number") };
  }
  return { value: raw };
}

export function issueMessage(
  issue: SubmissionAttentionIssueResponse,
  label?: string,
  locale: Locale = "en",
): string {
  const subject =
    label ??
    (issue.field_id === "submission" ? translate(locale, "issue.submission") : issue.field_id);
  return translate(locale, `issue.${issue.reason}`, { subject });
}
