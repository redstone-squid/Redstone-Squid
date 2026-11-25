<script lang="ts">
  import Button from "@/ui/actions/Button.svelte";
  import CopyButton from "@/ui/actions/CopyButton.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";

  /**
   * A secret shown once, such as an installation key or a merge code. The value sits in a
   * selectable value with a copy button inside its box, under a `warning` that it cannot be shown again.
   *
   * Until the user presses the acknowledge button, leaving the page asks for confirmation.
   * The button links to `acknowledgeHref` when given, so it also works without JavaScript.
   */
  type Props = {
    value: string;
    label: string;
    warning: string;
    /** Extra detail under the value, such as when a code expires. */
    detail?: string;
    copyLabels: { label: string; copied: string; failed: string };
    acknowledgeLabel: string;
    acknowledgeHref?: string;
    onAcknowledge?: () => void;
    /** False skips the leave-page confirmation, as in the workbench. */
    guardUnload?: boolean;
  };
  let {
    value,
    label,
    warning,
    detail,
    copyLabels,
    acknowledgeLabel,
    acknowledgeHref,
    onAcknowledge,
    guardUnload = true,
  }: Props = $props();
  let acknowledged = $state(false);
</script>

<svelte:window
  onbeforeunload={(event) => {
    if (guardUnload && !acknowledged) event.preventDefault();
  }}
/>

<div class="grid min-w-0 gap-4">
  <Notice tone="warning">{warning}</Notice>
  <div class="grid min-w-0 gap-2">
    <p class="text-sm font-semibold">{label}</p>
    <!-- The copy button sits inside the value's box, as a field's adornment would. One line of
         the value is exactly as tall as the button, so the box is a control's height. -->
    <div
      class="flex min-h-control min-w-0 items-start gap-2 rounded-sm border border-line-strong bg-surface-muted p-1 ps-3"
    >
      <code class="min-w-0 flex-1 py-2 font-mono text-sm break-all select-all">{value}</code>
      <CopyButton
        {value}
        label={copyLabels.label}
        copiedLabel={copyLabels.copied}
        failedLabel={copyLabels.failed}
      />
    </div>
    {#if detail !== undefined}<p class="text-sm text-fg-muted">{detail}</p>{/if}
  </div>
  <div>
    <Button
      variant="primary"
      href={acknowledgeHref}
      onclick={() => {
        acknowledged = true;
        onAcknowledge?.();
      }}>{acknowledgeLabel}</Button
    >
  </div>
</div>
