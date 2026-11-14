<script lang="ts">
  import { onDestroy, untrack } from "svelte";
  import { translate, type Locale, type StaticMessageKey } from "../../lib/i18n";
  import Button from "../../ui/svelte/Button.svelte";
  import Field from "../../ui/svelte/Field.svelte";
  import {
    createDraftFixture,
    validateDraft,
    versionOptions,
    type DraftErrors,
    type DraftInput,
  } from "./draft-fixture";
  let { locale, retryFirstSave }: { locale: Locale; retryFirstSave: boolean } = $props();
  const t = (key: StaticMessageKey): string => translate(locale, key);
  let input = $state<DraftInput>({ title: "", category: "door", width: "", version: "" });
  let search = $state("");
  let errors = $state<DraftErrors>({});
  let phase = $state<"idle" | "saving" | "saved" | "uploading" | "error">("idle");
  let progress = $state(0);
  let attachment = $state("");
  // The route scenario is fixed for this island's lifetime.
  const api = untrack(() => createDraftFixture(retryFirstSave));
  let operation: AbortController | undefined;
  onDestroy(() => operation?.abort());
  const busy = $derived(phase === "saving" || phase === "uploading");
  const versions = $derived(
    versionOptions.filter((version) => version.toLowerCase().includes(search.toLowerCase())),
  );
  function edited(): void {
    errors = {};
    phase = "idle";
  }
  async function save(): Promise<void> {
    errors = validateDraft(input);
    if (Object.keys(errors).length) return;
    operation?.abort();
    const controller = new AbortController();
    operation = controller;
    phase = "saving";
    try {
      const result = await api.save(input, controller.signal);
      if (result.kind === "invalid") {
        errors = result.errors;
        phase = "idle";
      } else phase = "saved";
    } catch {
      if (!controller.signal.aborted) phase = "error";
    }
  }
  async function upload(file: File | undefined): Promise<void> {
    if (!file) return;
    operation?.abort();
    const controller = new AbortController();
    operation = controller;
    progress = 0;
    attachment = "";
    phase = "uploading";
    try {
      attachment = await api.upload(file, controller.signal, (percent) => {
        progress = percent;
      });
      phase = "idle";
    } catch {
      if (!controller.signal.aborted) phase = "error";
    }
  }
</script>

<form
  class="sq-stack"
  novalidate
  onsubmit={(event) => {
    event.preventDefault();
    void save();
  }}
>
  <p class="sq-muted">{t("trial.disclaimer")}</p>
  <fieldset class="sq-stack sq-trial-fields" disabled={busy}>
    <legend class="sq-kicker">{t("trial.details")}</legend>
    <Field
      label={t("workbench.build-title")}
      bind:value={input.title}
      required
      oninput={edited}
      error={errors.title ? t(`trial.${errors.title}`) : undefined}
    />
    <div class="sq-field">
      <label for="trial-category">{t("trial.category")}</label><select
        class="sq-input"
        id="trial-category"
        bind:value={input.category}
        onchange={edited}
        ><option value="door">{t("trial.door")}</option><option value="utility"
          >{t("trial.utility")}</option
        ></select
      >
    </div>
    {#if input.category === "door"}<Field
        label={t("trial.width")}
        inputmode="numeric"
        bind:value={input.width}
        required
        oninput={edited}
        error={errors.width ? t(`trial.${errors.width}`) : undefined}
      />{/if}
    <Field
      label={t("trial.find-version")}
      type="search"
      bind:value={search}
      oninput={() => {
        input.version = "";
        edited();
      }}
    />
    <div class="sq-field">
      <label for="trial-version">{t("workbench.minecraft-version")}</label><select
        class="sq-input"
        id="trial-version"
        bind:value={input.version}
        aria-invalid={Boolean(errors.version)}
        aria-describedby={errors.version ? "trial-version-error" : undefined}
        required
        onchange={edited}
        ><option value="">{t("trial.choose-version")}</option
        >{#each versions as version (version)}<option value={version}>{version}</option
          >{/each}</select
      >{#if errors.version}<p id="trial-version-error" class="sq-error">
          {t(`trial.${errors.version}`)}
        </p>{/if}
    </div>
    <div class="sq-field">
      <label for="trial-upload">{t("trial.upload")}</label><input
        id="trial-upload"
        type="file"
        onchange={(event) => {
          void upload(event.currentTarget.files?.[0]);
          event.currentTarget.value = "";
        }}
      />
    </div>
  </fieldset>
  {#if phase === "uploading"}<div class="sq-field">
      <label for="trial-progress">{t("trial.uploading")}</label><progress
        id="trial-progress"
        max={100}
        value={progress}>{progress}%</progress
      >
    </div>{/if}
  {#if attachment}<p>{translate(locale, "trial.attachment", { filename: attachment })}</p>{/if}
  <div class="sq-row">
    <Button type="submit" variant="primary" disabled={busy}
      >{t(
        phase === "saving"
          ? "trial.saving"
          : phase === "error"
            ? "trial.retry"
            : "workbench.save-draft",
      )}</Button
    >{#if busy}<Button
        onclick={() => {
          operation?.abort();
          phase = "idle";
        }}>{t("trial.cancel")}</Button
      >{/if}
  </div>
  <p role="status">
    {t(
      phase === "saved"
        ? "trial.saved"
        : phase === "error"
          ? "trial.failed"
          : Object.keys(errors).length
            ? "trial.fix-errors"
            : "trial.ready",
    )}
  </p>
</form>
