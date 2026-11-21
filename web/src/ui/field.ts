export type FieldMessages = {
  label: string;
  detail?: string;
  hint?: string | null;
  error?: string | null;
};

type FieldAccessibility = Pick<FieldMessages, "hint" | "error"> & {
  "aria-describedby"?: string | null;
  "aria-invalid"?: boolean | "true" | "false" | "grammar" | "spelling" | null;
};

export function fieldAttributes(
  id: string,
  { hint, error, "aria-describedby": describedBy, "aria-invalid": invalid }: FieldAccessibility,
) {
  const descriptions = [describedBy, hint && `${id}-hint`, error && `${id}-error`];
  return {
    id,
    "aria-describedby": descriptions.filter(Boolean).join(" ") || undefined,
    "aria-invalid": invalid ?? Boolean(error),
  };
}
