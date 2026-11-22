<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import CheckField from "@/ui/svelte/CheckField.svelte";
  import Field from "@/ui/svelte/Field.svelte";
  import FieldGroup from "@/ui/svelte/FieldGroup.svelte";
  import SelectField from "@/ui/svelte/SelectField.svelte";
  import TextareaField from "@/ui/svelte/TextareaField.svelte";
  import { layoutStyles } from "@/ui/variants";

  let { state }: { state: "normal" | "invalid" | "disabled" } = $props();
  const disabled = $derived(state === "disabled");
  const invalid = $derived(state === "invalid");
</script>

<div class={layoutStyles({ pattern: "grid" })}>
  <SelectField
    label={m.trialCategory()}
    required
    value={invalid ? "" : "door"}
    {disabled}
    error={invalid ? m.trialRequired() : undefined}
  >
    <option value="">{m.workbenchSelectCategory()}</option>
    <option value="door">{m.trialDoor()}</option>
    <option value="utility">{m.trialUtility()}</option>
  </SelectField>
  <Field
    type="number"
    label={m.trialWidth()}
    min={1}
    step={1}
    value={invalid ? 0 : 5}
    {disabled}
    error={invalid ? m.trialPositive() : undefined}
  />
  <TextareaField rows={3} label={m.buildDescription()} hint={m.workbenchNotesHint()} {disabled} />
  <FieldGroup label={m.workbenchEdition()} {disabled}>
    <CheckField type="radio" label="Java" name="specimen-edition" checked />
    <CheckField type="radio" label="Bedrock" name="specimen-edition" />
  </FieldGroup>
  <CheckField label={m.workbenchIncludeDimensions()} checked {disabled} />
</div>
