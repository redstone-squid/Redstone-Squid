import { useId, type ComponentProps, type ReactNode } from "react";
import { cx } from "class-variance-authority";
import { fieldAttributes, type FieldMessages } from "../field";

type FrameProps = FieldMessages &
  Pick<ComponentProps<"input">, "id" | "aria-describedby" | "aria-invalid">;
type FieldControl = ReturnType<typeof fieldAttributes>;
type ControlProps<Tag extends "input" | "select" | "textarea"> = FieldMessages &
  ComponentProps<Tag>;

function Messages({ id, hint, error }: Pick<FieldMessages, "hint" | "error"> & { id: string }) {
  return (
    <>
      {hint && (
        <p id={`${id}-hint`} className="sq-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} role="alert" className="sq-error">
          {error}
        </p>
      )}
    </>
  );
}

function FieldFrame({
  inline,
  children,
  ...props
}: FrameProps & {
  inline?: boolean;
  children: (attributes: FieldControl) => ReactNode;
}) {
  const autoId = useId();
  const control = fieldAttributes(props.id ?? autoId, props);
  const caption = (
    <>
      {props.label}
      {props.detail && (
        <>
          {" "}
          <small>({props.detail})</small>
        </>
      )}
    </>
  );
  return (
    <div className="sq-field">
      {inline ? (
        <label className="sq-choice" htmlFor={control.id}>
          {children(control)}
          <span>{caption}</span>
        </label>
      ) : (
        <>
          <label htmlFor={control.id}>{caption}</label>
          {children(control)}
        </>
      )}
      <Messages id={control.id} hint={props.hint} error={props.error} />
    </div>
  );
}

export function Field({ label, detail, hint, error, className, ...input }: ControlProps<"input">) {
  return (
    <FieldFrame label={label} detail={detail} hint={hint} error={error} {...input}>
      {(attributes) => <input {...input} {...attributes} className={cx("sq-input", className)} />}
    </FieldFrame>
  );
}

export function SelectField({
  label,
  detail,
  hint,
  error,
  className,
  children,
  ...select
}: ControlProps<"select">) {
  return (
    <FieldFrame label={label} detail={detail} hint={hint} error={error} {...select}>
      {(attributes) => (
        <select {...select} {...attributes} className={cx("sq-input", className)}>
          {children}
        </select>
      )}
    </FieldFrame>
  );
}

export function TextareaField({
  label,
  detail,
  hint,
  error,
  className,
  ...textarea
}: ControlProps<"textarea">) {
  return (
    <FieldFrame label={label} detail={detail} hint={hint} error={error} {...textarea}>
      {(attributes) => (
        <textarea {...textarea} {...attributes} className={cx("sq-input", className)} />
      )}
    </FieldFrame>
  );
}

export function CheckField({
  type = "checkbox",
  label,
  detail,
  hint,
  error,
  ...input
}: ControlProps<"input"> & { type?: "checkbox" | "radio" }) {
  return (
    <FieldFrame label={label} detail={detail} hint={hint} error={error} {...input} inline>
      {(attributes) => <input {...input} {...attributes} type={type} />}
    </FieldFrame>
  );
}

export function FieldGroup({
  label,
  detail,
  hint,
  error,
  children,
  className,
  ...group
}: FieldMessages & ComponentProps<"fieldset">) {
  const autoId = useId();
  const attributes = fieldAttributes(group.id ?? autoId, { ...group, hint, error });
  return (
    <fieldset {...group} {...attributes} className={cx("sq-field-group", className)}>
      <legend>
        {label}
        {detail && (
          <>
            {" "}
            <small>({detail})</small>
          </>
        )}
      </legend>
      {children}
      <Messages id={attributes.id} hint={hint} error={error} />
    </fieldset>
  );
}
