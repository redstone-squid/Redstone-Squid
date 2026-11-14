import { useId, type ComponentProps } from "react";

type Props = ComponentProps<"input"> & { label: string; hint?: string; error?: string };
export function Field({ label, hint, error, id: suppliedId, ...props }: Props) {
  const generatedId = useId();
  const id = suppliedId ?? generatedId;
  const description = error ?? hint;
  return (
    <div className="sq-field">
      <label htmlFor={id}>{label}</label>
      <input
        {...props}
        id={id}
        className="sq-input"
        aria-invalid={Boolean(error)}
        aria-describedby={description ? `${id}-description` : undefined}
      />
      {description && (
        <p id={`${id}-description`} className={error ? "sq-error" : "sq-muted"}>
          {description}
        </p>
      )}
    </div>
  );
}
