<script lang="ts">
  import { onMount } from "svelte";
  import * as m from "@/locales/messages.gen";
  import ArrowRight from "@lucide/svelte/icons/arrow-right";
  import Check from "@lucide/svelte/icons/check";
  import Plus from "@lucide/svelte/icons/plus";
  import Button from "@/ui/svelte/Button.svelte";
  import { versionOptions } from "./draft-fixture";
  import Autocomplete from "@/ui/svelte/Autocomplete.svelte";
  import Field from "@/ui/svelte/Field.svelte";
  import Dialog from "@/ui/svelte/Dialog.svelte";
  import { buttonStyles, layoutStyles } from "@/ui/variants";
  let ready = $state(false);
  onMount(() => {
    ready = true;
  });
  let title = $state("");
  let submitted = $state(false);
  let saved = $state(false);
  const invalid = $derived(submitted && title.trim() === "");
</script>

<div class={layoutStyles({ pattern: "stack" })}>
  <div class={layoutStyles({ pattern: "row" })}>
    <Dialog
      title={m.workbenchCreateADraft()}
      description={m.workbenchDraftDescription()}
      closeLabel={m.workbenchCloseDialog()}
      triggerProps={{ disabled: !ready }}
    >
      {#snippet trigger()}<Plus size={16} aria-hidden="true" />{m.workbenchNewDraft()}{/snippet}
      <form
        class={layoutStyles({ pattern: "stack" })}
        novalidate
        onsubmit={(event) => {
          event.preventDefault();
          submitted = true;
          saved = Boolean(title.trim());
        }}
      >
        <Field
          label={m.workbenchBuildTitle()}
          bind:value={title}
          required
          oninput={() => {
            saved = false;
          }}
          error={invalid ? m.workbenchEnterATitleForYourBuild() : undefined}
        />
        <Button type="submit" variant="primary"
          >{m.workbenchSaveDraft()}<ArrowRight size={16} aria-hidden="true" /></Button
        >
        <p role="status">
          {saved
            ? m.workbenchExampleDraftSaved()
            : invalid
              ? m.workbenchCheckTheHighlightedField()
              : ""}
        </p>
      </form>
    </Dialog>
    <a class={buttonStyles()} href="#patterns">{m.workbenchBrowseExamples()}</a>
    <Button disabled>{m.workbenchUnavailable()}</Button>
  </div>
  <div class={layoutStyles({ pattern: "grid" })}>
    <Field
      label={m.workbenchCreator()}
      hint={m.workbenchPublicNameShownOnTheBuild()}
      placeholder="CircuitSage"
    />
    <Autocomplete
      name="specimen-version"
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
  </div>
  <p class="flex items-center gap-2 text-xs text-fg-muted">
    <Check class="shrink-0" size={16} aria-hidden="true" />{m.workbenchControlsDescription()}
  </p>
</div>
