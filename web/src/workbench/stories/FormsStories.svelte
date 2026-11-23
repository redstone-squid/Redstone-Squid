<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import Autocomplete from "@/ui/forms/Autocomplete.svelte";
  import CheckField from "@/ui/forms/CheckField.svelte";
  import Field from "@/ui/forms/Field.svelte";
  import FieldGroup from "@/ui/forms/FieldGroup.svelte";
  import FilePicker from "@/ui/forms/FilePicker.svelte";
  import SelectField from "@/ui/forms/SelectField.svelte";
  import TextareaField from "@/ui/forms/TextareaField.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import { versionOptions } from "@/workbench/draft-fixture";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  let files = $state<string[]>([]);
</script>

<Story name="Field" source="forms/Field">
  <Layout pattern="grid" min="card">
    <Variant label="hint">
      <Field label="Creator" hint="Public name shown on the build." placeholder="CircuitSage" />
    </Variant>
    <Variant label="error">
      <Field label="Build title" detail="required" error="Enter a title for your build." />
    </Variant>
    <Variant label="disabled">
      <Field label="Opening width" value="5" disabled />
    </Variant>
  </Layout>
</Story>

<Story name="SelectField" source="forms/SelectField">
  <SelectField label="Category" hint="Choose the closest category.">
    <option value="door">Piston door</option>
    <option value="utility">Utility</option>
  </SelectField>
</Story>

<Story name="TextareaField" source="forms/TextareaField">
  <TextareaField label="Builder notes" hint="Describe the opening sequence or timing." />
</Story>

<Story name="CheckField" source="forms/CheckField">
  <CheckField label="Include build dimensions" checked />
  <CheckField label="Hide my profile" hint="Only your builds stay public." />
</Story>

<Story name="FieldGroup" source="forms/FieldGroup" description="Related choices in a fieldset.">
  <FieldGroup label="Minecraft edition" hint="Pick the edition you tested on.">
    <CheckField type="radio" name="edition" label="Java" checked />
    <CheckField type="radio" name="edition" label="Bedrock" />
  </FieldGroup>
</Story>

<Story name="Autocomplete" source="forms/Autocomplete" description="Free text with suggestions.">
  <Autocomplete
    name="story-version"
    label={m.workbenchMinecraftVersion()}
    suggestionsLabel={m.searchSuggestions()}
    errorLabel={m.searchSuggestionError()}
    minLength={0}
    load={(query) =>
      Promise.resolve({
        items: versionOptions
          .filter((value) => value.toLowerCase().includes(query.trim().toLowerCase()))
          .map((value) => ({ value, label: value })),
      })}
  />
</Story>

<Story name="FilePicker" source="forms/FilePicker">
  <FilePicker
    label="Attach a schematic"
    onFiles={(selected) => (files = selected.map((file) => file.name))}
  />
  {#if files.length > 0}<p class="text-sm">Selected: {files.join(", ")}</p>{/if}
</Story>
