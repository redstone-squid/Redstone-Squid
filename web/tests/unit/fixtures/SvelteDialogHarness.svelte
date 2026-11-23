<script lang="ts">
  import Button from "@/ui/actions/Button.svelte";
  import Dialog from "@/ui/overlays/Dialog.svelte";
  let open = $state(false);
</script>

<Dialog
  bind:open
  title="Draft"
  description="Edit the draft"
  closeLabel="Close"
  triggerProps={{ variant: "secondary", "aria-label": "Edit draft" }}
  contentProps={{ class: "wide-dialog" }}
>
  {#snippet trigger()}Edit{/snippet}
  <Button
    onclick={() => {
      open = false;
    }}>Finish</Button
  >
</Dialog>
