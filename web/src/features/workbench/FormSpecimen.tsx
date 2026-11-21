import { translate, type Locale, type StaticMessageKey } from "../../lib/i18n";
import { CheckField, Field, FieldGroup, SelectField, TextareaField } from "../../ui/react/Field";

export function FormSpecimen({
  locale,
  state,
}: {
  locale: Locale;
  state: "normal" | "invalid" | "disabled";
}) {
  const t = (key: StaticMessageKey) => translate(locale, key);
  const disabled = state === "disabled";
  const invalid = state === "invalid";
  return (
    <div className="sq-grid">
      <SelectField
        label={t("trial.category")}
        required
        defaultValue={invalid ? "" : "door"}
        disabled={disabled}
        error={invalid ? t("trial.required") : undefined}
      >
        <option value="">{t("workbench.select-category")}</option>
        <option value="door">{t("trial.door")}</option>
        <option value="utility">{t("trial.utility")}</option>
      </SelectField>
      <Field
        type="number"
        label={t("trial.width")}
        min={1}
        step={1}
        defaultValue={invalid ? 0 : 5}
        disabled={disabled}
        error={invalid ? t("trial.positive") : undefined}
      />
      <TextareaField
        rows={3}
        label={t("build.description")}
        hint={t("workbench.notes-hint")}
        disabled={disabled}
      />
      <FieldGroup label={t("workbench.edition")} disabled={disabled}>
        <CheckField type="radio" label="Java" name="specimen-edition" defaultChecked />
        <CheckField type="radio" label="Bedrock" name="specimen-edition" />
      </FieldGroup>
      <CheckField label={t("workbench.include-dimensions")} defaultChecked disabled={disabled} />
    </div>
  );
}
