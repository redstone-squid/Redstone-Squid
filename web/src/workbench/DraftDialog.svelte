<script lang="ts">
  import ArrowRight from "@lucide/svelte/icons/arrow-right";
  import Plus from "@lucide/svelte/icons/plus";
  import { onMount } from "svelte";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/actions/Button.svelte";
  import TextField from "@/ui/forms/TextField.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Dialog from "@/ui/overlays/Dialog.svelte";

  // The trigger stays disabled until hydration so a click never lands on a dead button.
  let ready = $state(false);
  onMount(() => {
    ready = true;
  });
  let title = $state("");
  let submitted = $state(false);
  let saved = $state(false);
  const invalid = $derived(submitted && title.trim() === "");
</script>

<Layout pattern="cluster">
  <Dialog
    title={m.workbenchCreateADraft()}
    description={m.workbenchDraftDescription()}
    closeLabel={m.workbenchCloseDialog()}
    triggerProps={{ disabled: !ready }}
  >
    {#snippet trigger()}<Plus class="size-4" aria-hidden="true" />{m.workbenchNewDraft()}{/snippet}
    <form
      novalidate
      onsubmit={(event) => {
        event.preventDefault();
        submitted = true;
        saved = Boolean(title.trim());
      }}
    >
      <Layout pattern="stack">
        <TextField
          label={m.workbenchBuildTitle()}
          bind:value={title}
          required
          oninput={() => {
            saved = false;
          }}
          error={invalid ? m.workbenchEnterATitleForYourBuild() : undefined}
        />
        <Button type="submit" variant="primary" iconEnd={ArrowRight}
          >{m.workbenchSaveDraft()}</Button
        >
        <p role="status">
          {saved
            ? m.workbenchExampleDraftSaved()
            : invalid
              ? m.workbenchCheckTheHighlightedField()
              : ""}
        </p>
      </Layout>
    </form>
  </Dialog>
  <Button disabled>{m.workbenchUnavailable()}</Button>
</Layout>
