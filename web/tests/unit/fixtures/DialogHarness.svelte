<script lang="ts">
  import Button from "@/ui/actions/Button.svelte";
  import TextField from "@/ui/forms/TextField.svelte";
  import Dialog from "@/ui/overlays/Dialog.svelte";
  import Sheet from "@/ui/overlays/Sheet.svelte";
  import type { ModalProps } from "@/ui/overlays/props";

  let {
    sheet = false,
    withField = true,
    ...props
  }: Partial<ModalProps> & { sheet?: boolean; withField?: boolean } = $props();
  let open = $state(false);
  const Modal = $derived(sheet ? Sheet : Dialog);
</script>

<Button id="elsewhere">Elsewhere</Button>
<Modal
  bind:open
  title="Rename draft"
  description="Pick a short title"
  closeLabel="Close"
  trigger={{ label: "Rename" }}
  {...props}
>
  {#if withField}<TextField label="Title" />{/if}
  {#snippet footer()}
    <Button data-done onclick={() => (open = false)}>Done</Button>
  {/snippet}
</Modal>
