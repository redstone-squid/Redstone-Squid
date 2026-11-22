<script lang="ts">
  import type { ProgressProps } from "@/ui/upload";
  let { label, value, max = 100 }: ProgressProps = $props();
</script>

<label class="sq-progress">
  <span>{label}</span>
  <progress {value} {max}></progress>
</label>
