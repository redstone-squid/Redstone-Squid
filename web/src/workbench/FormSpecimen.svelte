<script lang="ts">
  import { translate, type Locale, type StaticMessageKey } from "../lib/i18n";
  import CheckField from "../ui/svelte/CheckField.svelte";
  import Field from "../ui/svelte/Field.svelte";
  import FieldGroup from "../ui/svelte/FieldGroup.svelte";
  import SelectField from "../ui/svelte/SelectField.svelte";
  import TextareaField from "../ui/svelte/TextareaField.svelte";

  let { locale, state }: { locale: Locale; state: "normal" | "invalid" | "disabled" } = $props();
  const t = (key: StaticMessageKey) => translate(locale, key);
  const disabled = $derived(state === "disabled");
  const invalid = $derived(state === "invalid");
</script>

<div class="sq-grid">
  <SelectField
    label={t("trial.category")}
    required
    value={invalid ? "" : "door"}
    {disabled}
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
    value={invalid ? 0 : 5}
    {disabled}
    error={invalid ? t("trial.positive") : undefined}
  />
  <TextareaField
    rows={3}
    label={t("build.description")}
    hint={t("workbench.notes-hint")}
    {disabled}
  />
  <FieldGroup label={t("workbench.edition")} {disabled}>
    <CheckField type="radio" label="Java" name="specimen-edition" checked />
    <CheckField type="radio" label="Bedrock" name="specimen-edition" />
  </FieldGroup>
  <CheckField label={t("workbench.include-dimensions")} checked {disabled} />
</div>
