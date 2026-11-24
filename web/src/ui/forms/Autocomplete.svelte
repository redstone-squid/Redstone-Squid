<script lang="ts" module>
  export type Suggestion = { value: string; label: string; description?: string | null };
  export type Suggestions = {
    items: Suggestion[];
    /** The span of the value that a chosen suggestion replaces; the whole value when absent. */
    replacement?: { start: number; end: number } | null;
  };
</script>

<script lang="ts">
  import { onMount, tick } from "svelte";
  import { cn } from "@/ui/cn";
  import Spinner from "@/ui/feedback/Spinner.svelte";
  import type { FieldMessages } from "@/ui/forms/field";
  import FieldFrame from "./FieldFrame.svelte";
  import { inputClass, popupClass, popupOptionClass } from "./styles";

  /**
   * Free text with suggestions, such as a search query or a credit name. The typed text is
   * the value; suggestions only help write it. Use `Combobox` to choose from a fixed set.
   *
   * After `minQueryLength` characters and `debounceMs` of quiet, `load(value, cursor, signal)`
   * fetches suggestions; a newer keystroke aborts the older request. A suggestion with a
   * `replacement` span replaces only that part of the value, such as the word at the cursor.
   *
   * Arrow keys move through suggestions, Enter takes the active one (otherwise it submits the
   * form), and Escape closes the list. Nothing loads while an input method is composing. The
   * number of suggestions is announced politely. Without JavaScript it is a plain input.
   */
  type Props = Omit<FieldMessages, "width"> & {
    name: string;
    id?: string;
    value?: string;
    multiline?: boolean;
    maxlength?: number;
    placeholder?: string;
    required?: boolean;
    disabled?: boolean;
    minQueryLength?: number;
    debounceMs?: number;
    labels: {
      suggestions: string;
      loading: string;
      error: string;
      noResults: string;
      count: (count: number) => string;
    };
    load: (value: string, cursor: number | null, signal: AbortSignal) => Promise<Suggestions>;
    width?: FieldMessages["width"];
    class?: string;
  };
  let {
    name,
    value = $bindable(""),
    multiline = false,
    maxlength,
    placeholder,
    required,
    disabled,
    minQueryLength = 2,
    debounceMs = 250,
    labels,
    load,
    ...frame
  }: Props = $props();
  const fieldId = $props.id();
  const listId = `${fieldId}-suggestions`;
  let input: HTMLInputElement | HTMLTextAreaElement | undefined = $state();
  let result = $state<Suggestions>({ items: [] });
  let phase = $state<"idle" | "loading" | "ready" | "failed">("idle");
  let active = $state(-1);
  let focused = $state(false);
  let dismissed = $state(false);
  let composing = $state(false);
  let cursor = $state<number | null>(null);
  const open = $derived(focused && !dismissed && !composing && phase !== "idle");
  const visible = $derived(open ? result.items : []);

  onMount(() => {
    // A visitor can start typing into the server-rendered field before hydration.
    if (input && document.activeElement === input) {
      focused = true;
      cursor = input.selectionStart;
    }
  });

  $effect(() => {
    const query = value;
    const caret = cursor;
    const request = load;
    active = -1;
    if (!focused || dismissed || composing || query.trim().length < minQueryLength) {
      result = { items: [] };
      phase = "idle";
      return;
    }
    // Earlier suggestions stay visible until the new ones arrive, so the list doesn't flicker.
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      if (result.items.length === 0) phase = "loading";
      request(query, caret, controller.signal)
        .then((response) => {
          if (controller.signal.aborted) return;
          result = response;
          phase = "ready";
        })
        .catch(() => {
          if (!controller.signal.aborted) phase = "failed";
        });
    }, debounceMs);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  });

  async function choose(index: number): Promise<void> {
    const item = visible[index];
    if (!item) return;
    const replacement = result.replacement;
    const span =
      replacement &&
      replacement.start >= 0 &&
      replacement.end >= replacement.start &&
      replacement.end <= value.length
        ? replacement
        : null;
    const caret = (span?.start ?? 0) + item.value.length;
    value = span ? value.slice(0, span.start) + item.value + value.slice(span.end) : item.value;
    dismissed = true;
    await tick();
    input?.focus();
    input?.setSelectionRange(caret, caret);
  }
  function changed(event: Event): void {
    const target = event.currentTarget as HTMLInputElement | HTMLTextAreaElement;
    value = target.value;
    cursor = target.selectionStart;
    dismissed = false;
  }
  async function keydown(event: KeyboardEvent): Promise<void> {
    if (event.isComposing || composing) return;
    if (event.key === "Escape" && open) {
      event.preventDefault();
      dismissed = true;
      return;
    }
    if (visible.length === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      active =
        event.key === "ArrowDown"
          ? (active + 1) % visible.length
          : active <= 0
            ? visible.length - 1
            : active - 1;
      await tick();
      document.getElementById(`${listId}-${active}`)?.scrollIntoView({ block: "nearest" });
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      await choose(active);
    }
  }
  const handlers = {
    oninput: changed,
    onkeydown: keydown,
    onfocus: () => (focused = true),
    onblur: () => (focused = false),
    onselect: () => {
      if (multiline) cursor = input?.selectionStart ?? null;
    },
    oncompositionstart: () => (composing = true),
    oncompositionend: (event: CompositionEvent) => {
      composing = false;
      changed(event);
    },
  };
