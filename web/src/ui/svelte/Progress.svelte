<script lang="ts">
  import type { ProgressProps } from "@/ui/upload";
  let { label, value, max = 100 }: ProgressProps = $props();
</script>

<label class="grid min-w-0 gap-2 text-sm font-semibold">
  <span>{label}</span>
  <progress class="w-full accent-accent" {value} {max}></progress>
</label>
