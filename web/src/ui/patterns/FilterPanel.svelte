<script lang="ts">
  import { debounce } from "es-toolkit";
  import { onMount, type Snippet } from "svelte";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";
  import Badge from "@/ui/feedback/Badge.svelte";

  /**
   * Filter fields in a GET form, so every filtered listing has a URL and works without
   * JavaScript. `hidden` keeps parameters the fields don't own, such as the query and view.
   * `activeLabel` ("2 active") is shown beside the title; `resetHref` is the unfiltered URL.
   *
   * With `autoApply`, a hydrated panel hides its Apply button and loads the filtered URL
   * 600 ms after the last change, leaving out empty fields. Text fields fire `change` on blur
   * or Enter, so typing does not reload the page.
   */
  type Props = {
    action: string;
    title: string;
    activeLabel?: string;
    applyLabel: string;
    resetHref: string;
    resetLabel: string;
    hidden?: Record<string, string>;
    autoApply?: boolean;
    class?: string;
    children: Snippet;
  };
  let {
    action,
    title,
    activeLabel,
    applyLabel,
    resetHref,
    resetLabel,
    hidden = {},
    autoApply = false,
    class: className,
    children,
  }: Props = $props();
  const id = $props.id();
  let hydrated = $state(false);
  onMount(() => {
    hydrated = true;
    return () => apply.cancel();
  });

  const apply = debounce((form: HTMLFormElement) => {
    const filled = [...new FormData(form)].filter(
      (entry): entry is [string, string] => typeof entry[1] === "string" && entry[1] !== "",
    );
    const search = new URLSearchParams(filled).toString();
    location.assign(search === "" ? action : `${action}?${search}`);
  }, 600);
</script>

<form
  method="get"
  {action}
  aria-labelledby={`${id}-title`}
  class={cn("grid min-w-0 content-start gap-5", className)}
  onchange={autoApply ? (event) => apply(event.currentTarget) : undefined}
>
  <div class="flex min-w-0 flex-wrap items-center gap-2">
    <h2 id={`${id}-title`} class="text-lg font-bold">{title}</h2>
    {#if activeLabel !== undefined}<Badge tone="accent">{activeLabel}</Badge>{/if}
  </div>
  {#each Object.entries(hidden) as [key, value] (key)}
    <input type="hidden" name={key} {value} />
  {/each}
  <div class="grid min-w-0 gap-4">{@render children()}</div>
  <div class="flex flex-wrap items-center gap-3">
    {#if !(autoApply && hydrated)}<Button type="submit" variant="primary">{applyLabel}</Button>{/if}
    <Button href={resetHref} variant="quiet">{resetLabel}</Button>
  </div>
</form>
