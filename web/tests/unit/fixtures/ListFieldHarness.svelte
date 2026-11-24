<script lang="ts">
  import ListField from "@/ui/forms/ListField.svelte";
  import TextField from "@/ui/forms/TextField.svelte";

  let { initial = ["a.example"] }: { initial?: string[] } = $props();
  // svelte-ignore state_referenced_locally
  let links = $state(initial.map((url) => ({ url })));
</script>

<ListField
  label="Links"
  bind:items={links}
  newItem={() => ({ url: "" })}
  reorderable
  labels={{
    add: "Add link",
    remove: (row) => `Remove ${row}`,
    moveUp: (row) => `Move ${row} up`,
    moveDown: (row) => `Move ${row} down`,
    rowLabel: (n) => `Link ${n}`,
    removed: (n) => `Link ${n} removed`,
  }}
>
  {#snippet row(link)}
    <TextField
      label="Address"
      value={link.url}
      oninput={(event) => (link.url = event.currentTarget.value)}
    />
  {/snippet}
</ListField>
