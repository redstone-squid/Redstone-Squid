<script lang="ts">
  import type { Locale } from "../locales/locale";
  import * as m from "../locales/messages";
  import CheckField from "../ui/svelte/CheckField.svelte";
  import Field from "../ui/svelte/Field.svelte";
  import FieldGroup from "../ui/svelte/FieldGroup.svelte";
  import SelectField from "../ui/svelte/SelectField.svelte";
  import TextareaField from "../ui/svelte/TextareaField.svelte";

  let { locale, state }: { locale: Locale; state: "normal" | "invalid" | "disabled" } = $props();
  const disabled = $derived(state === "disabled");
  const invalid = $derived(state === "invalid");
</script>

<div class="sq-grid">
  <SelectField
    label={m.trialCategory(locale)}
    required
    value={invalid ? "" : "door"}
    {disabled}
    error={invalid ? m.trialRequired(locale) : undefined}
  >
    <option value="">{m.workbenchSelectCategory(locale)}</option>
    <option value="door">{m.trialDoor(locale)}</option>
    <option value="utility">{m.trialUtility(locale)}</option>
  </SelectField>
  <Field
    type="number"
    label={m.trialWidth(locale)}
    min={1}
    step={1}
    value={invalid ? 0 : 5}
    {disabled}
    error={invalid ? m.trialPositive(locale) : undefined}
  />
  <TextareaField
    rows={3}
    label={m.buildDescription(locale)}
    hint={m.workbenchNotesHint(locale)}
    {disabled}
  />
  <FieldGroup label={m.workbenchEdition(locale)} {disabled}>
    <CheckField type="radio" label="Java" name="specimen-edition" checked />
    <CheckField type="radio" label="Bedrock" name="specimen-edition" />
  </FieldGroup>
  <CheckField label={m.workbenchIncludeDimensions(locale)} checked {disabled} />
</div>
