<script lang="ts">
  import Dialog from "@/ui/svelte/Dialog.svelte";
  import Button from "@/ui/svelte/Button.svelte";
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
