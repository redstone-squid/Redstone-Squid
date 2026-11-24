<script lang="ts">
  import * as m from "@/locales/messages.gen";
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

<Story name="Button" source="actions/Button" description={m.storyButtonDescription()}>
  {#each ["md", "sm"] as const as size (size)}
    <Variant label={m.storyButtonVariants({ size })}>
      <div class="flex flex-wrap items-center gap-3">
        <Button variant="primary" {size} icon={Plus}>{m.workbenchNewDraft()}</Button>
        <Button {size}>{m.storySecondary()}</Button>
        <Button variant="quiet" {size}>{m.storyQuiet()}</Button>
        <Button variant="danger" {size} icon={Trash}>{m.storyRevoke()}</Button>
        <Button {size} iconOnly icon={Plus} label={m.storyAdd()} />
      </div>
    </Variant>
  {/each}
  <Variant label={m.storyLinkDisabledLinkDisabled()}>
    <div class="flex flex-wrap items-center gap-3">
      <Button href="#button" variant="primary" iconEnd={ArrowUpRight}
        >{m.submissionPageTitle()}</Button
      >
      <Button href="#button" disabled>{m.storyNoNextPage()}</Button>
      <Button disabled>{m.workbenchUnavailable()}</Button>
    </div>
  </Variant>
  <Variant label={m.storyPending()}>
    <div class="flex flex-wrap items-center gap-3">
      <Button variant="primary" pending pendingLabel={m.storySaving()}>{m.storySave()}</Button>
      <Button
        icon={Plus}
        {pending}
        pendingLabel={m.storySaving()}
        onclick={() => {
          pending = true;
          setTimeout(() => (pending = false), 1500);
        }}>{m.storyClickToSave()}</Button
      >
      <Button variant="primary" fullWidth>{m.storyFullWidth()}</Button>
    </div>
  </Variant>
</Story>

<Story name="CopyButton" source="actions/CopyButton" description={m.storyCopyButtonDescription()}>
  <Variant label={m.storyWithTextIconOnlySizeMd()}>
    <div class="flex flex-wrap items-center gap-3">
      <Code>mc.example.net:25565</Code>
      <CopyButton
        value="mc.example.net:25565"
        label={m.storyCopyAddress()}
        copiedLabel={m.storyCopied()}
        failedLabel={m.storyCopyFailed()}
      />
      <CopyButton
        value="mc.example.net:25565"
        label={m.storyCopyAddress()}
        copiedLabel={m.storyCopied()}
        failedLabel={m.storyCopyFailed()}
        iconOnly
      />
      <CopyButton
        value="mc.example.net:25565"
        label={m.storyCopyAddress()}
        copiedLabel={m.storyCopied()}
        failedLabel={m.storyCopyFailed()}
        size="md"
      />
    </div>
  </Variant>
</Story>
