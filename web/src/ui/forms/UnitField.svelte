<script lang="ts">
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import type { FieldMessages } from "@/ui/forms/field";
  import FieldFrame from "./FieldFrame.svelte";
  import { boxClass, boxInputClass } from "./styles";

  type Unit = { id: string; label: string; factor: number };

  /**
   * A quantity typed in a unit the user picks, such as a delay in game ticks, redstone ticks
   * or seconds. `value` is the canonical number (the amount in the unit whose `factor` is 1),
   * or null while the text is empty or not a number. `factor` is how many canonical units one
   * of this unit holds: 2 for redstone ticks and 20 for seconds when the canonical unit is
   * game ticks. The other units' equivalents show beside the hint, such as "= 1.25 s".
   *
   * The form submits the typed number as `name` and the unit id as `unitName`, so a form
   * without JavaScript reads both.
   */
  type Props = Omit<FieldMessages, "labelVisibility"> & {
    name: string;
    unitName: string;
    units: Unit[];
    value?: number | null;
    unit?: string;
    labels: { unit: string };
    required?: boolean;
    disabled?: boolean;
    class?: string;
  };
  let {
    name,
    unitName,
    units,
    value = $bindable(null),
    unit = $bindable(units[0]?.id),
    labels,
    required,
    disabled,
    width = "md",
    ...frame
  }: Props = $props();
  const conversionId = $props.id();
  const current = $derived(units.find((item) => item.id === unit) ?? units[0]);
  // Rounding keeps 0.1 * 20 from showing as 2.0000000000000004.
  const format = (amount: number) => String(Number(amount.toFixed(3)));
  const shown = (amount: number | null) =>
    amount === null || !current ? "" : format(amount / current.factor);
  let text = $state(shown(value));

  // Follow a value set from outside, without rewriting what the user is typing.
  $effect(() => {
    const next = value;
    if (parse(text) !== next) text = shown(next);
  });

  function parse(raw: string): number | null {
    const trimmed = raw.trim().replace(",", ".");
    const amount = Number(trimmed);
    return trimmed === "" || !Number.isFinite(amount) || !current
      ? null
      : Number((amount * current.factor).toFixed(6));
  }
  const conversions = $derived.by(() => {
    const amount = value;
    if (amount === null) return [];
    return units
      .filter((item) => item.id !== current?.id)
      .map((item) => `${format(amount / item.factor)} ${item.label}`);
  });
</script>

{#snippet converted()}<span id={conversionId} class="text-xs text-fg-muted tabular-nums"
    >= {conversions.join(" · ")}</span
  >{/snippet}

<FieldFrame
  {...frame}
  {width}
  aria-describedby={conversions.length > 0 ? conversionId : undefined}
  aside={conversions.length > 0 ? converted : undefined}
>
  {#snippet children(attributes)}
    <div class={boxClass}>
      <input
        {...attributes}
        {name}
        {required}
        {disabled}
        inputmode="decimal"
        autocomplete="off"
        bind:value={text}
        oninput={() => (value = parse(text))}
        class={boxInputClass}
      />
      <span class="relative flex shrink-0 border-s border-line-strong">
        <select
          name={unitName}
          aria-label={labels.unit}
          {disabled}
          bind:value={unit}
          onchange={() => (text = shown(value))}
          class="cursor-pointer appearance-none bg-transparent ps-3 pe-8 text-fg focus-visible:outline-none disabled:cursor-not-allowed"
        >
          {#each units as option (option.id)}<option value={option.id}>{option.label}</option
            >{/each}
        </select>
        <ChevronDown
          class="pointer-events-none absolute end-2.5 top-1/2 size-4 -translate-y-1/2 text-fg-muted"
          aria-hidden="true"
        />
      </span>
    </div>
  {/snippet}
</FieldFrame>
