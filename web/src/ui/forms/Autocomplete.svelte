<script lang="ts">
  import X from "@lucide/svelte/icons/x";
  import { onMount, tick } from "svelte";
  import { cn } from "@/ui/cn";
  import Spinner from "@/ui/feedback/Spinner.svelte";
  import type { FieldMessages, SuggestionSource, Suggestions } from "@/ui/forms/field";
  import FieldFrame from "./FieldFrame.svelte";
  import { QueryLoader } from "./query-loader.svelte";
  import { inputClass, popupOptionClass } from "./styles";

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
   * `clearable` adds a button named `clearLabel` that empties a single-line field.
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
    labels: SuggestionSource["labels"];
    load: SuggestionSource["load"];
    width?: FieldMessages["width"];
    clearable?: boolean;
    clearLabel?: string;
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
    clearable = false,
    clearLabel,
    ...frame
  }: Props = $props();
  const fieldId = $props.id();
  const listId = `${fieldId}-suggestions`;
  let input: HTMLInputElement | HTMLTextAreaElement | undefined = $state();
  let active = $state(-1);
  let focused = $state(false);
  let dismissed = $state(false);
  let composing = $state(false);
  let hydrated = $state(false);
  let cursor = $state<number | null>(null);
  const suggestions = new QueryLoader<Suggestions>(() => {
    if (!focused || dismissed || composing) return null;
    const [caret, request] = [cursor, load];
    return {
      query: value,
      load: (query, signal) => request(query, caret, signal),
      debounceMs,
      minQueryLength,
    };
  });
  const result = $derived(suggestions.current ?? { items: [] });
  const open = $derived(focused && !dismissed && !composing && suggestions.status !== "idle");
  const visible = $derived(open ? result.items : []);

  onMount(() => {
    hydrated = true;
    // A visitor can start typing into the server-rendered field before hydration.
    if (input && document.activeElement === input) {
      focused = true;
      cursor = input.selectionStart;
    }
  });

  function clear(): void {
    value = "";
    dismissed = true;
    input?.focus();
  }
  const showClear = $derived(
    clearable && !multiline && hydrated && value !== "" && disabled !== true,
  );

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
    active = -1;
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
    onfocus: () => {
      focused = true;
      active = -1;
    },
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
          class={cn(inputClass, "min-h-control w-full ps-3", clearable ? "pe-control" : "pe-3")}
          bind:value
          bind:this={input}
        />{/if}
      {#if showClear}<button
          type="button"
          class="absolute inset-y-0 end-0 grid w-control cursor-pointer place-items-center text-fg-muted hover:text-fg focus-visible:-outline-offset-3"
          aria-label={clearLabel}
          onclick={clear}><X class="size-4" aria-hidden="true" /></button
        >{/if}
      {#if open}
        <div
          class="absolute inset-x-0 top-full z-dropdown mt-1 max-h-72 overflow-y-auto rounded-sm border border-line-strong bg-surface-raised p-1 shadow-overlay motion-safe:transition-opacity motion-safe:duration-fast motion-safe:starting:opacity-0"
        >
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
              {#if suggestions.status === "loading"}<Spinner
                />{labels.loading}{:else if suggestions.status === "failed"}{labels.error}{:else}{labels.noResults}{/if}
            </p>
          {/if}
        </div>
      {/if}
    </div>
    <span class="sr-only" role="status"
      >{!open
        ? ""
        : suggestions.status === "failed"
          ? labels.error
          : suggestions.current === undefined
            ? ""
            : result.items.length > 0
              ? labels.count(result.items.length)
              : labels.noResults}</span
    >
  {/snippet}
</FieldFrame>
