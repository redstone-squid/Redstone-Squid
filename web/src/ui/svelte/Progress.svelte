<script lang="ts">
  import type { ProgressProps } from "../upload";
  let { label, value, max = 100 }: ProgressProps = $props();
</script>

<label class="sq-progress">
  <span>{label}</span>
  <progress {value} {max}></progress>
</label>
