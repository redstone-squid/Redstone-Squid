<script lang="ts">
  import type { Locale } from "../locales/locale";
  import * as m from "../locales/messages";
  import ArrowRight from "@lucide/svelte/icons/arrow-right";
  import Check from "@lucide/svelte/icons/check";
  import Plus from "@lucide/svelte/icons/plus";
  import Button from "../ui/svelte/Button.svelte";
  import { versionOptions } from "./draft-fixture";
  import Autocomplete from "../ui/svelte/Autocomplete.svelte";
  import Field from "../ui/svelte/Field.svelte";
  import Dialog from "../ui/svelte/Dialog.svelte";
  let { locale }: { locale: Locale } = $props();
  let title = $state("");
  let submitted = $state(false);
  let saved = $state(false);
  const invalid = $derived(submitted && title.trim() === "");
</script>

<div class="sq-stack">
  <div class="sq-row">
    <Dialog
      title={m.workbenchCreateADraft(locale)}
      description={m.workbenchDraftDescription(locale)}
      closeLabel={m.workbenchCloseDialog(locale)}
    >
      {#snippet trigger()}<Plus size={16} aria-hidden="true" />{m.workbenchNewDraft(
          locale,
        )}{/snippet}
      <form
        class="sq-stack"
        novalidate
        onsubmit={(event) => {
          event.preventDefault();
          submitted = true;
          saved = Boolean(title.trim());
        }}
      >
        <Field
          label={m.workbenchBuildTitle(locale)}
          bind:value={title}
          required
          oninput={() => {
            saved = false;
          }}
          error={invalid ? m.workbenchEnterATitleForYourBuild(locale) : undefined}
        />
        <Button type="submit" variant="primary"
          >{m.workbenchSaveDraft(locale)}<ArrowRight size={16} aria-hidden="true" /></Button
        >
        <p role="status">
          {saved
            ? m.workbenchExampleDraftSaved(locale)
            : invalid
              ? m.workbenchCheckTheHighlightedField(locale)
              : ""}
        </p>
      </form>
    </Dialog>
    <Button
      onclick={() => document.getElementById("patterns")?.scrollIntoView({ behavior: "smooth" })}
      >{m.workbenchBrowseExamples(locale)}</Button
    >
    <Button disabled>{m.workbenchUnavailable(locale)}</Button>
  </div>
  <div class="sq-grid">
    <Field
      label={m.workbenchCreator(locale)}
      hint={m.workbenchPublicNameShownOnTheBuild(locale)}
      placeholder="CircuitSage"
    />
    <Autocomplete
      name="specimen-version"
      label={m.workbenchMinecraftVersion(locale)}
      suggestionsLabel={m.searchSuggestions(locale)}
      errorLabel={m.searchSuggestionError(locale)}
      minLength={0}
      load={(query) =>
        Promise.resolve({
          items: versionOptions
            .filter((value) => value.toLowerCase().includes(query.trim().toLowerCase()))
            .map((value) => ({ value, label: value })),
        })}
    />
  </div>
  <p class="sq-inline-note">
    <Check size={16} aria-hidden="true" />{m.workbenchControlsDescription(locale)}
  </p>
</div>
