<script lang="ts">
  import { onMount, tick } from "svelte";
  import { cn } from "@/ui/cn";
  import { fieldClass, fieldMessageClass, inputClass } from "./styles";
  export type Suggestion = { value: string; label: string; description?: string | null };
  export type Suggestions = {
    items: Suggestion[];
    replacement?: { start: number; end: number } | null;
  };
  type Props = {
    id?: string;
    name: string;
    label: string;
    suggestionsLabel: string;
    errorLabel: string;
    value?: string;
    multiline?: boolean;
    maxLength?: number;
    minLength?: number;
    load: (value: string, cursor: number | null, signal: AbortSignal) => Promise<Suggestions>;
  };
  let {
    id: suppliedId,
    name,
    label,
    suggestionsLabel,
    errorLabel,
    value = $bindable(""),
    multiline = false,
    maxLength,
    minLength = 2,
    load,
  }: Props = $props();
  const generatedId = $props.id();
  const id = $derived(suppliedId ?? generatedId);
  const listId = `${generatedId}-suggestions`;
  let input: HTMLInputElement | HTMLTextAreaElement | undefined = $state();
  let result = $state<Suggestions>({ items: [] });
  let active = $state(-1);
  let focused = $state(false);
  let dismissed = $state(false);
  let composing = $state(false);
  let failed = $state(false);
  let cursor = $state<number | null>(null);
  const visible = $derived(focused && !dismissed && !composing ? result.items : []);

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
    result = { items: [] };
    active = -1;
    failed = false;
    if (!focused || dismissed || composing || query.trim().length < minLength) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void request(query, caret, controller.signal)
        .then((response) => {
          if (!controller.signal.aborted) result = response;
        })
        .catch(() => {
          if (!controller.signal.aborted) failed = true;
        });
    }, 250);
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
    active = -1;
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
    if (event.key === "Escape") {
      dismissed = true;
      active = -1;
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
  const attributes = $derived({
    id,
    name,
    maxlength: maxLength,
    role: "combobox" as const,
    autocomplete: "off" as const,
    "aria-autocomplete": "list" as const,
    "aria-controls": visible.length > 0 ? listId : undefined,
    "aria-expanded": visible.length > 0,
    "aria-activedescendant": active >= 0 && visible[active] ? `${listId}-${active}` : undefined,
    oninput: changed,
    onkeydown: keydown,
    onfocus: () => {
      focused = true;
    },
    onblur: () => {
      focused = false;
    },
    onselect: () => {
      if (multiline) cursor = input?.selectionStart ?? null;
    },
    oncompositionstart: () => {
      composing = true;
    },
    oncompositionend: (event: CompositionEvent) => {
      composing = false;
      changed(event);
    },
  });
</script>

<div class={cn(fieldClass, "relative")}>
  <label for={id}>{label}</label>
  {#if multiline}<textarea
      class={cn(inputClass, "resize-y")}
      {...attributes}
      bind:value
      bind:this={input}
      rows="3"></textarea>{:else}<input
      class={inputClass}
      {...attributes}
      bind:value
      bind:this={input}
    />{/if}
  {#if visible.length}
    <ul
      class="absolute inset-x-0 top-full z-10 mt-1 max-h-64 list-none overflow-y-auto rounded-sm border border-line bg-surface p-1 shadow-card"
      id={listId}
      role="listbox"
      aria-label={suggestionsLabel}
    >
      {#each visible as item, index (index)}
        <li role="presentation">
          <button
            type="button"
            class="grid w-full cursor-pointer rounded-sm border-0 bg-surface p-3 text-left wrap-anywhere text-fg hover:bg-surface-muted aria-selected:forced-highlight aria-selected:bg-surface-muted"
            role="option"
            aria-label={item.label}
            tabindex="-1"
            id={`${listId}-${index}`}
            aria-selected={active === index}
            onpointerdown={(event) => event.preventDefault()}
            onclick={() => choose(index)}
            >{item.label}{#if item.description}<span class={fieldMessageClass}
                >{item.description}</span
              >{/if}</button
          >
        </li>
      {/each}
    </ul>
  {/if}
  {#if failed && focused && !dismissed}<p class={fieldMessageClass} role="status">
      {errorLabel}
    </p>{/if}
</div>
