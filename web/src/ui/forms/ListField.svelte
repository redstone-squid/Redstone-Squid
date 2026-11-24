<script lang="ts" generics="T">
  import ArrowDown from "@lucide/svelte/icons/arrow-down";
  import ArrowUp from "@lucide/svelte/icons/arrow-up";
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import Plus from "@lucide/svelte/icons/plus";
  import Trash from "@lucide/svelte/icons/trash-2";
  import { tick, type Snippet } from "svelte";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";
  import { fieldErrorClass, fieldMessageClass, labelClass } from "./styles";

  /**
   * Repeatable rows of fields, such as profile links or credits. Each row is a fieldset named
   * `labels.rowLabel(n)` ("Link 2"), rendered by `row(item, index, rowLabel)`; bind the
   * row's fields to `items[index]`. Rows can be added up to `max`, removed down to `min`, and
   * with `reorderable` moved up and down.
   *
   * Focus follows the change: to the new row's first field after adding, to the next row (or
   * the add button) after removing, and back to the moved button after a move. Removal is
   * announced through `labels.removed(n)`. Needs JavaScript; without it, render blank rows.
   */
  type Props = {
    label: string;
    hint?: string;
    error?: string | null;
    items: T[];
    row: Snippet<[T, number, string]>;
    newItem: () => T;
    min?: number;
    max?: number;
    reorderable?: boolean;
    labels: {
      add: string;
      remove: (rowLabel: string) => string;
      moveUp: (rowLabel: string) => string;
      moveDown: (rowLabel: string) => string;
      rowLabel: (n: number) => string;
      removed: (n: number) => string;
    };
    class?: string;
  };
  let {
    label,
    hint,
    error,
    items = $bindable(),
    row,
    newItem,
    min = 0,
    max = Infinity,
    reorderable = false,
    labels,
    class: className,
  }: Props = $props();
  const id = $props.id();
  let nextKey = 0;
  // Stable keys keep each row's DOM (and its focus and typed text) when rows move.
  let keys = $state(items.map(() => nextKey++));
  $effect.pre(() => {
    if (keys.length !== items.length) keys = items.map(() => nextKey++);
  });
  let rows: HTMLElement | undefined = $state();
  let addRow: HTMLElement | undefined = $state();
  let announcement = $state("");
  const descriptions = $derived(
    [hint !== undefined && `${id}-hint`, error != null && error !== "" && `${id}-error`]
      .filter((part) => part !== false)
      .join(" "),
  );

  const rowAt = (index: number) => rows?.querySelectorAll<HTMLElement>(":scope > li")[index];
  const firstField = (index: number) =>
    rowAt(index)?.querySelector<HTMLElement>("fieldset input, fieldset select, fieldset textarea");

  async function add(): Promise<void> {
    items = [...items, newItem()];
    keys = [...keys, nextKey++];
    announcement = "";
    await tick();
    firstField(items.length - 1)?.focus();
  }
  async function remove(index: number): Promise<void> {
    items = items.filter((_, other) => other !== index);
    keys = keys.filter((_, other) => other !== index);
    announcement = labels.removed(index + 1);
    await tick();
    const next = firstField(Math.min(index, items.length - 1));
    if (next) next.focus();
    else addRow?.querySelector("button")?.focus();
  }
  async function move(index: number, by: -1 | 1): Promise<void> {
    const target = index + by;
    const swap = <V,>(list: V[]) => {
      const copy = [...list];
      [copy[index], copy[target]] = [copy[target] as V, copy[index] as V];
      return copy;
    };
    items = swap(items);
    keys = swap(keys);
    await tick();
    // The pressed button may now be disabled at the edge, so fall back to its sibling.
    const buttons = rowAt(target)?.querySelectorAll<HTMLButtonElement>("[data-move]");
    const pressed = buttons?.[by === -1 ? 0 : 1];
    (pressed && !pressed.disabled ? pressed : buttons?.[by === -1 ? 1 : 0])?.focus();
  }
</script>

<div
  role="group"
  aria-labelledby={`${id}-label`}
  aria-describedby={descriptions === "" ? undefined : descriptions}
  class={cn("grid min-w-0 gap-3 text-sm", className)}
>
  <p id={`${id}-label`} class={labelClass}>{label}</p>
  {#if hint !== undefined}<p id={`${id}-hint`} class={fieldMessageClass}>{hint}</p>{/if}
  {#if items.length > 0}
    <ol bind:this={rows} class="grid list-none gap-3">
      {#each items as item, index (keys[index])}
        {@const rowLabel = labels.rowLabel(index + 1)}
        <li
          class="grid min-w-0 gap-3 rounded-md border border-line bg-surface p-4 sm:grid-cols-[1fr_auto]"
        >
          <fieldset class="grid min-w-0 gap-4">
            <legend class="mb-3 text-xs font-semibold text-fg-muted">{rowLabel}</legend>
            {@render row(item, index, rowLabel)}
          </fieldset>
          <div class="flex items-start gap-1 max-sm:justify-end">
            {#if reorderable}
              <Button
                size="sm"
                variant="quiet"
                iconOnly
                icon={ArrowUp}
                label={labels.moveUp(rowLabel)}
                disabled={index === 0}
                data-move=""
                onclick={() => move(index, -1)}
              />
              <Button
                size="sm"
                variant="quiet"
                iconOnly
                icon={ArrowDown}
                label={labels.moveDown(rowLabel)}
                disabled={index === items.length - 1}
                data-move=""
                onclick={() => move(index, 1)}
              />
            {/if}
            <Button
              size="sm"
              variant="quiet"
              iconOnly
              icon={Trash}
              label={labels.remove(rowLabel)}
              disabled={items.length <= min}
              onclick={() => remove(index)}
            />
          </div>
        </li>
      {/each}
    </ol>
  {/if}
  {#if error != null && error !== ""}<p id={`${id}-error`} class={fieldErrorClass}>
      <CircleAlert class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />{error}
    </p>{/if}
  <div bind:this={addRow}>
    <Button size="sm" icon={Plus} disabled={items.length >= max} onclick={add}>{labels.add}</Button>
  </div>
  <span class="sr-only" role="status">{announcement}</span>
</div>
