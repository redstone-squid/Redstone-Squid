<script lang="ts">
  import { onDestroy, untrack } from "svelte";
  import type { Locale } from "../locales/locale";
  import * as m from "../locales/messages";
  import Button from "../ui/svelte/Button.svelte";
  import SelectField from "../ui/svelte/SelectField.svelte";
  import FilePicker from "../ui/svelte/FilePicker.svelte";
  import Progress from "../ui/svelte/Progress.svelte";
  import Notice from "../ui/svelte/Notice.svelte";
  import { layoutStyles } from "../ui/variants";
  import Field from "../ui/svelte/Field.svelte";
  import {
    createDraftFixture,
    draftErrorMessages,
    draftFeedback,
    type DraftPhase,
    type DraftScenario,
    validateDraft,
    versionOptions,
    type DraftErrors,
    type DraftInput,
  } from "./draft-fixture";
  let { locale, scenario }: { locale: Locale; scenario?: DraftScenario } = $props();
  const input = $state<DraftInput>({ title: "", category: "door", width: "", version: "" });
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
    if (Object.keys(errors).length > 0) return;
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
  <p class="sq-muted">{m.trialDisclaimer(locale)}</p>
  <fieldset class="sq-stack sq-trial-fields" disabled={busy}>
    <legend class="sq-kicker">{m.trialDetails(locale)}</legend>
    <Field
      label={m.workbenchBuildTitle(locale)}
      bind:value={input.title}
      required
      oninput={edited}
      error={errors.title !== undefined ? draftErrorMessages[errors.title](locale) : undefined}
    />
    <SelectField label={m.trialCategory(locale)} bind:value={input.category} onchange={edited}>
      <option value="door">{m.trialDoor(locale)}</option>
      <option value="utility">{m.trialUtility(locale)}</option>
    </SelectField>
    {#if input.category === "door"}<Field
        label={m.trialWidth(locale)}
        inputmode="numeric"
        bind:value={input.width}
        required
        oninput={edited}
        error={errors.width !== undefined ? draftErrorMessages[errors.width](locale) : undefined}
      />{/if}
    <Field
      label={m.trialFindVersion(locale)}
      type="search"
      bind:value={search}
      oninput={() => {
        input.version = "";
        edited();
      }}
    />
    <SelectField
      label={m.workbenchMinecraftVersion(locale)}
      bind:value={input.version}
      required
      error={errors.version !== undefined ? draftErrorMessages[errors.version](locale) : undefined}
      onchange={edited}
    >
      <option value="">{m.trialChooseVersion(locale)}</option>
      {#each versions as version (version)}<option value={version}>{version}</option>{/each}
    </SelectField>
    <FilePicker label={m.trialUpload(locale)} onFiles={([file]) => void upload(file)} />
  </fieldset>
  {#if phase === "uploading"}<Progress label={m.trialUploading(locale)} value={progress} />{/if}
  {#if retryFile}
    <Notice role="alert" tone="warning">{m.trialUploadFailure(locale)}</Notice>
    <div class={layoutStyles({ pattern: "actions" })}>
      <Button disabled={busy} onclick={() => void upload(retryFile)}
        >{m.trialRetryUpload(locale)}</Button
      >
    </div>
  {/if}
  {#if attachment}<p>{m.trialAttachment(locale, { filename: attachment })}</p>{/if}
  <div class={layoutStyles({ pattern: "actions" })}>
    <Button type="submit" variant="primary" disabled={busy}
      >{(phase === "saving"
        ? m.trialSaving
        : phase === "error"
          ? m.trialRetry
          : m.workbenchSaveDraft)(locale)}</Button
    >{#if busy}<Button
        onclick={() => {
          operation?.abort();
          phase = "idle";
        }}>{m.trialCancel(locale)}</Button
      >{/if}
  </div>
  <Notice role="status" tone={feedback.tone}>{feedback.message(locale)}</Notice>
</form>
