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
  // FieldFrame renders a hint or error only when it is non-empty.
  const descriptions = [
    describedBy,
    hint == null || hint === "" ? undefined : `${id}-hint`,
    error == null || error === "" ? undefined : `${id}-error`,
  ].filter(Boolean);
  return {
    id,
    "aria-describedby": descriptions.length > 0 ? descriptions.join(" ") : undefined,
    "aria-invalid": invalid ?? Boolean(error),
  };
}
