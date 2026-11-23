<script lang="ts">
  import DraftDialog from "@/workbench/DraftDialog.svelte";
  import Story from "./Story.svelte";
</script>

<Story
  name="Dialog"
  source="overlays/Dialog"
  description="Modal form with focus trap; focus returns to the trigger on close."
>
  <DraftDialog />
</Story>
