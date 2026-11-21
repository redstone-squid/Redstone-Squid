<script lang="ts">
  import { takeSelectedFiles, type FilePickerProps } from "../upload";
  let { label, onFiles, accept, multiple, disabled }: FilePickerProps = $props();
</script>

<label class="sq-file-picker">
  <span>{label}</span>
  <input
    type="file"
    {accept}
    {multiple}
    {disabled}
    onchange={(event) => {
      const files = takeSelectedFiles(event.currentTarget);
      if (files.length) onFiles(files);
    }}
  />
</label>
