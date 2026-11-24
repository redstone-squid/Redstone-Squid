<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/actions/Button.svelte";
  import SelectField from "@/ui/forms/SelectField.svelte";
  import FileDrop from "@/ui/forms/FileDrop.svelte";
  import Progress from "@/ui/feedback/Progress.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Text from "@/ui/content/Text.svelte";
  import TextField from "@/ui/forms/TextField.svelte";
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
  let { scenario }: { scenario?: DraftScenario } = $props();
  const input = $state<DraftInput>({ title: "", category: "door", width: "", version: "" });
  let search = $state("");
  let errors = $state<DraftErrors>({});
  let phase = $state<DraftPhase>("idle");
  let progress = $state(0);
  let retryFile = $state<File>();
  let attachment = $state("");
  let ready = $state(false);
  // Changing the simulated transport leaves the visitor's draft intact.
  const api = $derived(createDraftFixture(scenario));
  let operation: AbortController | undefined;
  onMount(() => {
    ready = true;
  });
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
  class="grid gap-6"
  novalidate
  onsubmit={(event) => {
    event.preventDefault();
    void save();
  }}
>
  <Text tone="muted">{m.trialDisclaimer()}</Text>
  <fieldset class="grid min-w-0 gap-6" disabled={!ready || busy}>
    <legend class="mb-3 text-sm font-semibold text-fg-muted">{m.trialDetails()}</legend>
    <TextField
      label={m.workbenchBuildTitle()}
      bind:value={input.title}
      required
      oninput={edited}
      error={errors.title !== undefined ? draftErrorMessages[errors.title]() : undefined}
    />
    <SelectField label={m.trialCategory()} bind:value={input.category} onchange={edited}>
      <option value="door">{m.trialDoor()}</option>
      <option value="utility">{m.trialUtility()}</option>
    </SelectField>
    {#if input.category === "door"}<TextField
        label={m.trialWidth()}
        inputmode="numeric"
        width="xs"
        bind:value={input.width}
        required
        oninput={edited}
        error={errors.width !== undefined ? draftErrorMessages[errors.width]() : undefined}
      />{/if}
    <TextField
      label={m.trialFindVersion()}
      type="search"
      bind:value={search}
      oninput={() => {
        input.version = "";
        edited();
      }}
    />
    <SelectField
      label={m.workbenchMinecraftVersion()}
      bind:value={input.version}
      required
      error={errors.version !== undefined ? draftErrorMessages[errors.version]() : undefined}
      onchange={edited}
    >
      <option value="">{m.trialChooseVersion()}</option>
      {#each versions as version (version)}<option value={version}>{version}</option>{/each}
    </SelectField>
    <FileDrop
      label={m.trialUpload()}
      labels={{ drop: m.trialUploadDrop(), browse: m.trialUploadBrowse() }}
      onFiles={([file]) => void upload(file)}
    />
  </fieldset>
  {#if phase === "uploading"}<Progress label={m.trialUploading()} value={progress} />{/if}
  {#if retryFile}
    <Notice role="alert" tone="warning">{m.trialUploadFailure()}</Notice>
    <Layout pattern="cluster">
      <Button disabled={busy} onclick={() => void upload(retryFile)}>{m.trialRetryUpload()}</Button>
    </Layout>
  {/if}
  {#if attachment}<p>{m.trialAttachment({ filename: attachment })}</p>{/if}
  <Layout pattern="cluster">
    <Button type="submit" variant="primary" disabled={!ready || busy}
      >{(phase === "saving"
        ? m.trialSaving
        : phase === "error"
          ? m.trialRetry
          : m.workbenchSaveDraft)()}</Button
    >{#if busy}<Button
        onclick={() => {
          operation?.abort();
          phase = "idle";
        }}>{m.trialCancel()}</Button
      >{/if}
  </Layout>
  <Notice role="status" tone={feedback.tone}>{feedback.message()}</Notice>
</form>
