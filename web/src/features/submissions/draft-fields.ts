import { isEqual } from "es-toolkit";
import { ApiError } from "@/api-client/api-error";
import type {
  FieldOperationRequest,
  FormFieldResponse,
  StoredDraftResponse,
} from "@/generated/types.gen";

type DraftField = Pick<FormFieldResponse, "id" | "control" | "value_kind">;

function text(value: FormDataEntryValue | null): string {
  if (value === null) return "";
  if (typeof value !== "string") throw new ApiError(422);
  return value;
}

/** Convert between a manifest field's stored value and its HTML form controls. */
export class DraftFields {
  constructor(private readonly answers: StoredDraftResponse["answers"]) {}
  display(field: DraftField): string {
    const value = this.answers[field.id];
    if (value == null) return "";
    if (field.value_kind === "string_list" && Array.isArray(value)) return value.join("\n");
    return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  }
  objects(field: DraftField): Record<string, unknown>[] {
    const value = this.answers[field.id];
    return Array.isArray(value)
      ? value.filter(
          (item): item is Record<string, unknown> =>
            typeof item === "object" && item !== null && !Array.isArray(item),
        )
      : [];
  }
  operations(form: FormData, fields: DraftField[]): FieldOperationRequest[] {
    const operations: FieldOperationRequest[] = [];
    for (const field of fields) {
      const raw = form.get(field.id);
      let value: FieldOperationRequest["value"];
      if (field.control === "credits") {
        const existing = this.objects(field);
        const roles = form.getAll(`${field.id}.role`);
        value = form.getAll(`${field.id}.name`).flatMap((input, index) => {
          if (typeof input !== "string" || input.trim() === "") return [];
          const name = input.trim();
          const previous = existing[index];
          return [
            {
              ...(previous?.name === name ? previous : {}),
              name,
              role: text(roles[index] ?? "creator"),
            },
          ];
        });
      } else if (field.control === "showcase_tags") {
        value = form.getAll(field.id).map((input) => {
          const key = text(input);
          const entered = text(form.get(`${field.id}.${key}`)).trim();
          return entered !== "" ? { key, value: entered } : { key };
        });
      } else if (field.control === "multi_choice") {
        value = form.getAll(field.id).map(text);
      } else {
        if (typeof raw !== "string" || raw === this.display(field)) continue;
        value = raw === "" ? null : raw;
        if (raw !== "" && field.value_kind === "string_list")
          value = raw
            .split(/\r?\n/)
            .map((s) => s.trim())
            .filter(Boolean);
        else if (raw !== "" && field.value_kind === "boolean") value = raw === "true";
        else if (raw !== "" && ["integer", "game_ticks", "number"].includes(field.value_kind)) {
          value = Number(raw);
          if (!Number.isFinite(value)) throw new ApiError(422);
        }
      }
      if (Array.isArray(value) && value.length === 0) value = null;
      if (isEqual(value, this.answers[field.id] ?? null)) continue;
      operations.push({
        operation_id: crypto.randomUUID(),
        field_id: field.id,
        kind: value == null ? "unset" : "set",
        value,
      });
    }
    return operations;
  }
}
