import { useId, type ComponentProps } from "react";
import { cx } from "class-variance-authority";

type Props = ComponentProps<"input"> & { label: string; hint?: string; error?: string };
export function Field({
  label,
  hint,
  error,
  id: suppliedId,
  className,
  "aria-describedby": describedBy,
  "aria-invalid": invalid,
  ...props
}: Props) {
  const generatedId = useId();
  const id = suppliedId ?? generatedId;
  const description = error ?? hint;
  const descriptionIds =
    [describedBy, description ? `${id}-description` : undefined].filter(Boolean).join(" ") ||
    undefined;
  return (
    <div className="sq-field">
      <label htmlFor={id}>{label}</label>
      <input
        {...props}
        id={id}
        className={cx("sq-input", className)}
        aria-invalid={invalid ?? Boolean(error)}
        aria-describedby={descriptionIds}
      />
      {description && (
        <p id={`${id}-description`} className={error ? "sq-error" : "sq-muted"}>
          {description}
        </p>
      )}
    </div>
  );
}
