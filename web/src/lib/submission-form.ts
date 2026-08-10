import type {
  FormFieldResponse,
  FormManifestResponse,
  FormSectionResponse,
  StoredDraftResponse,
  SubmissionAttentionIssueResponse,
} from "../generated/types.gen";
import type { Locale } from "./i18n";

export const SUBMISSION_PROTOCOL = 1;
export const WEB_SUBMISSION_CAPABILITIES = ["repeatable_text"] as const;

const REQUIRED_CATEGORIES = new Set(["door", "extender", "utility", "entrance", "other"]);
const SUPPORTED_CONTROLS = new Set([
  "text",
  "number",
  "choice",
  "multi_choice",
  "duration",
  "boolean",
]);
const SUPPORTED_VALUE_KINDS = new Set([
  "string",
  "integer",
  "number",
  "boolean",
  "string_list",
  "game_ticks",
]);

export type DraftValue = string | number | boolean | string[];

export class SubmissionSchemaError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SubmissionSchemaError";
  }
}

export function validateSubmissionManifest(manifest: FormManifestResponse): FormManifestResponse {
  if (
    manifest.minimum_protocol > SUBMISSION_PROTOCOL ||
    manifest.maximum_protocol < SUBMISSION_PROTOCOL
  ) {
    throw new SubmissionSchemaError(
      `This form requires submission protocol ${manifest.minimum_protocol}–${manifest.maximum_protocol}; this browser supports ${SUBMISSION_PROTOCOL}.`,
    );
  }

  const categories = new Set(manifest.categories.map((category) => category.code));
  const missingCategories = [...REQUIRED_CATEGORIES].filter(
    (category) => !categories.has(category),
  );
  if (missingCategories.length > 0) {
    throw new SubmissionSchemaError(
      `This form is missing supported categories: ${missingCategories.join(", ")}.`,
    );
  }

  const fieldIds = new Set<string>();
  for (const category of manifest.categories) {
    for (const section of [...manifest.common_sections, ...category.sections]) {
      for (const field of section.fields) {
        if (!field.origins.includes("web")) continue;
        if (fieldIds.has(`${category.code}:${field.id}`)) {
          throw new SubmissionSchemaError(`The form repeats field ${field.id}.`);
        }
        fieldIds.add(`${category.code}:${field.id}`);
        if (
          !SUPPORTED_CONTROLS.has(field.control) ||
          !SUPPORTED_VALUE_KINDS.has(field.value_kind)
        ) {
          throw new SubmissionSchemaError(`The browser cannot safely render field ${field.id}.`);
        }
        if (
          field.required &&
          field.required_capability !== null &&
          !WEB_SUBMISSION_CAPABILITIES.includes(
            field.required_capability as (typeof WEB_SUBMISSION_CAPABILITIES)[number],
          )
        ) {
          throw new SubmissionSchemaError(
            `The browser is missing required capability ${field.required_capability}.`,
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
): FormSectionResponse[] {
  const category = manifest.categories.find((candidate) => candidate.code === draft.category);
  if (!category) {
    throw new SubmissionSchemaError(`Draft category ${draft.category} is not in the current form.`);
  }
  return [...manifest.common_sections, ...category.sections]
    .map((section) => ({
      ...section,
      fields: section.fields.filter((field) => field.origins.includes("web")),
    }))
    .filter((section) => section.fields.length > 0);
}

export function materializedAnswers(
  sections: FormSectionResponse[],
  answers: StoredDraftResponse["answers"],
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

export function draftValue(value: unknown): DraftValue | undefined {
  if (typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) return value;
  return undefined;
}

export function parseDuration(value: string): number | undefined {
  const match = /^\s*(\d+(?:\.\d+)?)\s*(gt|rt|s)\s*$/i.exec(value);
  if (!match?.[1] || !match[2]) return undefined;
  const amount = Number(match[1]);
  const multiplier = match[2].toLowerCase() === "s" ? 20 : match[2].toLowerCase() === "rt" ? 2 : 1;
  const ticks = amount * multiplier;
  return Number.isSafeInteger(ticks) ? ticks : undefined;
}

export function fieldInputValue(field: FormFieldResponse, value: unknown): string {
  const parsed = draftValue(value);
  if (Array.isArray(parsed)) return parsed.join("\n");
  if (typeof parsed === "number" && field.control === "duration") return `${parsed}gt`;
  if (typeof parsed === "string" || typeof parsed === "number") return String(parsed);
  return "";
}

export function parseFieldInput(
  field: FormFieldResponse,
  raw: string,
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
      ? { error: "Use an explicit unit: game ticks (gt), redstone ticks (rt), or seconds (s)." }
      : { value };
  }
  if (field.value_kind === "integer" || field.value_kind === "number") {
    const value = Number(raw);
    const valid =
      Number.isFinite(value) && (field.value_kind !== "integer" || Number.isSafeInteger(value));
    return valid ? { value } : { error: "Enter a valid number." };
  }
  return { value: raw };
}

export function issueMessage(
  issue: SubmissionAttentionIssueResponse,
  label?: string,
  locale: Locale = "en",
): string {
  if (locale === "zh-CN") {
    const subject = label ?? (issue.field_id === "submission" ? "投稿" : issue.field_id);
    const messages: Record<SubmissionAttentionIssueResponse["reason"], string> = {
      unknown_field: "已不再被识别；请移除此项或创建新草稿",
      required: "为必填项",
      wrong_type: "的值格式不正确",
      required_value: "必须确认",
      below_minimum: "低于最小值",
      above_maximum: "高于最大值",
      too_short: "内容过短",
      too_long: "内容过长",
      too_few_items: "需要更多条目",
      too_many_items: "条目过多",
      unknown_option: "包含已不可用的选项",
      schema_unsupported: "使用的表单版本无法由服务器完成投稿",
      schematic_required: "需要由游戏内投稿客户端提供原理图",
      schematic_processing: "正在等待原理图处理",
      schematic_rejected: "的原理图无法被接受",
      media_processing: "正在等待媒体处理",
      media_rejected: "的媒体无法被接受；请丢弃并上传替代文件",
      target_rejected: "无法创建目标作品记录",
      retry_exhausted: "多次重试后仍无法完成投稿",
    };
    return `${subject}${messages[issue.reason]}。`;
  }

  const subject = label ?? (issue.field_id === "submission" ? "Submission" : issue.field_id);
  const messages: Record<SubmissionAttentionIssueResponse["reason"], string> = {
    unknown_field: "is no longer recognized; remove it or start a new draft",
    required: "is required",
    wrong_type: "has the wrong value format",
    required_value: "must be accepted",
    below_minimum: "is below the minimum",
    above_maximum: "is above the maximum",
    too_short: "is too short",
    too_long: "is too long",
    too_few_items: "needs more entries",
    too_many_items: "has too many entries",
    unknown_option: "contains an option that is no longer available",
    schema_unsupported: "uses a form revision this server cannot finalize",
    schematic_required: "needs a schematic from an in-game submission client",
    schematic_processing: "is waiting for schematic processing",
    schematic_rejected: "has a schematic that could not be accepted",
    media_processing: "is waiting for media processing",
    media_rejected: "has media that could not be accepted; discard it and upload a replacement",
    target_rejected: "could not create the target build record",
    retry_exhausted: "could not be finalized after several retries",
  };
  return `${subject} ${messages[issue.reason]}.`;
}