</script>

<FieldFrame {...frame} class={cn("relative", frame.class)}>
  {#snippet children(field)}
    {@const attributes = {
      ...field,
      ...handlers,
      name,
      maxlength,
      placeholder,
      required,
      disabled,
      role: "combobox" as const,
      autocomplete: "off" as const,
      "aria-autocomplete": "list" as const,
      "aria-controls": visible.length > 0 ? listId : undefined,
      "aria-expanded": visible.length > 0,
      "aria-activedescendant": active >= 0 && visible[active] ? `${listId}-${active}` : undefined,
    }}
    <div class="relative">
      {#if multiline}<textarea
          {...attributes}
          class={cn(inputClass, "block min-h-24 w-full resize-y px-3")}
          bind:value
          bind:this={input}
          rows="3"></textarea>{:else}<input
          {...attributes}
          class={cn(inputClass, "min-h-control w-full px-3")}
          bind:value
          bind:this={input}
        />{/if}
      {#if open}
        <div class={popupClass}>
          {#if visible.length > 0}
            <ul class="grid list-none" id={listId} role="listbox" aria-label={labels.suggestions}>
              {#each visible as item, index (index)}
                <!-- Keyboard users choose through the input (aria-activedescendant). -->
                <!-- svelte-ignore a11y_click_events_have_key_events -->
                <li
                  id={`${listId}-${index}`}
                  role="option"
                  aria-selected={active === index}
                  aria-labelledby={`${listId}-${index}-label`}
                  aria-describedby={item.description != null
                    ? `${listId}-${index}-description`
                    : undefined}
                  class={popupOptionClass}
                  onpointerdown={(event) => event.preventDefault()}
                  onclick={() => choose(index)}
                >
                  <span id={`${listId}-${index}-label`}>{item.label}</span>
                  {#if item.description != null}<span
                      id={`${listId}-${index}-description`}
                      class="text-xs text-fg-muted">{item.description}</span
                    >{/if}
                </li>
              {/each}
            </ul>
          {:else}
            <p class="flex items-center gap-2 px-3 py-2.5 text-sm text-fg-muted">
              {#if phase === "loading"}<Spinner
                />{labels.loading}{:else if phase === "failed"}{labels.error}{:else}{labels.noResults}{/if}
            </p>
          {/if}
        </div>
      {/if}
    </div>
    <span class="sr-only" role="status"
      >{open && phase === "ready"
        ? result.items.length > 0
          ? labels.count(result.items.length)
          : labels.noResults
        : open && phase === "failed"
          ? labels.error
          : ""}</span
    >
  {/snippet}
</FieldFrame>
