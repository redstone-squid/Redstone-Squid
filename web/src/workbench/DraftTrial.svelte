<script lang="ts">
  import { onDestroy, untrack } from "svelte";
  import { translate, type Locale, type StaticMessageKey } from "../lib/i18n";
  import Button from "../ui/svelte/Button.svelte";
  import SelectField from "../ui/svelte/SelectField.svelte";
  import FilePicker from "../ui/svelte/FilePicker.svelte";
  import Progress from "../ui/svelte/Progress.svelte";
  import Notice from "../ui/svelte/Notice.svelte";
  import { layoutStyles } from "../ui/variants";
  import Field from "../ui/svelte/Field.svelte";
  import {
    createDraftFixture,
    draftFeedback,
    type DraftPhase,
    type DraftScenario,
    validateDraft,
    versionOptions,
    type DraftErrors,
    type DraftInput,
  } from "./draft-fixture";
  let { locale, scenario }: { locale: Locale; scenario?: DraftScenario } = $props();
  const t = (key: StaticMessageKey): string => translate(locale, key);
  let input = $state<DraftInput>({ title: "", category: "door", width: "", version: "" });
  let search = $state("");
  let errors = $state<DraftErrors>({});
  let phase = $state<DraftPhase>("idle");
  let progress = $state(0);
  let retryFile = $state<File>();
  let attachment = $state("");
  // The route scenario is fixed for this island's lifetime.
  const api = untrack(() => createDraftFixture(scenario));
  let operation: AbortController | undefined;
  onDestroy(() => operation?.abort());
  const busy = $derived(phase === "saving" || phase === "uploading");
  const feedback = $derived(draftFeedback(phase, errors));
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
      if (controller.signal.aborted || operation !== controller) return;
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
    retryFile = undefined;
    progress = 0;
    attachment = "";
    phase = "uploading";
    try {
      const uploaded = await api.upload(file, controller.signal, (percent) => {
        if (!controller.signal.aborted && operation === controller) progress = percent;
      });
      if (controller.signal.aborted || operation !== controller) return;
      attachment = uploaded;
      phase = "idle";
    } catch {
      if (!controller.signal.aborted) {
        retryFile = file;
        phase = "idle";
      }
    }
  }
</script>

<form
  class={layoutStyles({ pattern: "stack" })}
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
    <SelectField label={t("trial.category")} bind:value={input.category} onchange={edited}>
      <option value="door">{t("trial.door")}</option>
      <option value="utility">{t("trial.utility")}</option>
    </SelectField>
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
    <SelectField
      label={t("workbench.minecraft-version")}
      bind:value={input.version}
      required
      error={errors.version ? t(`trial.${errors.version}`) : undefined}
      onchange={edited}
    >
      <option value="">{t("trial.choose-version")}</option>
      {#each versions as version (version)}<option value={version}>{version}</option>{/each}
    </SelectField>
    <FilePicker label={t("trial.upload")} onFiles={([file]) => void upload(file)} />
  </fieldset>
  {#if phase === "uploading"}<Progress label={t("trial.uploading")} value={progress} />{/if}
  {#if retryFile}
    <Notice role="alert" tone="warning">{t("trial.uploadFailure")}</Notice>
    <div class={layoutStyles({ pattern: "actions" })}>
      <Button disabled={busy} onclick={() => void upload(retryFile)}
        >{t("trial.retryUpload")}</Button
      >
    </div>
  {/if}
  {#if attachment}<p>{translate(locale, "trial.attachment", { filename: attachment })}</p>{/if}
  <div class={layoutStyles({ pattern: "actions" })}>
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
  <Notice role="status" tone={feedback.tone}>{t(feedback.message)}</Notice>
</form>
