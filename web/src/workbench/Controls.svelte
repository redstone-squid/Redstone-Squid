<script lang="ts">
  import { translate, type Locale, type StaticMessageKey } from "../lib/i18n";
  import ArrowRight from "@lucide/svelte/icons/arrow-right";
  import Check from "@lucide/svelte/icons/check";
  import Plus from "@lucide/svelte/icons/plus";
  import Button from "../ui/svelte/Button.svelte";
  import { versionOptions } from "./draft-fixture";
  import Autocomplete from "../ui/svelte/Autocomplete.svelte";
  import Field from "../ui/svelte/Field.svelte";
  import Dialog from "../ui/svelte/Dialog.svelte";
  let { locale }: { locale: Locale } = $props();
  const t = (key: StaticMessageKey): string => translate(locale, key);
  let title = $state("");
  let submitted = $state(false);
  let saved = $state(false);
  const invalid = $derived(submitted && title.trim() === "");
</script>

<div class="sq-stack">
  <div class="sq-row">
    <Dialog
      title={t("workbench.create-a-draft")}
      description={t("workbench.draft-description")}
      closeLabel={t("workbench.close-dialog")}
    >
      {#snippet trigger()}<Plus size={16} aria-hidden="true" />{t("workbench.new-draft")}{/snippet}
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
          label={t("workbench.build-title")}
          bind:value={title}
          required
          oninput={() => {
            saved = false;
          }}
          error={invalid ? t("workbench.enter-a-title-for-your-build") : undefined}
        />
        <Button type="submit" variant="primary"
          >{t("workbench.save-draft")}<ArrowRight size={16} aria-hidden="true" /></Button
        >
        <p role="status">
          {saved
            ? t("workbench.example-draft-saved")
            : invalid
              ? t("workbench.check-the-highlighted-field")
              : ""}
        </p>
      </form>
    </Dialog>
    <Button
      onclick={() => document.getElementById("patterns")?.scrollIntoView({ behavior: "smooth" })}
      >{t("workbench.browse-examples")}</Button
    >
    <Button disabled>{t("workbench.unavailable")}</Button>
  </div>
  <div class="sq-grid">
    <Field
      label={t("workbench.creator")}
      hint={t("workbench.public-name-shown-on-the-build")}
      placeholder="CircuitSage"
    />
    <Autocomplete
      name="specimen-version"
      label={t("workbench.minecraft-version")}
      suggestionsLabel={t("search.suggestions")}
      errorLabel={t("search.suggestionError")}
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
    <Check size={16} aria-hidden="true" />{t("workbench.controls-description")}
  </p>
</div>
