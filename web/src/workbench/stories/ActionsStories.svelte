<script lang="ts">
  import ArrowUpRight from "@lucide/svelte/icons/arrow-up-right";
  import Plus from "@lucide/svelte/icons/plus";
  import Trash from "@lucide/svelte/icons/trash-2";
  import Button from "@/ui/actions/Button.svelte";
  import CopyButton from "@/ui/actions/CopyButton.svelte";
  import Code from "@/ui/content/Code.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  let pending = $state(false);
</script>

<Story
  name="Button"
  source="actions/Button"
  description="A command, or with href a link styled as one."
>
  {#each ["md", "sm"] as const as size (size)}
    <Variant label={`variants, size ${size}`}>
      <div class="flex flex-wrap items-center gap-3">
        <Button variant="primary" {size} icon={Plus}>New draft</Button>
        <Button {size}>Secondary</Button>
        <Button variant="quiet" {size}>Quiet</Button>
        <Button variant="danger" {size} icon={Trash}>Revoke</Button>
        <Button {size} iconOnly icon={Plus} label="Add" />
      </div>
    </Variant>
  {/each}
  <Variant label="link, disabled link, disabled">
    <div class="flex flex-wrap items-center gap-3">
      <Button href="#button" variant="primary" iconEnd={ArrowUpRight}>Submit a build</Button>
      <Button href="#button" disabled>No next page</Button>
      <Button disabled>Unavailable</Button>
    </div>
  </Variant>
  <Variant label="pending">
    <div class="flex flex-wrap items-center gap-3">
      <Button variant="primary" pending pendingLabel="Saving">Save</Button>
      <Button
        icon={Plus}
        {pending}
        pendingLabel="Saving"
        onclick={() => {
          pending = true;
          setTimeout(() => (pending = false), 1500);
        }}>Click to save</Button
      >
      <Button variant="primary" fullWidth>Full width</Button>
    </div>
  </Variant>
</Story>

<Story
  name="CopyButton"
  source="actions/CopyButton"
  description="Shows and announces the outcome for two seconds. Hidden until hydrated, so keep the value selectable beside it."
>
  <Variant label="with text, icon only, size md">
    <div class="flex flex-wrap items-center gap-3">
      <Code>mc.example.net:25565</Code>
      <CopyButton
        value="mc.example.net:25565"
        label="Copy address"
        copiedLabel="Copied"
        failedLabel="Copy failed"
      />
      <CopyButton
        value="mc.example.net:25565"
        label="Copy address"
        copiedLabel="Copied"
        failedLabel="Copy failed"
        iconOnly
      />
      <CopyButton
        value="mc.example.net:25565"
        label="Copy address"
        copiedLabel="Copied"
        failedLabel="Copy failed"
        size="md"
      />
    </div>
  </Variant>
</Story>
