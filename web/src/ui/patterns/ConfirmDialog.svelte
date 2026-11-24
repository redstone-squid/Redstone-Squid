<script lang="ts">
  import Button from "@/ui/actions/Button.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import TextField from "@/ui/forms/TextField.svelte";
  import Dialog from "@/ui/overlays/Dialog.svelte";
  import type { OverlayTrigger } from "@/ui/overlays/props";

  /**
   * Asks before an irreversible action. Name the object in `title` ("Revoke Laptop CLI?") and
   * the verb in `confirmLabel`. `consequences` lists what will be lost; `requireText` asks the
   * user to type a value, such as the account name, before a broad action.
   *
   * A `danger` dialog starts focus on Cancel. While `onConfirm` runs the dialog cannot be
   * dismissed; if it rejects, the dialog stays open showing `error`, which the caller sets.
   * On success it closes and focus goes to `afterConfirmFocus`, for when the trigger's row is
   * gone. Without JavaScript the trigger links to `fallbackHref`, a page with the same text
   * and a POST form.
   */
  type Props = {
    open?: boolean;
    title: string;
    description?: string;
    consequences?: string[];
    confirmLabel: string;
    cancelLabel: string;
    closeLabel: string;
    pendingLabel?: string;
    tone?: "danger" | "default";
    requireText?: { label: string; expected: string; error: string };
    error?: string;
    onConfirm: () => Promise<void>;
    trigger?: OverlayTrigger;
    fallbackHref?: string;
    afterConfirmFocus?: () => HTMLElement | null | undefined;
  };
  let {
    open = $bindable(false),
    title,
    description,
    consequences = [],
    confirmLabel,
    cancelLabel,
    closeLabel,
    pendingLabel,
    tone = "danger",
    requireText,
    error,
    onConfirm,
    trigger,
    fallbackHref,
    afterConfirmFocus,
  }: Props = $props();

  let pending = $state(false);
  let confirmed = false;
  let typed = $state("");
  let mismatch = $state(false);

  function reset(isOpen: boolean): void {
    if (!isOpen) return;
    confirmed = false;
    typed = "";
    mismatch = false;
  }

  async function confirm(): Promise<void> {
    if (requireText && typed.trim() !== requireText.expected) {
      mismatch = true;
      return;
    }
    pending = true;
    try {
      await onConfirm();
      confirmed = true;
      open = false;
    } catch {
      // The caller explains the failure through `error`.
    } finally {
      pending = false;
    }
  }
</script>

<Dialog
  bind:open
  size="sm"
  {title}
  {description}
  {closeLabel}
  {trigger}
  {fallbackHref}
  dismissible={!pending}
  initialFocus={requireText
    ? "first-field"
    : tone === "danger"
      ? "[data-cancel]"
      : "[data-confirm]"}
  returnFocus={() => (confirmed ? afterConfirmFocus?.() : undefined)}
  onOpenChange={reset}
>
  <div class="grid gap-4 text-sm">
    {#if consequences.length > 0}
      <ul class="grid list-disc gap-1 ps-5">
        {#each consequences as consequence (consequence)}<li>{consequence}</li>{/each}
      </ul>
    {/if}
    {#if requireText}
      <TextField
        label={requireText.label}
        bind:value={typed}
        error={mismatch && typed.trim() !== requireText.expected ? requireText.error : undefined}
        autocomplete="off"
        spellcheck="false"
        onkeydown={(event) => {
          if (event.key === "Enter") void confirm();
        }}
      />
    {/if}
    {#if error !== undefined && error !== ""}<Notice tone="danger" role="alert">{error}</Notice
      >{/if}
  </div>
  {#snippet footer()}
    <Button data-cancel disabled={pending} onclick={() => (open = false)}>{cancelLabel}</Button>
    <Button
      data-confirm
      variant={tone === "danger" ? "danger" : "primary"}
      {pending}
      {pendingLabel}
      onclick={confirm}>{confirmLabel}</Button
    >
  {/snippet}
</Dialog>
