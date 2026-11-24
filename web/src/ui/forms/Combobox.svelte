<script lang="ts">
  import Check from "@lucide/svelte/icons/check";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import Plus from "@lucide/svelte/icons/plus";
  import { Combobox } from "bits-ui";
  import { onMount } from "svelte";
  import { SvelteMap } from "svelte/reactivity";
  import { cn } from "@/ui/cn";
  import Chip from "@/ui/feedback/Chip.svelte";
  import Spinner from "@/ui/feedback/Spinner.svelte";
  import type { ComboboxOption, FieldMessages } from "@/ui/forms/field";
  import FieldFrame from "./FieldFrame.svelte";
  import { boxClass, boxInputClass, inputClass, popupOptionClass } from "./styles";

  /**
   * Choose one value, or several with `multiple`, from a set too long for `ChoiceGroup`,
   * such as a Minecraft version or tags. Typing filters `options`, or with `load` asks the
   * server (debounced, with an abort signal). Chosen values in `multiple` show as removable
   * chips inside the control, and Backspace in the empty input removes the last one.
   * `allowCreate` offers the typed text as a new value, named by `labels.create(text)`.
   *
   * `value` is a string, or a string array with `multiple`; `selected` gives labels for
   * values that `load` has not returned yet. Before hydration, static options render as a
   * native select and async ones as a text input, so the form still submits `name`.
   */
  type Props = Omit<FieldMessages, "labelVisibility"> & {
    name: string;
    options?: ComboboxOption[];
    load?: (query: string, signal: AbortSignal) => Promise<ComboboxOption[]>;
    selected?: ComboboxOption[];
    value?: string | string[];
    multiple?: boolean;
    allowCreate?: boolean;
    placeholder?: string;
    required?: boolean;
    disabled?: boolean;
    debounceMs?: number;
    labels: {
      toggle: string;
      noResults: string;
      loading: string;
      error: string;
      remove: (label: string) => string;
      create?: (text: string) => string;
    };
    onchange?: (value: string | string[]) => void;
    class?: string;
  };
  let {
    name,
    options,
    load,
    selected = [],
    value = $bindable(),
    multiple = false,
    allowCreate = false,
    placeholder,
    required,
    disabled,
    debounceMs = 250,
    labels,
    onchange,
    width = multiple ? "full" : "md",
    ...frame
  }: Props = $props();

  let hydrated = $state(false);
  onMount(() => (hydrated = true));

  let open = $state(false);
  let search = $state("");
  let filtering = $state(false);
  let loaded = $state<ComboboxOption[]>([]);
  let phase = $state<"idle" | "loading" | "failed">("idle");
  // Labels of every option seen, so chips and the input can name values no longer listed.
  const seen = new SvelteMap<string, string>();
  const values = $derived(
    multiple ? (Array.isArray(value) ? value : []) : typeof value === "string" ? [value] : [],
  );
  const labelOf = (item: string) =>
    [...(options ?? []), ...selected].find((option) => option.value === item)?.label ??
    seen.get(item) ??
    item;

  const query = $derived(filtering ? search.trim() : "");
  const shown = $derived(
    load
      ? loaded
      : (options ?? []).filter((option) =>
          option.label.toLowerCase().includes(query.toLowerCase()),
        ),
  );
  const creatable = $derived(
    allowCreate &&
      query !== "" &&
      !shown.some((option) => option.label.toLowerCase() === query.toLowerCase()) &&
      !values.includes(query),
  );

  $effect(() => {
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- bind:open writes it.
    if (!load || !open) return;
    const text = query;
    const request = load;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      phase = "loading";
      request(text, controller.signal)
        .then((items) => {
          if (controller.signal.aborted) return;
          for (const item of items) seen.set(item.value, item.label);
          loaded = items;
          phase = "idle";
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

  function changed(next: string | string[]): void {
    if (creatable) seen.set(query, query);
    value = next;
    search = multiple || typeof next !== "string" ? "" : labelOf(next);
    filtering = false;
    onchange?.(next);
  }
  function remove(item: string): void {
    changed(values.filter((other) => other !== item));
  }
</script>

{#snippet list()}
  <Combobox.Portal>
    <Combobox.Content
      sideOffset={4}
      class="z-dropdown max-h-72 w-(--bits-floating-anchor-width) min-w-field-sm overflow-y-auto rounded-sm border border-line-strong bg-surface-raised p-1 shadow-overlay"
    >
      {#each shown as option (option.value)}
        <Combobox.Item
          value={option.value}
          label={option.label}
          disabled={option.disabled}
          class={cn(
            popupOptionClass,
            "grid-cols-[1fr_auto] data-disabled:cursor-not-allowed data-disabled:text-fg-muted",
          )}
        >
          {#snippet children({ selected: isSelected })}
            <span class="grid min-w-0 gap-0.5">
              <span>{option.label}</span>
              {#if option.description !== undefined}<span class="text-xs text-fg-muted"
                  >{option.description}</span
                >{/if}
            </span>
            {#if isSelected}<Check class="size-4 self-center text-accent" aria-hidden="true" />{/if}
          {/snippet}
        </Combobox.Item>
      {/each}
      {#if creatable && labels.create}
        <Combobox.Item
          value={query}
          label={query}
          class={cn(popupOptionClass, "flex items-center gap-2")}
        >
          <Plus class="size-4 shrink-0 text-fg-muted" aria-hidden="true" />{labels.create(query)}
        </Combobox.Item>
      {/if}
      {#if shown.length === 0 && !creatable}
        <p class="flex items-center gap-2 px-3 py-2.5 text-sm text-fg-muted" role="status">
          {#if phase === "loading"}<Spinner
            />{labels.loading}{:else if phase === "failed"}{labels.error}{:else}{labels.noResults}{/if}
        </p>
      {/if}
    </Combobox.Content>
  </Combobox.Portal>
{/snippet}

<FieldFrame {...frame} {width}>
  {#snippet children(attributes)}
    {#if !hydrated && !load}
      <select
        {...attributes}
        {name}
        {multiple}
        {required}
        {disabled}
        class={cn(inputClass, "w-full", "px-3", multiple ? "min-h-24" : "min-h-control")}
      >
        {#if !multiple && placeholder !== undefined}<option value="">{placeholder}</option>{/if}
        {#each options ?? [] as option (option.value)}<option
            value={option.value}
            selected={values.includes(option.value)}
            disabled={option.disabled}>{option.label}</option
          >{/each}
      </select>
    {:else if !hydrated}
      <input
        {...attributes}
        {name}
        {required}
        {disabled}
        {placeholder}
        value={values.map(labelOf).join(", ")}
        class={cn(inputClass, "min-h-control w-full px-3")}
      />
    {:else if multiple}
      <Combobox.Root
        type="multiple"
        {name}
        {required}
        {disabled}
        bind:open
        value={values}
        onValueChange={changed}
        inputValue={search}
        onOpenChange={(next) => {
          if (!next) filtering = false;
        }}
      >
        <div
          class={cn(boxClass, "flex-wrap items-center gap-1.5 p-1.5")}
          data-disabled={disabled === true ? "" : undefined}
        >
          {#each values as item (item)}
            <Chip removeLabel={labels.remove(labelOf(item))} onRemove={() => remove(item)}
              >{labelOf(item)}</Chip
            >
          {/each}
          <Combobox.Input
            {...attributes}
            {placeholder}
            class={cn(boxInputClass, "min-w-field-xs px-1.5 py-1")}
            oninput={(event) => {
              search = event.currentTarget.value;
              filtering = true;
            }}
            onkeydown={(event) => {
              if (event.key === "Backspace" && search === "" && values.length > 0)
                remove(values[values.length - 1] ?? "");
            }}
          />
          <Combobox.Trigger
            aria-label={labels.toggle}
            class="grid size-control-sm shrink-0 cursor-pointer place-items-center rounded-xs text-fg-muted hover:bg-surface-muted"
            ><ChevronDown class="size-4" aria-hidden="true" /></Combobox.Trigger
          >
        </div>
        {@render list()}
      </Combobox.Root>
    {:else}
      <Combobox.Root
        type="single"
        {name}
        {required}
        {disabled}
        bind:open
        value={values[0] ?? ""}
        onValueChange={changed}
        inputValue={search !== "" || filtering ? search : labelOf(values[0] ?? "")}
        onOpenChange={(next) => {
          if (!next) filtering = false;
        }}
      >
        <div class={boxClass} data-disabled={disabled === true ? "" : undefined}>
          <Combobox.Input
            {...attributes}
            {placeholder}
            class={boxInputClass}
            oninput={(event) => {
              search = event.currentTarget.value;
              filtering = true;
            }}
          />
          <Combobox.Trigger
            aria-label={labels.toggle}
            class="grid w-control shrink-0 cursor-pointer place-items-center text-fg-muted hover:text-fg"
            ><ChevronDown class="size-4" aria-hidden="true" /></Combobox.Trigger
          >
        </div>
        {@render list()}
      </Combobox.Root>
    {/if}
  {/snippet}
</FieldFrame>
