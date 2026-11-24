<script lang="ts">
  import Pencil from "@lucide/svelte/icons/pencil";
  import Button from "@/ui/actions/Button.svelte";
  import Tooltip from "@/ui/overlays/Tooltip.svelte";
</script>

<Tooltip content="Edit build">
  {#snippet children(props)}<Button
      {...props}
      iconOnly
      icon={Pencil}
      label="Edit build"
    />{/snippet}
</Tooltip>
