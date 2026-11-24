<script lang="ts">
  import Check from "@lucide/svelte/icons/check";
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import CloudOff from "@lucide/svelte/icons/cloud-off";
  import Dot from "@lucide/svelte/icons/dot";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { onDestroy } from "svelte";
  import { cn } from "@/ui/cn";
  import Button from "@/ui/actions/Button.svelte";
  import Timestamp from "@/ui/content/Timestamp.svelte";
  import type { SaveState } from "./save-state";
  import Spinner from "./Spinner.svelte";

  /**
   * Where an autosaving form stands: unsaved changes, saving, saved (with `savedAt`), offline,
   * failed or in conflict. `onRetry` adds a retry button to the error state.
   *
   * Screen readers hear only arrivals at saved, error and conflict, at most once every
   * 5 seconds, so steady autosaving does not interrupt typing. A later state replaces one
   * still waiting.
   */
  type Props = {
    state: SaveState;
    labels: Record<SaveState, string> & { retry?: string };
    savedAt?: { datetime: string; label: string };
    onRetry?: () => void;
    class?: string;
  };
  let { state: saveState, labels, savedAt, onRetry, class: className }: Props = $props();

  const looks = {
    idle: { icon: undefined, class: "text-fg-muted" },
    dirty: { icon: Dot, class: "text-fg-muted" },
    saving: { icon: undefined, class: "text-fg-muted" },
    saved: { icon: Check, class: "text-success" },
    offline: { icon: CloudOff, class: "text-warning" },
    error: { icon: CircleAlert, class: "text-danger" },
    conflict: { icon: TriangleAlert, class: "text-warning" },
  } satisfies Record<SaveState, { icon: unknown; class: string }>;
  const look = $derived(looks[saveState]);

  const THROTTLE_MS = 5000;
  let announcement = $state("");
  let lastAnnounced = -Infinity;
  let timer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    if (saveState !== "saved" && saveState !== "error" && saveState !== "conflict") return;
    const text = labels[saveState];
    clearTimeout(timer);
    const wait = lastAnnounced + THROTTLE_MS - Date.now();
    const speak = () => {
      announcement = text;
      lastAnnounced = Date.now();
    };
    if (wait <= 0) speak();
    else timer = setTimeout(speak, wait);
  });
  onDestroy(() => clearTimeout(timer));
</script>

<div class={cn("flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm", className)}>
  <span class={cn("inline-flex min-w-0 items-center gap-1.5", look.class)}>
    {#if saveState === "saving"}<Spinner />{:else if look.icon}<look.icon
        class="size-4 shrink-0"
        aria-hidden="true"
      />{/if}
    <span class={saveState === "error" || saveState === "conflict" ? "text-fg" : "text-fg-muted"}
      >{labels[saveState]}{#if saveState === "saved" && savedAt}
        <span class="ms-1">· <Timestamp datetime={savedAt.datetime} label={savedAt.label} /></span
        >{/if}</span
    >
  </span>
  {#if saveState === "error" && onRetry}<Button size="sm" variant="quiet" onclick={onRetry}
      >{labels.retry}</Button
    >{/if}
  <span class="sr-only" role="status">{announcement}</span>
</div>
